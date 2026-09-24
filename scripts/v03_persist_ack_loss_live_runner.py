#!/usr/bin/env python3
"""Two-process trusted-main Persist ACK-loss runner for v0.3 Issue #221.

This scenario deliberately continues the exact durable Operation produced by the
reviewed live lost-ACK G0 -> G1 takeover phase. It never creates a second
same-semantic Reviewer Operation. Phase 1 waits for the already-authorized first
Reviewer completion, computes the exact translated Feature Event through the
production result source/callback bindings without mutation, then wraps only the
production Persist gateway and crashes after the authoritative Event receipt but
before local persist.confirmed. Phase 2 is a fresh process and allows exact Event
lookup only; any attempted second Feature Event write fails closed.

Only after phase 2 confirms the exact Event is the preceding lost-ACK scenario
end-to-end complete: launch ACK loss + G1 same-key adoption + exact first-attempt
Worker result correlation + at-most-once Feature Persist.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from operator_store_model import digest_json, operation_events, operation_id_for, reservation_path
from operator_vertical import (
    TrustedDispatchContext,
    VERTICAL_PROFILE,
    VerticalInvariantError,
    translate_result,
    validate_worker_result,
)
from operator_vertical_gh_aw_collector import _build_receipts, _current_launch_binding, _validate_run
from operator_vertical_recovery import derive_role_independence_policy
from operator_vertical_store import vertical_projection
from v03_lost_ack_live_runner import IDEMPOTENCY_KEY as LOST_ACK_IDEMPOTENCY_KEY
from v03_real_runtime_driver import assemble_live_preflight
from v03_real_runtime_fault_injection import InjectedPersistRunnerCrash, LostAckCrashAfterPersistGateway

PHASE1_EXIT = 87
PHASE1_EVIDENCE = Path("evidence/v03-live-persist-ack-loss-phase1.json")
FINAL_EVIDENCE = Path("evidence/v03-live-persist-ack-loss.json")
WAIT_ATTEMPTS = 120
WAIT_SECONDS = 10.0


class V03PersistAckLossLiveError(RuntimeError):
    pass


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _operation_id(preflight) -> str:
    return operation_id_for(
        preflight.execution.repository,
        preflight.composition.feature_id,
        LOST_ACK_IDEMPOTENCY_KEY,
    )


def _scenario_events(preflight, operation_id: str) -> list[dict[str, Any]]:
    return operation_events(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id)


def _one_event(events: list[dict[str, Any]], event_type: str, *, generation: int | None = None) -> dict[str, Any]:
    rows = [
        row for row in events
        if row.get("event_type") == event_type
        and (generation is None or int(row.get("operation_generation", -1)) == generation)
    ]
    if len(rows) != 1:
        raise V03PersistAckLossLiveError(
            f"Persist ACK-loss requires exactly one {event_type}"
            + (f" in generation {generation}" if generation is not None else "")
        )
    return rows[0]


def _require_lost_ack_takeover_state(preflight, operation_id: str) -> dict[str, Any]:
    """Require the exact durable end-state of the preceding live lost-ACK phase."""
    snapshot = preflight.composition.bundle.runtime.backend.read_snapshot()
    projection = vertical_projection(snapshot, operation_id)
    if int(projection.get("generation", -1)) != 1:
        raise V03PersistAckLossLiveError("Persist ACK-loss requires lost-ACK takeover generation G1")
    if projection.get("status") != "WAITING_EXTERNAL":
        raise V03PersistAckLossLiveError("Persist ACK-loss requires the adopted Reviewer to remain WAITING_EXTERNAL")

    events = operation_events(snapshot, operation_id)
    g0_auth = _one_event(events, "dispatch.launch.authorized", generation=0)
    g1_auth = _one_event(events, "dispatch.launch.authorized", generation=1)
    g1_lookup = _one_event(events, "dispatch.launch.lookup-recorded", generation=1)
    if any(
        row.get("event_type") == "dispatch.launch.lookup-recorded"
        and int(row.get("operation_generation", -1)) == 0
        for row in events
    ):
        raise V03PersistAckLossLiveError("preceding lost-ACK G0 unexpectedly contains local lookup evidence")
    if (g1_lookup.get("payload") or {}).get("lookup_state") != "LAUNCHED":
        raise V03PersistAckLossLiveError("G1 lost-ACK adoption is not an exact LAUNCHED receipt")

    keys = {
        str((row.get("payload") or {}).get("external_dispatch_key") or "")
        for row in (g0_auth, g1_auth, g1_lookup)
    }
    if len(keys) != 1 or not next(iter(keys)):
        raise V03PersistAckLossLiveError("lost-ACK G0/G1 external dispatch identity drifted")
    external_key = next(iter(keys))
    runtime_receipt = str((g1_lookup.get("payload") or {}).get("receipt_id") or "")
    if not runtime_receipt:
        raise V03PersistAckLossLiveError("lost-ACK G1 adoption lacks runtime receipt identity")

    forbidden = {
        "worker.callback.recorded",
        "worker.result.validated",
        "feature.event.translated",
        "persist.requested",
        "persist.linearized",
        "persist.confirmed",
    }
    if any(row.get("event_type") in forbidden for row in events):
        raise V03PersistAckLossLiveError("Persist ACK-loss phase1 requires no prior callback/Persist adoption")
    if any(row.get("event_type") == "dispatch.launch.unknown" for row in events):
        raise V03PersistAckLossLiveError("lost-ACK predecessor contains UNKNOWN launch state")
    return {
        "operation_id": operation_id,
        "generation": 1,
        "external_dispatch_key": external_key,
        "runtime_receipt_identity": runtime_receipt,
        "expected_revision": int(projection["expected_feature_revision"]),
        "g0_launch_authorized_event_id": g0_auth["event_id"],
        "g1_launch_authorized_event_id": g1_auth["event_id"],
        "g1_lookup_event_id": g1_lookup["event_id"],
    }


def _retryable_wait(exc: VerticalInvariantError) -> bool:
    text = str(exc)
    return exc.code == "BLOCKED" and any(
        token in text
        for token in (
            "first-attempt gh-aw run snapshot is not exact/successful",
            "exact gh-aw run is not successful workflow_dispatch",
        )
    )


def _prepare_exact_reviewer_event(
    preflight,
    *,
    operation_id: str,
    external_dispatch_key: str,
    wait_attempts: int = WAIT_ATTEMPTS,
    wait_seconds: float = WAIT_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Read-only resolve the exact first Reviewer completion and derive its Event id."""
    if wait_attempts < 1 or wait_attempts > 240 or wait_seconds < 0 or wait_seconds > 30:
        raise V03PersistAckLossLiveError("invalid bounded Reviewer completion wait policy")
    collector = preflight.composition.collector
    executor = collector.callback_coordinator.executor
    result_source = collector.result_source

    last_error: Exception | None = None
    resolved = None
    for attempt in range(wait_attempts):
        snapshot = executor.runtime.backend.read_snapshot()
        projection, launch, receipt = _current_launch_binding(
            snapshot,
            operation_id=operation_id,
            external_dispatch_key=external_dispatch_key,
        )
        semantic_key = str(launch["semantic_effect_key"])
        trusted = {
            "operation_id": operation_id,
            "operation_generation": int(projection["generation"]),
            "operation_profile": VERTICAL_PROFILE,
            "semantic_effect_key": semantic_key,
            "external_dispatch_key": external_dispatch_key,
            "dispatch_id": str(launch["dispatch_id"]),
            "target_repository": str(projection["target_repository"]),
            "target_ref": executor.config.target_ref,
            "feature_id": str(projection["feature_id"]),
            "expected_revision": int(projection["expected_feature_revision"]),
            "feature_stage": str(launch["stage"]),
            "role": str(launch["role"]),
            "launch_candidate_head_sha": launch.get("candidate_head_sha"),
        }
        try:
            resolved = result_source.resolve(
                external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=receipt,
                trusted_context=trusted,
            )
            break
        except VerticalInvariantError as exc:
            last_error = exc
            if not _retryable_wait(exc) or attempt + 1 == wait_attempts:
                raise V03PersistAckLossLiveError(
                    f"exact Reviewer completion did not become release-eligible: {exc.code}: {exc}"
                ) from exc
            sleeper(wait_seconds)
    if resolved is None:
        raise V03PersistAckLossLiveError("exact Reviewer completion was not resolved") from last_error

    snapshot = executor.runtime.backend.read_snapshot()
    projection, launch, receipt = _current_launch_binding(
        snapshot,
        operation_id=operation_id,
        external_dispatch_key=external_dispatch_key,
    )
    semantic_key = str(launch["semantic_effect_key"])
    reservation = snapshot.get(reservation_path(semantic_key))
    if not isinstance(reservation, dict):
        raise V03PersistAckLossLiveError("Reviewer completion lacks durable semantic reservation")
    _validate_run(
        resolved.run,
        control_repository=collector.control_repository,
        workflows=collector.workflows,
        launch=launch,
        expected_receipt_identity=receipt,
        external_dispatch_key=external_dispatch_key,
        reservation=reservation,
    )
    if str(resolved.run.run_id) != receipt:
        raise V03PersistAckLossLiveError("first-attempt Reviewer run id differs from durable runtime receipt")
    role = str(launch["role"])
    worker_payload = validate_worker_result(role, resolved.role_payload)
    if role != "reviewer" or worker_payload.get("verdict") != "PASS":
        raise V03PersistAckLossLiveError(
            "Persist ACK-loss release scenario requires the exact first-attempt Reviewer to PASS"
        )
    context = TrustedDispatchContext(
        operation_id=operation_id,
        operation_generation=int(projection["generation"]),
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=semantic_key,
        external_dispatch_key=external_dispatch_key,
        dispatch_id=str(launch["dispatch_id"]),
        runtime_receipt_identity=receipt,
        target_repository=str(projection["target_repository"]),
        target_ref=executor.config.target_ref,
        feature_id=str(projection["feature_id"]),
        expected_revision=int(projection["expected_feature_revision"]),
        feature_stage=str(launch["stage"]),
        task_id=resolved.run.task_id,
        role=role,
        candidate_pr_number=resolved.run.candidate_pr_number,
        candidate_head_sha=launch.get("candidate_head_sha"),
        worker_identity=resolved.run.worker_identity,
        collector_identity=resolved.run.collector_identity,
    )
    declared = {str(row["label"]): str(row["kind"]) for row in worker_payload.get("outputs", [])}
    receipts = _build_receipts(
        coordinator=collector.callback_coordinator,
        context=context,
        outputs=resolved.outputs,
        declared_outputs=declared,
        collected_at=str(collector.clock()),
    )
    feature, _ = executor.feature_gateway.read_feature(operation_id=operation_id)
    policy = derive_role_independence_policy(snapshot, operation_id=operation_id)
    event = translate_result(
        context=context,
        feature=feature,
        worker_payload=worker_payload,
        receipts=receipts,
        independence_policy=policy,
        occurred_at=str(collector.clock()),
        content_loader=result_source.load_content,
    )
    if not isinstance(event, dict) or not event.get("id"):
        raise V03PersistAckLossLiveError("Reviewer PASS did not derive one exact Feature Event")
    callback_id = "gh-aw-callback-" + digest_json(
        {
            "operation_id": operation_id,
            "generation": context.operation_generation,
            "external_dispatch_key": external_dispatch_key,
            "runtime_receipt_identity": receipt,
            "run_id": resolved.run.run_id,
        }
    )[:24]
    return {
        "feature_event_id": str(event["id"]),
        "callback_id": callback_id,
        "run_id": int(resolved.run.run_id),
        "runtime_receipt_identity": receipt,
        "expected_revision": int(context.expected_revision),
        "candidate_head_sha": context.candidate_head_sha,
        "semantic_effect_key": context.semantic_effect_key,
    }


