#!/usr/bin/env python3
"""Trusted-main live runner for the #314 dispatch/recovery Issue #221 trio.

Every phase consumes one fixed #310 slot through the #312 production preflight.
PR validation imports the wrappers but never enters these live phases.  The live
workflow invokes each phase in a fresh Python process; the concurrent scenario
uses two independent repository copies so their remote-Store tracking refs and
runtime objects are independent while both converge on the same protected ref.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from operator_external_create_attempt import find_external_create_attempt
from operator_store_model import operation_events, operation_id_for, reservation_path
from operator_vertical_controller import select_vertical_action
from operator_vertical_recovery import plan_vertical_takeover
from operator_vertical_store import vertical_projection
from v03_effect_safety_live_ledger import ReleaseAuthority
from v03_effect_safety_live_ledger_authority_set import (
    ReleaseAuthoritySet,
    authority_set_document,
    evaluate_issue_221_authority_set,
)
from v03_live_evidence_provenance import write_live_evidence_envelope
from v03_scenario_fixture_pool import inventory_document
from v03_scenario_runtime_driver import ADAPTER_ID, assemble_scenario_live_preflight

UNKNOWN = "unknown-takeover"
CONCURRENT = "concurrent-resume"
PREAUTH = "reservation-committed-pre-authorization-crash-recovery"
IDEMPOTENCY = {
    UNKNOWN: "v03-release-fi-unknown-takeover",
    CONCURRENT: "v03-release-fi-concurrent-resume",
    PREAUTH: "v03-release-fi-preauth-crash",
}
PHASE_SCENARIO = {
    "unknown-inject": UNKNOWN,
    "unknown-finalize": UNKNOWN,
    "preauth-crash": PREAUTH,
    "preauth-recover": PREAUTH,
    "preauth-finalize": PREAUTH,
    "concurrent-setup": CONCURRENT,
    "concurrent-racer": CONCURRENT,
    "concurrent-finalize": CONCURRENT,
}


class V03DispatchRecoveryLiveError(RuntimeError):
    pass


class InjectedPreAuthorizationCrash(BaseException):
    pass


def require(value: Any, message: str) -> None:
    if not value:
        raise V03DispatchRecoveryLiveError(message)


def _checkout_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise V03DispatchRecoveryLiveError("cannot resolve exact live checkout HEAD")
    return completed.stdout.strip()


def _shared_dir() -> Path:
    return Path(os.environ.get("AI_SDLC_SHARED_EVIDENCE_DIR") or "evidence/v03-dispatch-recovery").resolve()


def _path(scenario: str, label: str) -> Path:
    return _shared_dir() / f"{scenario}-{label}.json"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise V03DispatchRecoveryLiveError(f"required phase evidence is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise V03DispatchRecoveryLiveError(f"required phase evidence is not an object: {path}")
    return value


def _wait(path: Path, timeout_seconds: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.2)
    raise V03DispatchRecoveryLiveError(f"timed out waiting for coordination file: {path}")


def _preflight(scenario: str):
    return assemble_scenario_live_preflight(
        scenario=scenario,
        env=os.environ,
        checkout_sha=_checkout_sha(),
    )


def _base(preflight):
    executor = preflight.composition.bundle.executor
    base = getattr(executor, "base", None)
    if base is None:
        raise V03DispatchRecoveryLiveError("production recovering executor lacks exact base executor")
    return base


def _trusted_context(preflight):
    return preflight.composition.bundle.write_bundle.read_bundle.trusted_context_provider.for_request(
        {
            "repository": preflight.execution.repository,
            "feature_id": preflight.slot.feature_id,
        }
    )


def _manifest(preflight) -> dict[str, Any]:
    manifest = preflight.composition.feature_event_gateway.read_feature(
        repository=preflight.execution.repository,
        feature_id=preflight.slot.feature_id,
        target_ref=preflight.slot.target_ref,
    )
    if not isinstance(manifest, dict) or int(manifest.get("revision", -1)) < 0:
        raise V03DispatchRecoveryLiveError("scenario fixture Manifest is invalid")
    return manifest


def _operation_id(preflight, scenario: str) -> str:
    return operation_id_for(
        preflight.execution.repository,
        preflight.slot.feature_id,
        IDEMPOTENCY[scenario],
    )


def _events(preflight, operation_id: str, event_type: str | None = None, generation: int | None = None):
    rows = operation_events(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id)
    if event_type is not None:
        rows = [row for row in rows if row.get("event_type") == event_type]
    if generation is not None:
        rows = [row for row in rows if int(row.get("operation_generation", -1)) == generation]
    return rows


def _require_unused_operation(preflight, operation_id: str) -> None:
    if _events(preflight, operation_id):
        raise V03DispatchRecoveryLiveError(
            "fixed release scenario Operation already exists; contaminated slot may not be reused"
        )


def _start_request(preflight, scenario: str, revision: int) -> dict[str, Any]:
    return {
        "idempotency_key": IDEMPOTENCY[scenario],
        "client_identity": {"adapter_id": ADAPTER_ID},
        "target": {
            "repository": preflight.execution.repository,
            "feature_id": preflight.slot.feature_id,
        },
        "context": {"expected_feature_revision": revision},
    }


def _start_only(preflight, scenario: str) -> tuple[str, int]:
    operation_id = _operation_id(preflight, scenario)
    _require_unused_operation(preflight, operation_id)
    revision = int(_manifest(preflight)["revision"])
    outer = preflight.composition.bundle.backends["operation.start"]
    raw = getattr(outer, "delegate", None)
    if raw is None or not callable(getattr(raw, "invoke", None)):
        raise V03DispatchRecoveryLiveError(
            "scenario setup cannot reach the scoped raw operation.start beneath auto-advance"
        )
    started = raw.invoke(_start_request(preflight, scenario, revision), _trusted_context(preflight))
    if not isinstance(started, dict) or started.get("operation_id") != operation_id:
        raise V03DispatchRecoveryLiveError("raw scenario operation.start returned wrong Operation identity")
    projection = vertical_projection(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id)
    if projection.get("status") != "RUNNING" or int(projection.get("generation", -1)) != 0:
        raise V03DispatchRecoveryLiveError("raw scenario Operation did not stop at fresh RUNNING/G0")
    return operation_id, revision


def _select_dispatch(preflight, operation_id: str):
    feature, manifest = preflight.composition.feature_truth_gateway.read_feature(operation_id=operation_id)
    action = select_vertical_action(
        feature=feature,
        manifest=manifest,
        occurred_at=preflight.composition.bundle.runtime.clock(),
    )
    if action.kind != "dispatch" or not action.candidate_head_sha:
        raise V03DispatchRecoveryLiveError("fixed #310 slot did not resolve to exact Reviewer dispatch")
    if action.candidate_head_sha != preflight.fixture_candidate.candidate_head_sha:
        raise V03DispatchRecoveryLiveError("selected action candidate differs from fresh preflight PR/head")
    return feature, action


def _binding_from_claim(preflight, operation_id: str, generation: int = 0) -> dict[str, Any]:
    claims = _events(preflight, operation_id, "dispatch.claimed", generation)
    if len(claims) != 1:
        raise V03DispatchRecoveryLiveError("scenario requires exactly one durable dispatch claim")
    payload = dict(claims[0].get("payload") or {})
    semantic = str(payload.get("semantic_effect_key") or "")
    external = str(payload.get("external_dispatch_key") or "")
    claim_id = str(payload.get("claim_id") or "")
    if not semantic or not external or not claim_id:
        raise V03DispatchRecoveryLiveError("durable dispatch claim lacks exact effect identity")
    return {
        "semantic_effect_key": semantic,
        "external_dispatch_key": external,
        "claim_id": claim_id,
        "sequence": int(claims[0]["sequence"]),
    }


def _authorization_rows(preflight, operation_id: str, generation: int = 0):
    return _events(preflight, operation_id, "dispatch.launch.authorized", generation)


def _lookup_rows(preflight, operation_id: str, generation: int = 0):
    return _events(preflight, operation_id, "dispatch.launch.lookup-recorded", generation)


def _persist_rows(preflight, operation_id: str):
    return [row for row in _events(preflight, operation_id) if str(row.get("event_type") or "").startswith("persist.")]


def _generic_record(
    *,
    scenario: str,
    operation_id: str,
    generation: int,
    semantic_effect_key: str,
    external_dispatch_key: str,
    candidate_head_sha: str,
    feature_revision_before: int,
    runtime_lookup_state: str,
    runtime_receipt_identity: Any,
    measurements: dict[str, int],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "schema_version": "ai-sdlc.v03-effect-safety-live-scenario/v1",
        "status": "PASS",
        "completed_issue_221_scenarios": [scenario],
        "operation_id": operation_id,
        "operation_generation": generation,
        "semantic_effect_key": semantic_effect_key,
        "external_dispatch_key": external_dispatch_key,
        "candidate_head_sha": candidate_head_sha,
        "feature_revision_before": feature_revision_before,
        "runtime_lookup_state": runtime_lookup_state,
        "runtime_receipt_identity": runtime_receipt_identity,
        "measurements": measurements,
        "overall_issue_221_pass": False,
    }
    if extra:
        record.update(extra)
    return record


def _seal(preflight, scenario: str, record: dict[str, Any]) -> dict[str, Any]:
    evidence = _path(scenario, "evidence")
    provenance_path = _path(scenario, "provenance")
    authority_path = _path(scenario, "authority")
    authority_set_path = _path(scenario, "authority-set")
    ledger_path = _path(scenario, "ledger-partial")
    _write_json(evidence, record)
    authority_doc, provenance = write_live_evidence_envelope(
        preflight=preflight,
        evidence_path=evidence,
        provenance_path=provenance_path,
        authority_path=authority_path,
        github_workflow_run_id=os.environ.get("GITHUB_RUN_ID"),
        workflow_sha=str(os.environ.get("GITHUB_SHA") or ""),
        record_id=f"issue-221:{scenario}:{os.environ.get('GITHUB_RUN_ID', '')}",
    )
    common = ReleaseAuthority.from_document(authority_doc)
    set_doc = authority_set_document(
        authority=common,
        fixture_pool_inventory_digest=inventory_document()["inventory_digest"],
    )
    authority_set = ReleaseAuthoritySet.from_document(set_doc)
    raw = evidence.read_bytes()
    ledger = evaluate_issue_221_authority_set(
        authority_set=authority_set,
        evidence=[(raw, record, provenance)],
    )
    if ledger.get("status") != "PENDING" or ledger.get("satisfied_scenarios") != [scenario]:
        raise V03DispatchRecoveryLiveError("single-scenario live evidence did not enter closed authority-set ledger exactly once")
    _write_json(authority_set_path, set_doc)
    _write_json(ledger_path, ledger)
    return ledger


class UnknownAfterProductionLaunchGateway:
    """Permit one exact production launch, then intentionally suppress launch certainty."""

    verification_only = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.launch_calls = 0
        self.lookup_calls = 0
        self.dispatch: dict[str, Any] | None = None
        self.actual_receipt: dict[str, Any] | None = None

    def launch(self, *, dispatch):
        self.launch_calls += 1
        if self.launch_calls != 1:
            raise V03DispatchRecoveryLiveError("UNKNOWN injector attempted multiple production launches")
        receipt = self.delegate.launch(dispatch=dispatch)
        if (
            not isinstance(receipt, dict)
            or receipt.get("lookup_state") != "LAUNCHED"
            or not receipt.get("receipt_id")
        ):
            raise V03DispatchRecoveryLiveError("UNKNOWN injector did not obtain one exact production launch receipt")
        self.dispatch = dict(dispatch)
        self.actual_receipt = dict(receipt)
        return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls += 1
        raise V03DispatchRecoveryLiveError("UNKNOWN injector unexpectedly entered fallback lookup")


class NoExternalAccessGateway:
    verification_only = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.launch_calls = 0
        self.lookup_calls = 0

    def launch(self, *, dispatch):
        self.launch_calls += 1
        raise V03DispatchRecoveryLiveError("stable recovery attempted an external launch")

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls += 1
        raise V03DispatchRecoveryLiveError("stable recovery attempted an external lookup")


class CrashAfterDurableReservationRuntime:
    """Delegate exact production Store CAS, then terminate immediately after first reservation."""

    verification_only = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.backend = delegate.backend
        self.clock = delegate.clock
        self.injected = False
        self.reservation: dict[str, str] | None = None

    def protected_receipt(self):
        return self.delegate.protected_receipt()

    def commit_replanned(self, planner, *, max_attempts: int = 4):
        result = self.delegate.commit_replanned(planner, max_attempts=max_attempts)
        payload = getattr(result, "result", None)
        if (
            not self.injected
            and isinstance(payload, dict)
            and payload.get("semantic_effect_key")
            and payload.get("external_dispatch_key")
            and not payload.get("claim_id")
            and payload.get("status") != "BLOCKED"
        ):
            self.injected = True
            self.reservation = {
                "semantic_effect_key": str(payload["semantic_effect_key"]),
                "external_dispatch_key": str(payload["external_dispatch_key"]),
            }
            raise InjectedPreAuthorizationCrash()
        return result


def run_unknown_inject() -> None:
    preflight = _preflight(UNKNOWN)
    operation_id, revision = _start_only(preflight, UNKNOWN)
    feature, action = _select_dispatch(preflight, operation_id)
    base = _base(preflight)
    production_gateway = base.dispatch_gateway
    injector = UnknownAfterProductionLaunchGateway(production_gateway)
    base.dispatch_gateway = injector
    result = base.advance_action(operation_id=operation_id, action=action)
    require(result.get("status") == "BLOCKED", "UNKNOWN injection did not durably fail closed")
    require(injector.launch_calls == 1 and injector.lookup_calls == 0, "UNKNOWN injection external call count drifted")
    binding = _binding_from_claim(preflight, operation_id, 0)
    auth = _authorization_rows(preflight, operation_id, 0)
    lookup = _lookup_rows(preflight, operation_id, 0)
    require(len(auth) == 1, "UNKNOWN injection lacks exactly one durable launch authorization")
    require(len(lookup) == 1, "UNKNOWN injection lacks exactly one durable lookup fact")
    lp = lookup[0].get("payload") or {}
    require(lp.get("external_dispatch_key") == binding["external_dispatch_key"], "UNKNOWN lookup key drifted")
    require(lp.get("lookup_state") == "UNKNOWN" and lp.get("receipt_id") is None, "UNKNOWN lookup was not durably unknown")
    require(injector.dispatch is not None and injector.actual_receipt is not None, "UNKNOWN injector lost real launch binding")
    require(injector.dispatch["semantic_effect_key"] == binding["semantic_effect_key"], "UNKNOWN real launch semantic key drifted")
    require(injector.dispatch["external_dispatch_key"] == binding["external_dispatch_key"], "UNKNOWN real launch external key drifted")
    attempt = find_external_create_attempt(
        preflight.composition.bundle.runtime.backend.read_snapshot(),
        external_dispatch_key=binding["external_dispatch_key"],
    )
    require(attempt is not None, "UNKNOWN real launch lacks durable one-shot external-create attempt")
    require(not _persist_rows(preflight, operation_id), "UNKNOWN dispatch unexpectedly created Feature Persist authority")
    _write_json(_path(UNKNOWN, "inject"), {
        "schema_version": "ai-sdlc.v03-dispatch-recovery-phase/v1",
        "scenario": UNKNOWN,
        "phase": "inject",
        "trusted_main_head_sha": preflight.execution.installation_commit_sha,
        "operation_id": operation_id,
        "operation_generation": 0,
        "semantic_effect_key": binding["semantic_effect_key"],
        "external_dispatch_key": binding["external_dispatch_key"],
        "candidate_head_sha": action.candidate_head_sha,
        "feature_revision_before": revision,
        "actual_runtime_receipt_identity": injector.actual_receipt["receipt_id"],
        "durable_lookup_state": "UNKNOWN",
        "durable_lookup_receipt_identity": None,
        "external_launch_count": 1,
        "release_evidence": False,
    })


def run_unknown_finalize() -> None:
    phase = _read_json(_path(UNKNOWN, "inject"))
    preflight = _preflight(UNKNOWN)
    require(phase.get("trusted_main_head_sha") == preflight.execution.installation_commit_sha, "UNKNOWN phases crossed trusted-main heads")
    operation_id = _operation_id(preflight, UNKNOWN)
    require(phase.get("operation_id") == operation_id, "UNKNOWN phase Operation identity drifted")
    before = _events(preflight, operation_id)
    base = _base(preflight)
    pre = vertical_projection(base.runtime.backend.read_snapshot(), operation_id)
    require(pre.get("status") == "BLOCKED" and int(pre.get("generation", -1)) == 0, "UNKNOWN takeover did not begin from BLOCKED/G0")
    require(phase["external_dispatch_key"] in pre.get("unresolved_unknown", []), "UNKNOWN external key is not durably unresolved")
    base.runtime.commit_replanned(
        lambda snapshot: plan_vertical_takeover(
            snapshot,
            operation_id=operation_id,
            occurred_at=base.runtime.clock(),
            trusted_context_digest=base.config.trusted_context_digest,
        )
    )
    after_takeover = vertical_projection(base.runtime.backend.read_snapshot(), operation_id)
    require(after_takeover.get("status") == "BLOCKED" and int(after_takeover.get("generation", -1)) == 1, "UNKNOWN takeover did not preserve BLOCKED into G1")
    require(phase["external_dispatch_key"] in after_takeover.get("unresolved_unknown", []), "UNKNOWN key was cleared by takeover")
    fence = NoExternalAccessGateway(base.dispatch_gateway)
    base.dispatch_gateway = fence
    result = preflight.composition.bundle.executor.advance_until_stop(operation_id=operation_id)
    require(result.get("status") == "BLOCKED", "fresh G1 escaped UNKNOWN BLOCKED stable stop")
    require(fence.launch_calls == 0 and fence.lookup_calls == 0, "fresh G1 touched external runtime under UNKNOWN")
    require(not _events(preflight, operation_id, "dispatch.claimed", 1), "G1 created a new dispatch claim under UNKNOWN")
    require(not _authorization_rows(preflight, operation_id, 1), "G1 created a new launch authorization under UNKNOWN")
    require(not _lookup_rows(preflight, operation_id, 1), "G1 created new launch lookup evidence under UNKNOWN")
    require(not _persist_rows(preflight, operation_id), "UNKNOWN takeover created Feature Persist authority")
    final = _generic_record(
        scenario=UNKNOWN,
        operation_id=operation_id,
        generation=1,
        semantic_effect_key=str(phase["semantic_effect_key"]),
        external_dispatch_key=str(phase["external_dispatch_key"]),
        candidate_head_sha=str(phase["candidate_head_sha"]),
        feature_revision_before=int(phase["feature_revision_before"]),
        runtime_lookup_state="UNKNOWN",
        runtime_receipt_identity=phase["actual_runtime_receipt_identity"],
        measurements={
            "duplicate_external_effect_count": 0,
            "speculative_retry_under_unknown_count": 0,
        },
        extra={
            "external_runtime_execution_count": 1,
            "g1_external_launch_count": 0,
            "g1_external_lookup_count": 0,
            "g1_new_claim_count": 0,
            "g1_new_authorization_count": 0,
            "durable_event_count_before_takeover": len(before),
        },
    )
    _seal(preflight, UNKNOWN, final)


def run_preauth_crash() -> None:
    preflight = _preflight(PREAUTH)
    operation_id, revision = _start_only(preflight, PREAUTH)
    feature, action = _select_dispatch(preflight, operation_id)
    base = _base(preflight)
    original_runtime = base.runtime
    crash = CrashAfterDurableReservationRuntime(original_runtime)
    base.runtime = crash
    try:
        base.advance_action(operation_id=operation_id, action=action)
    except InjectedPreAuthorizationCrash:
        pass
    else:
        raise V03DispatchRecoveryLiveError("pre-authorization crash was not injected after reservation")
    finally:
        base.runtime = original_runtime
    require(crash.injected and crash.reservation is not None, "pre-authorization crash lost reservation identity")
    binding = crash.reservation
    snapshot = original_runtime.backend.read_snapshot()
    reservation = snapshot.get(reservation_path(binding["semantic_effect_key"]))
    require(isinstance(reservation, dict), "pre-authorization crash did not leave one durable semantic reservation")
    require(reservation.get("external_dispatch_key") == binding["external_dispatch_key"], "pre-authorization crash reservation external key drifted")
    require(not _events(preflight, operation_id, "dispatch.claimed", 0), "pre-authorization crash occurred after dispatch claim")
    require(not _authorization_rows(preflight, operation_id, 0), "pre-authorization crash occurred after launch authorization")
    require(not _lookup_rows(preflight, operation_id, 0), "pre-authorization crash recorded a launch lookup")
    require(find_external_create_attempt(snapshot, external_dispatch_key=binding["external_dispatch_key"]) is None, "pre-authorization crash crossed external-create attempt boundary")
    require(not _persist_rows(preflight, operation_id), "pre-authorization crash created Feature Persist authority")
    _write_json(_path(PREAUTH, "crash"), {
        "schema_version": "ai-sdlc.v03-dispatch-recovery-phase/v1",
        "scenario": PREAUTH,
        "phase": "crash-after-reservation",
        "trusted_main_head_sha": preflight.execution.installation_commit_sha,
        "operation_id": operation_id,
        "operation_generation": 0,
        "semantic_effect_key": binding["semantic_effect_key"],
        "external_dispatch_key": binding["external_dispatch_key"],
        "candidate_head_sha": action.candidate_head_sha,
        "feature_revision_before": revision,
        "dispatch_claim_count": 0,
        "launch_authorization_count": 0,
        "external_create_attempt_count": 0,
        "release_evidence": False,
    })


def run_preauth_recover() -> None:
    phase = _read_json(_path(PREAUTH, "crash"))
    preflight = _preflight(PREAUTH)
    require(phase.get("trusted_main_head_sha") == preflight.execution.installation_commit_sha, "preauth phases crossed trusted-main heads")
    operation_id = _operation_id(preflight, PREAUTH)
    require(phase.get("operation_id") == operation_id, "preauth recovery Operation identity drifted")
    result = preflight.composition.bundle.executor.advance_until_stop(operation_id=operation_id)
    require(result.get("status") == "WAITING_EXTERNAL", "fresh preauth recovery did not reach WAITING_EXTERNAL")
    binding = _binding_from_claim(preflight, operation_id, 0)
    require(binding["semantic_effect_key"] == phase["semantic_effect_key"], "fresh preauth recovery changed semantic reservation")
    require(binding["external_dispatch_key"] == phase["external_dispatch_key"], "fresh preauth recovery changed external dispatch key")
    auth = _authorization_rows(preflight, operation_id, 0)
    lookup = _lookup_rows(preflight, operation_id, 0)
    require(len(auth) == 1 and len(lookup) == 1, "fresh preauth recovery lacks exact authorization/receipt")
    lp = lookup[0].get("payload") or {}
    require(lp.get("lookup_state") == "LAUNCHED" and lp.get("receipt_id"), "fresh preauth recovery did not prove one launched runtime receipt")
    snapshot = preflight.composition.bundle.runtime.backend.read_snapshot()
    reservation = snapshot.get(reservation_path(binding["semantic_effect_key"]))
    require(isinstance(reservation, dict), "fresh preauth recovery lost original reservation")
    attempt = find_external_create_attempt(snapshot, external_dispatch_key=binding["external_dispatch_key"])
    require(attempt is not None, "fresh preauth recovery lacks one durable external-create attempt")
    require(not _persist_rows(preflight, operation_id), "preauth recovery unexpectedly created Feature Persist authority")
    _write_json(_path(PREAUTH, "recover"), {
        "schema_version": "ai-sdlc.v03-dispatch-recovery-phase/v1",
        "scenario": PREAUTH,
        "phase": "fresh-recovery-launched",
        "trusted_main_head_sha": preflight.execution.installation_commit_sha,
        "operation_id": operation_id,
        "semantic_effect_key": binding["semantic_effect_key"],
        "external_dispatch_key": binding["external_dispatch_key"],
        "candidate_head_sha": phase["candidate_head_sha"],
        "feature_revision_before": phase["feature_revision_before"],
        "runtime_receipt_identity": lp["receipt_id"],
        "dispatch_claim_count": 1,
        "launch_authorization_count": 1,
        "launch_lookup_count": 1,
        "external_create_attempt_count": 1,
        "release_evidence": False,
    })


def run_preauth_finalize() -> None:
    crash = _read_json(_path(PREAUTH, "crash"))
    recovery = _read_json(_path(PREAUTH, "recover"))
    preflight = _preflight(PREAUTH)
    operation_id = _operation_id(preflight, PREAUTH)
    require(crash["operation_id"] == recovery["operation_id"] == operation_id, "preauth phase Operation binding drifted")
    require(crash["semantic_effect_key"] == recovery["semantic_effect_key"], "preauth phase semantic key drifted")
    require(crash["external_dispatch_key"] == recovery["external_dispatch_key"], "preauth phase external key drifted")
    base = _base(preflight)
    before_events = _events(preflight, operation_id)
    fence = NoExternalAccessGateway(base.dispatch_gateway)
    base.dispatch_gateway = fence
    result = preflight.composition.bundle.executor.advance_until_stop(operation_id=operation_id)
    require(result.get("status") == "WAITING_EXTERNAL", "second fresh preauth recovery changed stable stop")
    require(fence.launch_calls == 0 and fence.lookup_calls == 0, "second fresh preauth recovery touched external runtime")
    after_events = _events(preflight, operation_id)
    require(len(after_events) == len(before_events), "second fresh preauth recovery mutated durable Operation")
    require(len(_events(preflight, operation_id, "dispatch.claimed", 0)) == 1, "preauth final state has duplicate claims")
    require(len(_authorization_rows(preflight, operation_id, 0)) == 1, "preauth final state has duplicate authorizations")
    require(len(_lookup_rows(preflight, operation_id, 0)) == 1, "preauth final state has duplicate launch receipts")
    require(not _persist_rows(preflight, operation_id), "preauth final state gained Feature Persist authority")
    final = _generic_record(
        scenario=PREAUTH,
        operation_id=operation_id,
        generation=0,
        semantic_effect_key=recovery["semantic_effect_key"],
        external_dispatch_key=recovery["external_dispatch_key"],
        candidate_head_sha=recovery["candidate_head_sha"],
        feature_revision_before=int(recovery["feature_revision_before"]),
        runtime_lookup_state="LAUNCHED",
        runtime_receipt_identity=recovery["runtime_receipt_identity"],
        measurements={
            "duplicate_external_effect_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "external_runtime_execution_count": 1,
            "reservation_count": 1,
            "dispatch_claim_count": 1,
            "launch_authorization_count": 1,
            "launch_receipt_count": 1,
            "second_fresh_process_external_access_count": 0,
        },
    )
    _seal(preflight, PREAUTH, final)


def run_concurrent_setup() -> None:
    preflight = _preflight(CONCURRENT)
    operation_id, revision = _start_only(preflight, CONCURRENT)
    _write_json(_path(CONCURRENT, "setup"), {
        "schema_version": "ai-sdlc.v03-dispatch-recovery-phase/v1",
        "scenario": CONCURRENT,
        "phase": "setup",
        "trusted_main_head_sha": preflight.execution.installation_commit_sha,
        "operation_id": operation_id,
        "feature_revision_before": revision,
        "candidate_head_sha": preflight.fixture_candidate.candidate_head_sha,
        "release_evidence": False,
    })


def run_concurrent_racer(racer: str) -> None:
    if racer not in {"a", "b"}:
        raise V03DispatchRecoveryLiveError("concurrent racer must be a or b")
    setup = _read_json(_path(CONCURRENT, "setup"))
    preflight = _preflight(CONCURRENT)
    require(setup.get("trusted_main_head_sha") == preflight.execution.installation_commit_sha, "concurrent racer crossed trusted-main head")
    operation_id = _operation_id(preflight, CONCURRENT)
    require(setup.get("operation_id") == operation_id, "concurrent racer Operation identity drifted")
    projection = vertical_projection(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id)
    require(projection.get("status") == "RUNNING" and int(projection.get("generation", -1)) == 0, "concurrent racer did not select from common pre-effect RUNNING/G0")
    feature, action = _select_dispatch(preflight, operation_id)
    action_doc = asdict(action)
    ready = _path(CONCURRENT, f"ready-{racer}")
    _write_json(ready, {
        "schema_version": "ai-sdlc.v03-concurrent-racer-ready/v1",
        "racer": racer,
        "operation_id": operation_id,
        "feature_revision": feature.revision,
        "candidate_head_sha": feature.candidate_head_sha,
        "action": action_doc,
        "store_checkout": str(os.environ.get("AI_SDLC_STORE_CHECKOUT") or ""),
    })
    # Both independent processes must have selected the same action before either may execute.
    _wait(_path(CONCURRENT, "ready-a"))
    _wait(_path(CONCURRENT, "ready-b"))
    _wait(_path(CONCURRENT, f"go-{racer}"))
    result = _base(preflight).advance_action(operation_id=operation_id, action=action)
    if result.get("status") != "WAITING_EXTERNAL":
        raise V03DispatchRecoveryLiveError(f"concurrent racer {racer} did not converge to WAITING_EXTERNAL")
    _write_json(_path(CONCURRENT, f"result-{racer}"), {
        "schema_version": "ai-sdlc.v03-concurrent-racer-result/v1",
        "racer": racer,
        "operation_id": operation_id,
        "action": action_doc,
        "status": result.get("status"),
    })


def run_concurrent_finalize() -> None:
    setup = _read_json(_path(CONCURRENT, "setup"))
    ready_a = _read_json(_path(CONCURRENT, "ready-a"))
    ready_b = _read_json(_path(CONCURRENT, "ready-b"))
    result_a = _read_json(_path(CONCURRENT, "result-a"))
    result_b = _read_json(_path(CONCURRENT, "result-b"))
    require(ready_a["action"] == ready_b["action"], "independent concurrent racers did not preselect the same exact action")
    require(result_a["action"] == result_b["action"] == ready_a["action"], "concurrent execution escaped preselected stale action")
    require(result_a["status"] == result_b["status"] == "WAITING_EXTERNAL", "concurrent racers did not converge to same stable stop")
    require(ready_a.get("store_checkout") and ready_b.get("store_checkout") and ready_a["store_checkout"] != ready_b["store_checkout"], "concurrent racers did not use independent Store checkouts")
    preflight = _preflight(CONCURRENT)
    operation_id = _operation_id(preflight, CONCURRENT)
    require(setup["operation_id"] == operation_id, "concurrent finalizer Operation identity drifted")
    binding = _binding_from_claim(preflight, operation_id, 0)
    auth = _authorization_rows(preflight, operation_id, 0)
    lookup = _lookup_rows(preflight, operation_id, 0)
    selected = _events(preflight, operation_id, "loop.step.selected", 0)
    require(len(auth) == 1, "concurrent convergence created duplicate/missing authorization")
    require(len(lookup) == 1, "concurrent convergence created duplicate/missing launch receipt fact")
    require(len(selected) == 1, "concurrent convergence created duplicate selected-step facts")
    lp = lookup[0].get("payload") or {}
    require(lp.get("lookup_state") == "LAUNCHED" and lp.get("receipt_id"), "concurrent convergence lacks exact launched receipt")
    snapshot = preflight.composition.bundle.runtime.backend.read_snapshot()
    reservation = snapshot.get(reservation_path(binding["semantic_effect_key"]))
    require(isinstance(reservation, dict), "concurrent convergence lacks one semantic reservation")
    attempt = find_external_create_attempt(snapshot, external_dispatch_key=binding["external_dispatch_key"])
    require(attempt is not None, "concurrent convergence lacks one durable external-create attempt")
    # Read-only exact lookup over the durable one-shot binding proves exactly one positive gh-aw run.
    observed = _base(preflight).dispatch_gateway.lookup(
        external_dispatch_key=binding["external_dispatch_key"]
    )
    require(isinstance(observed, dict) and observed.get("lookup_state") == "LAUNCHED", "concurrent final lookup did not resolve exactly one external run")
    require(observed.get("receipt_id") == lp.get("receipt_id"), "concurrent final lookup receipt differs from durable receipt")
    require(not _persist_rows(preflight, operation_id), "concurrent dispatch race created Feature Persist authority")
    final = _generic_record(
        scenario=CONCURRENT,
        operation_id=operation_id,
        generation=0,
        semantic_effect_key=binding["semantic_effect_key"],
        external_dispatch_key=binding["external_dispatch_key"],
        candidate_head_sha=str(setup["candidate_head_sha"]),
        feature_revision_before=int(setup["feature_revision_before"]),
        runtime_lookup_state="LAUNCHED",
        runtime_receipt_identity=lp["receipt_id"],
        measurements={
            "duplicate_external_effect_count": 0,
            "duplicate_feature_write_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "external_runtime_execution_count": 1,
            "independent_racer_count": 2,
            "reservation_count": 1,
            "dispatch_claim_count": 1,
            "launch_authorization_count": 1,
            "launch_receipt_count": 1,
            "selected_step_count": 1,
            "read_only_final_runtime_lookup_count": 1,
        },
    )
    _seal(preflight, CONCURRENT, final)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=tuple(PHASE_SCENARIO), required=True)
    parser.add_argument("--racer", choices=("a", "b"))
    args = parser.parse_args()
    if str(os.environ.get("GITHUB_EVENT_NAME") or "") != "workflow_dispatch" or str(os.environ.get("GITHUB_REF") or "") != "refs/heads/main":
        raise V03DispatchRecoveryLiveError("#314 live runner is authorized only by workflow_dispatch on trusted main")
    phase = args.phase
    if phase == "unknown-inject":
        run_unknown_inject()
    elif phase == "unknown-finalize":
        run_unknown_finalize()
    elif phase == "preauth-crash":
        run_preauth_crash()
    elif phase == "preauth-recover":
        run_preauth_recover()
    elif phase == "preauth-finalize":
        run_preauth_finalize()
    elif phase == "concurrent-setup":
        run_concurrent_setup()
    elif phase == "concurrent-racer":
        if args.racer is None:
            raise V03DispatchRecoveryLiveError("concurrent-racer requires --racer")
        run_concurrent_racer(args.racer)
    elif phase == "concurrent-finalize":
        run_concurrent_finalize()
    else:
        raise V03DispatchRecoveryLiveError("unsupported #314 live phase")


if __name__ == "__main__":
    main()