def run_phase1(
    *,
    preflight,
    evidence_path: Path = PHASE1_EVIDENCE,
    hard_exit: Callable[[int], Any] = os._exit,
    prepare_exact_reviewer_event: Callable[..., dict[str, Any]] = _prepare_exact_reviewer_event,
) -> None:
    operation_id = _operation_id(preflight)
    predecessor = _require_lost_ack_takeover_state(preflight, operation_id)
    prepared = prepare_exact_reviewer_event(
        preflight,
        operation_id=operation_id,
        external_dispatch_key=predecessor["external_dispatch_key"],
    )
    if prepared["expected_revision"] != predecessor["expected_revision"]:
        raise V03PersistAckLossLiveError("prepared Reviewer completion revision differs from durable G1 fence")
    if prepared["runtime_receipt_identity"] != predecessor["runtime_receipt_identity"]:
        raise V03PersistAckLossLiveError("Reviewer completion runtime receipt differs from G1 adopted receipt")

    bundle = preflight.composition.bundle
    base = getattr(bundle.executor, "base", bundle.executor)
    normal_persist = getattr(base, "persist_gateway", None)
    if normal_persist is not preflight.composition.feature_event_gateway:
        raise V03PersistAckLossLiveError("phase1 executor does not use exact production Persist gateway")
    base.persist_gateway = LostAckCrashAfterPersistGateway(
        delegate=normal_persist,
        expected_feature_event_id=prepared["feature_event_id"],
        expected_target_ref=preflight.composition.target_ref,
    )
    try:
        preflight.composition.collector.handle(
            operation_id=operation_id,
            external_dispatch_key=predecessor["external_dispatch_key"],
        )
    except InjectedPersistRunnerCrash as crash:
        if (
            crash.feature_event_id != prepared["feature_event_id"]
            or crash.target_ref != preflight.composition.target_ref
        ):
            raise V03PersistAckLossLiveError("Persist fault escaped for a different exact Event/ref")
        events = _scenario_events(preflight, operation_id)
        recorded = [
            row for row in events
            if row.get("event_type") == "worker.callback.recorded"
            and (row.get("payload") or {}).get("callback_id") == prepared["callback_id"]
        ]
        validated = [
            row for row in events
            if row.get("event_type") == "worker.result.validated"
            and (row.get("payload") or {}).get("callback_id") == prepared["callback_id"]
        ]
        translated = [
            row for row in events
            if row.get("event_type") == "feature.event.translated"
            and (row.get("payload") or {}).get("feature_event_id") == prepared["feature_event_id"]
        ]
        requested = [
            row for row in events
            if row.get("event_type") == "persist.requested"
            and (row.get("payload") or {}).get("feature_event_id") == prepared["feature_event_id"]
        ]
        linearized = [
            row for row in events
            if row.get("event_type") == "persist.linearized"
            and (row.get("payload") or {}).get("feature_event_id") == prepared["feature_event_id"]
        ]
        confirmed = [
            row for row in events
            if row.get("event_type") == "persist.confirmed"
            and (row.get("payload") or {}).get("feature_event_id") == prepared["feature_event_id"]
        ]
        if not all(len(rows) == 1 for rows in (recorded, validated, translated, requested, linearized)) or confirmed:
            raise V03PersistAckLossLiveError(
                "phase1 durable state is not exact callback+translated+requested+linearized+unconfirmed"
            )
        _write_json(
            evidence_path,
            {
                "schema_version": "ai-sdlc.v03-live-persist-ack-loss-phase1/v1",
                "installation_commit_sha": preflight.execution.installation_commit_sha,
                "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
                "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
                "operation_id": operation_id,
                "operation_generation": 1,
                "semantic_effect_key": prepared["semantic_effect_key"],
                "external_dispatch_key": predecessor["external_dispatch_key"],
                "runtime_receipt_identity": prepared["runtime_receipt_identity"],
                "reviewer_run_id": prepared["run_id"],
                "callback_id": prepared["callback_id"],
                "feature_event_id": prepared["feature_event_id"],
                "target_ref": preflight.composition.target_ref,
                "expected_revision": prepared["expected_revision"],
                "expected_result_revision": prepared["expected_revision"] + 1,
                "fixture_candidate_pr_number": preflight.fixture_candidate.candidate_pr_number,
                "fixture_candidate_head_sha_before_persist": prepared["candidate_head_sha"],
                "lost_ack_predecessor": predecessor,
                "fault_code": crash.code,
                "persist_requested_event_id": requested[0]["event_id"],
                "persist_linearized_event_id": linearized[0]["event_id"],
                "persist_confirmed_count": 0,
                "phase1_external_feature_write_count": 1,
                "hard_exit_code": PHASE1_EXIT,
            },
        )
        hard_exit(PHASE1_EXIT)
        raise V03PersistAckLossLiveError("hard-exit hook returned instead of terminating phase1")
    raise V03PersistAckLossLiveError("phase1 returned without exact injected Persist acknowledgement-loss crash")


class LookupOnlyPersistRecoveryGateway:
    """Verification-only fresh-process fence: exact lookup may pass; write cannot."""

    verification_only = True

    def __init__(self, *, delegate: Any, expected_event_id: str, expected_target_ref: str, expected_result_revision: int):
        if not callable(getattr(delegate, "lookup_feature_event", None)):
            raise ValueError("production Persist delegate with exact lookup is required")
        self.delegate = delegate
        self.expected_event_id = expected_event_id
        self.expected_target_ref = expected_target_ref
        self.expected_result_revision = expected_result_revision
        self.lookup_calls = 0
        self.persist_calls = 0

    def lookup_feature_event(self, *, event_id: str, target_ref: str):
        if event_id != self.expected_event_id or target_ref != self.expected_target_ref:
            raise V03PersistAckLossLiveError("fresh Persist recovery lookup escaped exact Event/ref")
        self.lookup_calls += 1
        receipt = self.delegate.lookup_feature_event(event_id=event_id, target_ref=target_ref)
        if (
            not isinstance(receipt, dict)
            or receipt.get("event_id") != self.expected_event_id
            or receipt.get("result_revision") != self.expected_result_revision
        ):
            raise V03PersistAckLossLiveError("fresh Persist recovery did not observe the exact authoritative receipt")
        return receipt

    def persist_feature_event(self, *, event: dict[str, Any], target_ref: str):
        self.persist_calls += 1
        raise V03PersistAckLossLiveError(
            "fresh Persist ACK-loss recovery attempted a second Feature Event write"
        )


def _load_phase1(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise V03PersistAckLossLiveError("fresh-process phase2 lacks valid phase1 evidence") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "ai-sdlc.v03-live-persist-ack-loss-phase1/v1":
        raise V03PersistAckLossLiveError("Persist ACK-loss phase1 evidence schema is invalid")
    return value


def run_phase2(
    *,
    preflight,
    phase1_path: Path = PHASE1_EVIDENCE,
    final_path: Path = FINAL_EVIDENCE,
) -> dict[str, Any]:
    prior = _load_phase1(phase1_path)
    operation_id = _operation_id(preflight)
    if prior.get("operation_id") != operation_id:
        raise V03PersistAckLossLiveError("fresh-process operation identity differs from phase1")
    if prior.get("installation_commit_sha") != preflight.execution.installation_commit_sha:
        raise V03PersistAckLossLiveError("phase2 trusted-main installation differs from phase1")
    if prior.get("materialization_commit_sha") != preflight.live_authority.materialization_commit_sha:
        raise V03PersistAckLossLiveError("phase2 live policy materialization differs from phase1")
    if prior.get("policy_bundle_digest") != preflight.live_authority.policy.bundle_digest:
        raise V03PersistAckLossLiveError("phase2 policy bundle differs from phase1")
    if prior.get("target_ref") != preflight.composition.target_ref:
        raise V03PersistAckLossLiveError("phase2 fixture ref differs from phase1")
    if str(prior.get("reviewer_run_id")) != str(prior.get("runtime_receipt_identity")):
        raise V03PersistAckLossLiveError("phase1 Reviewer run/receipt identity was not exact")

    bundle = preflight.composition.bundle
    base = getattr(bundle.executor, "base", bundle.executor)
    normal_persist = getattr(base, "persist_gateway", None)
    if normal_persist is not preflight.composition.feature_event_gateway:
        raise V03PersistAckLossLiveError("phase2 executor does not use exact production Persist gateway")
    fence = LookupOnlyPersistRecoveryGateway(
        delegate=normal_persist,
        expected_event_id=str(prior["feature_event_id"]),
        expected_target_ref=str(prior["target_ref"]),
        expected_result_revision=int(prior["expected_result_revision"]),
    )
    base.persist_gateway = fence
    recovered = bundle.executor._reconcile_persist(operation_id)
    if recovered is not True:
        raise V03PersistAckLossLiveError("fresh process did not reconcile the exact linearized Persist receipt")

    events = _scenario_events(preflight, operation_id)
    confirmed = [
        row for row in events
        if row.get("event_type") == "persist.confirmed"
        and (row.get("payload") or {}).get("feature_event_id") == prior["feature_event_id"]
    ]
    if len(confirmed) != 1:
        raise V03PersistAckLossLiveError("fresh Persist recovery did not confirm exactly once")
    payload = confirmed[0].get("payload") or {}
    if payload.get("result_revision") != prior["expected_result_revision"]:
        raise V03PersistAckLossLiveError("fresh Persist confirmation has wrong exact result revision")
    projection = vertical_projection(bundle.runtime.backend.read_snapshot(), operation_id)
    if projection["expected_feature_revision"] != prior["expected_result_revision"]:
        raise V03PersistAckLossLiveError("fresh Persist recovery did not advance the Vertical revision fence")
    if fence.lookup_calls != 1 or fence.persist_calls != 0:
        raise V03PersistAckLossLiveError("fresh Persist recovery was not exact lookup-only convergence")

    g0_auth = [
        row for row in events
        if row.get("event_type") == "dispatch.launch.authorized"
        and int(row.get("operation_generation", -1)) == 0
        and (row.get("payload") or {}).get("external_dispatch_key") == prior["external_dispatch_key"]
    ]
    g1_auth = [
        row for row in events
        if row.get("event_type") == "dispatch.launch.authorized"
        and int(row.get("operation_generation", -1)) == 1
        and (row.get("payload") or {}).get("external_dispatch_key") == prior["external_dispatch_key"]
    ]
    g1_lookup = [
        row for row in events
        if row.get("event_type") == "dispatch.launch.lookup-recorded"
        and int(row.get("operation_generation", -1)) == 1
        and (row.get("payload") or {}).get("external_dispatch_key") == prior["external_dispatch_key"]
        and (row.get("payload") or {}).get("lookup_state") == "LAUNCHED"
        and str((row.get("payload") or {}).get("receipt_id") or "") == str(prior["runtime_receipt_identity"])
    ]
    callbacks = [
        row for row in events
        if row.get("event_type") == "worker.callback.recorded"
        and (row.get("payload") or {}).get("callback_id") == prior["callback_id"]
    ]
    translated = [
        row for row in events
        if row.get("event_type") == "feature.event.translated"
        and (row.get("payload") or {}).get("feature_event_id") == prior["feature_event_id"]
    ]
    unknown = [row for row in events if row.get("event_type") == "dispatch.launch.unknown"]
    if not (
        len(g0_auth) == 1
        and len(g1_auth) == 1
        and len(g1_lookup) == 1
        and len(callbacks) == 1
        and len(translated) == 1
        and len(confirmed) == 1
        and not unknown
    ):
        raise V03PersistAckLossLiveError(
            "combined lost-ACK/Persist evidence lacks exact G0+G1/run/callback/Event/Persist lineage"
        )

    again = bundle.executor._reconcile_persist(operation_id)
    if again is not None or fence.lookup_calls != 1 or fence.persist_calls != 0:
        raise V03PersistAckLossLiveError("repeated reconciliation after confirmation was not externally inert")

    evidence = {
        "schema_version": "ai-sdlc.v03-live-persist-ack-loss/v1",
        "scenario": "persist-ack-loss-fresh-process-recovery",
        "completed_issue_221_scenarios": [
            "lost-ack-crash-takeover",
            "persist-ack-loss-recovery",
        ],
        "lost_ack_crash_takeover_status": "PASS",
        "persist_ack_loss_recovery_status": "PASS",
        "status": "PASS",
        "release_evidence_scope": "issue-221-lost-ack-and-persist-ack-loss-only",
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
        "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
        "operation_id": operation_id,
        "operation_generation": int(projection["generation"]),
        "semantic_effect_key": prior["semantic_effect_key"],
        "external_dispatch_key": prior["external_dispatch_key"],
        "runtime_receipt_identity": prior["runtime_receipt_identity"],
        "reviewer_run_id": prior["reviewer_run_id"],
        "callback_id": prior["callback_id"],
        "feature_event_id": prior["feature_event_id"],
        "feature_revision_before": prior["expected_revision"],
        "feature_revision_after": prior["expected_result_revision"],
        "phase1": prior,
        "persist_confirmed_event_id": confirmed[0]["event_id"],
        "fresh_exact_lookup_count": fence.lookup_calls,
        "fresh_retry_write_count": fence.persist_calls,
        "external_runtime_execution_count": 1,
        "feature_persist_count": 1,
        "duplicate_external_effect_count": 0,
        "duplicate_feature_write_count": 0,
        "unauthorized_lifecycle_transition_count": 0,
        "speculative_retry_under_unknown_count": 0,
        "overall_issue_221_pass": False,
    }
    _write_json(final_path, evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("phase1", "phase2"), required=True)
    args = parser.parse_args()
    preflight = assemble_live_preflight(env=os.environ, checkout_sha=os.environ["GITHUB_SHA"])
    if args.phase == "phase1":
        run_phase1(preflight=preflight)
        return
    print(json.dumps(run_phase2(preflight=preflight), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
