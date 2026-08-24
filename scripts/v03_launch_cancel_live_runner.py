#!/usr/bin/env python3
"""Trusted-main live runner for the two Issue #221 launch/cancel orderings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from operator_store import plan_cancel
from operator_store_model import operation_events, operation_id_for, reservation_path
from operator_vertical import FeatureSnapshot
from operator_vertical_store import vertical_projection
from v03_effect_safety_live_ledger import ReleaseAuthority
from v03_effect_safety_live_ledger_launch_cancel import (
    PAIR_SCENARIOS,
    evaluate_issue_221_with_launch_cancel,
)
from v03_live_evidence_provenance import write_live_evidence_envelope
from v03_lost_ack_live_runner import IDEMPOTENCY_KEY as LOST_ACK_IDEMPOTENCY_KEY
from v03_real_runtime_driver import assemble_live_preflight

BEFORE_IDEMPOTENCY_KEY = LOST_ACK_IDEMPOTENCY_KEY
AFTER_IDEMPOTENCY_KEY = "v03-release-fi-launch-auth-before-cancel"
ADAPTER_ID = "v03-real-runtime-release-verifier"
BEFORE_EVIDENCE = Path("evidence/v03-live-cancel-before-launch-authorization.json")
PAIR_EVIDENCE = Path("evidence/v03-live-launch-cancel-pair.json")
AUTHORITY_EVIDENCE = Path("evidence/v03-live-launch-cancel-authority.json")
PROVENANCE_EVIDENCE = Path("evidence/v03-live-launch-cancel-provenance.json")
PARTIAL_LEDGER_EVIDENCE = Path("evidence/v03-live-launch-cancel-ledger-partial.json")


class V03LaunchCancelLiveError(RuntimeError):
    pass


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _events(preflight, operation_id: str) -> list[dict[str, Any]]:
    return operation_events(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id)


def _feature(preflight) -> tuple[FeatureSnapshot, dict[str, Any]]:
    manifest = preflight.composition.feature_event_gateway.read_feature(
        repository=preflight.execution.repository,
        feature_id=preflight.composition.feature_id,
        target_ref=preflight.composition.target_ref,
    )
    candidate = preflight.composition.candidate_provider.resolve()
    return (
        FeatureSnapshot.from_manifest(
            repository=preflight.execution.repository,
            target_ref=preflight.composition.target_ref,
            manifest=manifest,
            candidate_pr_number=candidate.candidate_pr_number,
            candidate_head_sha=candidate.candidate_head_sha,
        ),
        manifest,
    )


def _base_executor(preflight):
    executor = preflight.composition.bundle.executor
    return getattr(executor, "base", executor)


def _persist_after_sequence(events: list[dict[str, Any]], sequence: int) -> int:
    return sum(
        1
        for row in events
        if int(row.get("sequence", -1)) > sequence
        and str(row.get("event_type") or "").startswith("persist.")
    )


class NoExternalDispatchGateway:
    """Verification-only fence: cancellation-before-authorization must never touch Worker transport."""

    verification_only = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.launch_calls = 0
        self.lookup_calls = 0

    def launch(self, *, dispatch):
        self.launch_calls += 1
        raise V03LaunchCancelLiveError("cancel-before-authorization reached external Worker launch")

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls += 1
        raise V03LaunchCancelLiveError("cancel-before-authorization reached external Worker lookup")


class CancelAfterNewClaimFeatureGateway:
    """Delegate production Feature truth, injecting cancel only for one newly-created claim."""

    verification_only = True

    def __init__(
        self,
        *,
        delegate,
        runtime,
        clock,
        trusted_context_digest: str,
        operation_id: str,
        baseline_claim_ids: set[str],
    ):
        self.delegate = delegate
        self.runtime = runtime
        self.clock = clock
        self.trusted_context_digest = trusted_context_digest
        self.operation_id = operation_id
        self.baseline_claim_ids = set(baseline_claim_ids)
        self.injected_claim: dict[str, Any] | None = None

    def read_feature(self, *, operation_id):
        if operation_id != self.operation_id:
            raise V03LaunchCancelLiveError("cancel injection escaped the exact predecessor Operation")
        events = operation_events(self.runtime.backend.read_snapshot(), operation_id)
        new_claims = [
            dict(row.get("payload") or {})
            for row in events
            if row.get("event_type") == "dispatch.claimed"
            and str((row.get("payload") or {}).get("claim_id") or "") not in self.baseline_claim_ids
        ]
        if len(new_claims) > 1:
            raise V03LaunchCancelLiveError("cancel-before-authorization observed multiple new dispatch claims")
        if new_claims and self.injected_claim is None:
            claim = new_claims[0]
            claim_id = str(claim.get("claim_id") or "")
            authorized = any(
                row.get("event_type") == "dispatch.launch.authorized"
                and (row.get("payload") or {}).get("claim_id") == claim_id
                for row in events
            )
            cancelled = any(row.get("event_type") == "operation.cancelled" for row in events)
            if authorized:
                raise V03LaunchCancelLiveError("cancel-before-authorization injection arrived after launch authorization")
            if not cancelled:
                self.runtime.commit_replanned(
                    lambda snapshot: plan_cancel(
                        snapshot,
                        operation_id=operation_id,
                        reason="fault-injection: cancellation durable before launch authorization",
                        occurred_at=self.clock(),
                        trusted_context_digest=self.trusted_context_digest,
                    )
                )
                self.injected_claim = claim
        return self.delegate.read_feature(operation_id=operation_id)


class CancelAfterAuthorizedProductionLaunchGateway:
    """One real Worker launch, then durable cancel before executor records its receipt."""

    verification_only = True

    def __init__(
        self,
        *,
        delegate,
        runtime,
        clock,
        trusted_context_digest: str,
        expected_operation_id: str,
        expected_semantic_effect_key: str,
        expected_external_dispatch_key: str,
    ):
        self.delegate = delegate
        self.runtime = runtime
        self.clock = clock
        self.trusted_context_digest = trusted_context_digest
        self.expected_operation_id = expected_operation_id
        self.expected_semantic_effect_key = expected_semantic_effect_key
        self.expected_external_dispatch_key = expected_external_dispatch_key
        self.launch_calls = 0
        self.lookup_calls = 0
        self.receipt: dict[str, Any] | None = None

    def launch(self, *, dispatch):
        operation_id = str(dispatch.get("operation_id") or "")
        generation = int(dispatch.get("operation_generation", -1))
        semantic_key = str(dispatch.get("semantic_effect_key") or "")
        external_key = str(dispatch.get("external_dispatch_key") or "")
        if (
            operation_id != self.expected_operation_id
            or semantic_key != self.expected_semantic_effect_key
            or external_key != self.expected_external_dispatch_key
        ):
            raise V03LaunchCancelLiveError("authorization-before-cancel launch escaped exact semantic identity")
        authorized = [
            row
            for row in operation_events(self.runtime.backend.read_snapshot(), operation_id)
            if row.get("event_type") == "dispatch.launch.authorized"
            and int(row.get("operation_generation", -1)) == generation
            and (row.get("payload") or {}).get("external_dispatch_key") == external_key
        ]
        if len(authorized) != 1:
            raise V03LaunchCancelLiveError("real launch did not have exactly one durable authorization")
        self.launch_calls += 1
        if self.launch_calls != 1:
            raise V03LaunchCancelLiveError("authorization-before-cancel attempted duplicate external launch")
        receipt = self.delegate.launch(dispatch=dispatch)
        self.runtime.commit_replanned(
            lambda snapshot: plan_cancel(
                snapshot,
                operation_id=operation_id,
                reason="fault-injection: launch authorization durable before cancellation",
                occurred_at=self.clock(),
                trusted_context_digest=self.trusted_context_digest,
            )
        )
        self.receipt = dict(receipt) if isinstance(receipt, dict) else None
        return receipt

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls += 1
        return self.delegate.lookup(external_dispatch_key=external_dispatch_key)


def _start_request(preflight, *, idempotency_key: str, expected_revision: int) -> dict[str, Any]:
    return {
        "idempotency_key": idempotency_key,
        "client_identity": {"adapter_id": ADAPTER_ID},
        "target": {
            "repository": preflight.execution.repository,
            "feature_id": preflight.composition.feature_id,
        },
        "context": {"expected_feature_revision": expected_revision},
    }


def _trusted_context(preflight):
    return preflight.composition.bundle.write_bundle.read_bundle.trusted_context_provider.for_request(
        {
            "repository": preflight.execution.repository,
            "feature_id": preflight.composition.feature_id,
        }
    )


def run_cancel_before_authorization(
    *,
    preflight,
    evidence_path: Path = BEFORE_EVIDENCE,
) -> dict[str, Any]:
    operation_id = operation_id_for(
        preflight.execution.repository,
        preflight.composition.feature_id,
        BEFORE_IDEMPOTENCY_KEY,
    )
    before_events = _events(preflight, operation_id)
    baseline_claim_ids = {
        str((row.get("payload") or {}).get("claim_id") or "")
        for row in before_events
        if row.get("event_type") == "dispatch.claimed"
    }
    baseline_persist_ids = {
        row["event_id"] for row in before_events if row.get("event_type") == "persist.confirmed"
    }
    projection_before = vertical_projection(
        preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id
    )
    feature_before, _ = _feature(preflight)
    if projection_before.get("status") != "RUNNING":
        raise V03LaunchCancelLiveError("cancel pair requires the chained lost-ACK Operation to be RUNNING")
    if projection_before.get("expected_feature_revision") != feature_before.revision:
        raise V03LaunchCancelLiveError("pre-cancel Operation/Feature revision fence drifted")
    if feature_before.current_stage != "verification" or feature_before.stages.get("verification") != "READY":
        raise V03LaunchCancelLiveError("cancel pair requires verification stage READY after Reviewer PASS")

    bundle = preflight.composition.bundle
    base = _base_executor(preflight)
    if base.feature_gateway is not preflight.composition.feature_truth_gateway:
        raise V03LaunchCancelLiveError("executor does not use exact production Feature truth gateway")
    if base.dispatch_gateway is not preflight.composition.dispatch_gateway:
        raise V03LaunchCancelLiveError("executor does not use exact production dispatch gateway")

    feature_fence = CancelAfterNewClaimFeatureGateway(
        delegate=base.feature_gateway,
        runtime=bundle.runtime,
        clock=bundle.runtime.clock,
        trusted_context_digest=base.config.trusted_context_digest,
        operation_id=operation_id,
        baseline_claim_ids=baseline_claim_ids,
    )
    external_fence = NoExternalDispatchGateway(base.dispatch_gateway)
    base.feature_gateway = feature_fence
    base.dispatch_gateway = external_fence
    try:
        bundle.executor.advance_until_stop(operation_id=operation_id)
    except Exception as exc:
        projection = vertical_projection(bundle.runtime.backend.read_snapshot(), operation_id)
        if projection.get("status") != "CANCELLED":
            raise V03LaunchCancelLiveError(
                f"cancel-before-authorization failed before durable cancellation: {getattr(exc, 'code', type(exc).__name__)}"
            ) from exc
    projection = vertical_projection(bundle.runtime.backend.read_snapshot(), operation_id)
    if projection.get("status") != "CANCELLED" or feature_fence.injected_claim is None:
        raise V03LaunchCancelLiveError("cancel-before-authorization did not converge to exact durable cancellation")
    if external_fence.launch_calls or external_fence.lookup_calls:
        raise V03LaunchCancelLiveError("cancel-before-authorization touched external Worker transport")

    events = _events(preflight, operation_id)
    claim_id = str(feature_fence.injected_claim["claim_id"])
    claims = [
        row for row in events
        if row.get("event_type") == "dispatch.claimed"
        and (row.get("payload") or {}).get("claim_id") == claim_id
    ]
    if len(claims) != 1:
        raise V03LaunchCancelLiveError("cancel-before-authorization lacks exactly one injected claim")
    claim_payload = claims[0].get("payload") or {}
    semantic_key = str(claim_payload.get("semantic_effect_key") or "")
    external_key = str(claim_payload.get("external_dispatch_key") or "")
    authorization = [
        row for row in events
        if row.get("event_type") == "dispatch.launch.authorized"
        and (row.get("payload") or {}).get("claim_id") == claim_id
    ]
    lookup = [
        row for row in events
        if row.get("event_type") == "dispatch.launch.lookup-recorded"
        and (row.get("payload") or {}).get("external_dispatch_key") == external_key
        and int(row.get("sequence", -1)) >= int(claims[0]["sequence"])
    ]
    cancellations = [row for row in events if row.get("event_type") == "operation.cancelled"]
    if authorization or lookup or len(cancellations) != 1:
        raise V03LaunchCancelLiveError("cancel-before-authorization durable launch/cancel ordering is incorrect")
    cancel_sequence = int(cancellations[0]["sequence"])
    if not int(claims[0]["sequence"]) < cancel_sequence:
        raise V03LaunchCancelLiveError("cancel-before-authorization did not persist claim before cancel")

    snapshot = bundle.runtime.backend.read_snapshot()
    reservation = snapshot.get(reservation_path(semantic_key))
    if not isinstance(reservation, dict) or reservation.get("external_dispatch_key") != external_key:
        raise V03LaunchCancelLiveError("cancel-before-authorization lacks exact immutable reservation")
    feature_after, _ = _feature(preflight)
    new_persist = [
        row for row in events
        if row.get("event_type") == "persist.confirmed" and row["event_id"] not in baseline_persist_ids
    ]
    if len(new_persist) != 1:
        raise V03LaunchCancelLiveError("verification READY->WORKING setup did not Persist exactly once")
    if (
        feature_after.revision != reservation.get("expected_revision")
        or feature_after.current_stage != "verification"
        or feature_after.stages.get("verification") != "WORKING"
        or feature_after.candidate_head_sha != reservation.get("candidate_head_sha")
    ):
        raise V03LaunchCancelLiveError("QA reservation differs from fresh verification Feature/candidate truth")
    if _persist_after_sequence(events, cancel_sequence) != 0:
        raise V03LaunchCancelLiveError("cancel-before-authorization gained Persist authority after cancellation")

    evidence = {
        "schema_version": "ai-sdlc.v03-live-cancel-before-authorization/v1",
        "status": "PASS",
        "scenario": PAIR_SCENARIOS[0],
        "operation_id": operation_id,
        "operation_generation": int(projection["generation"]),
        "semantic_effect_key": semantic_key,
        "external_dispatch_key": external_key,
        "candidate_head_sha": str(reservation.get("candidate_head_sha") or ""),
        "feature_revision_before": int(reservation["expected_revision"]),
        "runtime_lookup_state": "NOT_LAUNCHED",
        "runtime_receipt_identity": None,
        "final_status": "CANCELLED",
        "dispatch_claim_count": 1,
        "launch_authorization_count": 0,
        "launch_lookup_count": 0,
        "external_runtime_execution_count": 0,
        "setup_feature_persist_count": 1,
        "post_cancel_persist_authority_count": 0,
        "claim_sequence": int(claims[0]["sequence"]),
        "cancel_sequence": cancel_sequence,
        "measurements": {
            "duplicate_external_effect_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        "overall_issue_221_pass": False,
    }
    _write_json(evidence_path, evidence)
    return evidence


class CancelAfterAuthorizedProductionGateway:
    """Verification-only wrapper: one real launch, then cancel before local receipt record."""

    verification_only = True

    def __init__(
        self,
        *,
        delegate,
        runtime,
        clock,
        trusted_context_digest: str,
        expected_operation_id: str,
        expected_semantic_effect_key: str,
        expected_external_dispatch_key: str,
    ):
        self.delegate = delegate
        self.runtime = runtime
        self.clock = clock
        self.trusted_context_digest = trusted_context_digest
        self.expected_operation_id = expected_operation_id
        self.expected_semantic_effect_key = expected_semantic_effect_key
        self.expected_external_dispatch_key = expected_external_dispatch_key
        self.launch_calls = 0
        self.lookup_calls = 0
        self.returned_receipt: dict[str, Any] | None = None

    def launch(self, *, dispatch):
        operation_id = str(dispatch.get("operation_id") or "")
        generation = int(dispatch.get("operation_generation", -1))
        if (
            operation_id != self.expected_operation_id
            or str(dispatch.get("semantic_effect_key") or "") != self.expected_semantic_effect_key
            or str(dispatch.get("external_dispatch_key") or "") != self.expected_external_dispatch_key
        ):
            raise V03LaunchCancelLiveError("real QA launch escaped exact reused reservation identity")
        authorized = [
            row
            for row in operation_events(self.runtime.backend.read_snapshot(), operation_id)
            if row.get("event_type") == "dispatch.launch.authorized"
            and int(row.get("operation_generation", -1)) == generation
            and (row.get("payload") or {}).get("external_dispatch_key") == self.expected_external_dispatch_key
        ]
        if len(authorized) != 1:
            raise V03LaunchCancelLiveError("QA launch lacks exactly one durable authorization")
        self.launch_calls += 1
        if self.launch_calls != 1:
            raise V03LaunchCancelLiveError("QA cancellation race attempted duplicate launch")
        receipt = self.delegate.launch(dispatch=dispatch)
        self.runtime.commit_replanned(
            lambda snapshot: plan_cancel(
                snapshot,
                operation_id=operation_id,
                reason="fault-injection: launch authorization before cancellation",
                occurred_at=self.clock(),
                trusted_context_digest=self.trusted_context_digest,
            )
        )
        self.returned_receipt = dict(receipt) if isinstance(receipt, dict) else None
        return receipt

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls += 1
        return self.delegate.lookup(external_dispatch_key=external_dispatch_key)


def _load_before(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise V03LaunchCancelLiveError("authorization-before-cancel lacks valid predecessor evidence") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "ai-sdlc.v03-live-cancel-before-authorization/v1":
        raise V03LaunchCancelLiveError("cancel-before-authorization predecessor schema is invalid")
    if value.get("status") != "PASS" or value.get("final_status") != "CANCELLED":
        raise V03LaunchCancelLiveError("cancel-before-authorization predecessor did not PASS")
    return value


def run_authorized_before_cancel(
    *,
    preflight,
    before_path: Path = BEFORE_EVIDENCE,
    pair_path: Path = PAIR_EVIDENCE,
    authority_path: Path = AUTHORITY_EVIDENCE,
    provenance_path: Path = PROVENANCE_EVIDENCE,
    ledger_path: Path = PARTIAL_LEDGER_EVIDENCE,
    github_workflow_run_id: object | None = None,
    workflow_sha: str | None = None,
    envelope_writer: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = write_live_evidence_envelope,
    ledger_evaluator: Callable[..., dict[str, Any]] = evaluate_issue_221_with_launch_cancel,
) -> dict[str, Any]:
    before = _load_before(before_path)
    feature, _ = _feature(preflight)
    if (
        feature.revision != before.get("feature_revision_before")
        or feature.current_stage != "verification"
        or feature.stages.get("verification") != "WORKING"
        or feature.candidate_head_sha != before.get("candidate_head_sha")
    ):
        raise V03LaunchCancelLiveError("fresh process Feature/candidate differs from cancelled QA reservation")

    old_projection = vertical_projection(
        preflight.composition.bundle.runtime.backend.read_snapshot(), before["operation_id"]
    )
    if old_projection.get("status") != "CANCELLED":
        raise V03LaunchCancelLiveError("predecessor Operation is no longer durably CANCELLED")

    operation_id = operation_id_for(
        preflight.execution.repository,
        preflight.composition.feature_id,
        AFTER_IDEMPOTENCY_KEY,
    )
    if _events(preflight, operation_id):
        raise V03LaunchCancelLiveError("authorization-before-cancel requires a clean second Operation idempotency key")
    base = _base_executor(preflight)
    if base.dispatch_gateway is not preflight.composition.dispatch_gateway:
        raise V03LaunchCancelLiveError("fresh executor does not use exact production dispatch gateway")
    gateway = CancelAfterAuthorizedProductionGateway(
        delegate=base.dispatch_gateway,
        runtime=preflight.composition.bundle.runtime,
        clock=preflight.composition.bundle.runtime.clock,
        trusted_context_digest=base.config.trusted_context_digest,
        expected_operation_id=operation_id,
        expected_semantic_effect_key=before["semantic_effect_key"],
        expected_external_dispatch_key=before["external_dispatch_key"],
    )
    base.dispatch_gateway = gateway
    start = preflight.composition.bundle.backends.get("operation.start")
    if start is None or not callable(getattr(start, "invoke", None)):
        raise V03LaunchCancelLiveError("production bundle lacks operation.start")
    try:
        start.invoke(
            _start_request(
                preflight,
                idempotency_key=AFTER_IDEMPOTENCY_KEY,
                expected_revision=feature.revision,
            ),
            _trusted_context(preflight),
        )
    except Exception as exc:
        projection = vertical_projection(
            preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id
        )
        if projection.get("status") != "CANCELLED":
            raise V03LaunchCancelLiveError(
                f"authorization-before-cancel failed before durable cancellation: {getattr(exc, 'code', type(exc).__name__)}"
            ) from exc

    projection = vertical_projection(
        preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id
    )
    if projection.get("status") != "CANCELLED" or gateway.launch_calls != 1:
        raise V03LaunchCancelLiveError("authorization-before-cancel did not converge after exactly one real launch")
    if gateway.lookup_calls != 0:
        raise V03LaunchCancelLiveError("authorized launch unexpectedly used a fallback external lookup")
    if not isinstance(gateway.returned_receipt, dict):
        raise V03LaunchCancelLiveError("real QA launch did not return an exact receipt object")
    if gateway.returned_receipt.get("lookup_state") != "LAUNCHED" or not gateway.returned_receipt.get("receipt_id"):
        raise V03LaunchCancelLiveError("real QA launch did not return a LAUNCHED receipt")

    events = _events(preflight, operation_id)
    claims = [row for row in events if row.get("event_type") == "dispatch.claimed"]
    authorizations = [row for row in events if row.get("event_type") == "dispatch.launch.authorized"]
    cancellations = [row for row in events if row.get("event_type") == "operation.cancelled"]
    lookups = [row for row in events if row.get("event_type") == "dispatch.launch.lookup-recorded"]
    if not (len(claims) == len(authorizations) == len(cancellations) == len(lookups) == 1):
        raise V03LaunchCancelLiveError("authorization-before-cancel lacks exact claim/auth/cancel/lookup cardinality")
    claim_payload = claims[0].get("payload") or {}
    auth_payload = authorizations[0].get("payload") or {}
    lookup_payload = lookups[0].get("payload") or {}
    if (
        claim_payload.get("semantic_effect_key") != before["semantic_effect_key"]
        or claim_payload.get("external_dispatch_key") != before["external_dispatch_key"]
        or auth_payload.get("external_dispatch_key") != before["external_dispatch_key"]
        or lookup_payload.get("external_dispatch_key") != before["external_dispatch_key"]
    ):
        raise V03LaunchCancelLiveError("second Operation did not reuse the exact cancelled QA reservation")
    if (
        lookup_payload.get("lookup_state") != "LAUNCHED"
        or str(lookup_payload.get("receipt_id") or "") != str(gateway.returned_receipt["receipt_id"])
    ):
        raise V03LaunchCancelLiveError("post-cancel durable lookup differs from real QA launch receipt")
    claim_sequence = int(claims[0]["sequence"])
    authorization_sequence = int(authorizations[0]["sequence"])
    cancel_sequence = int(cancellations[0]["sequence"])
    lookup_sequence = int(lookups[0]["sequence"])
    if not claim_sequence < authorization_sequence < cancel_sequence < lookup_sequence:
        raise V03LaunchCancelLiveError("durable ordering is not claim -> authorization -> cancel -> lookup")
    if _persist_after_sequence(events, cancel_sequence) != 0:
        raise V03LaunchCancelLiveError("authorization-before-cancel gained Persist authority after cancellation")

    after = {
        "status": "PASS",
        "scenario": PAIR_SCENARIOS[1],
        "operation_id": operation_id,
        "operation_generation": int(projection["generation"]),
        "semantic_effect_key": before["semantic_effect_key"],
        "external_dispatch_key": before["external_dispatch_key"],
        "candidate_head_sha": before["candidate_head_sha"],
        "feature_revision_before": int(before["feature_revision_before"]),
        "runtime_lookup_state": "LAUNCHED",
        "runtime_receipt_identity": str(lookup_payload["receipt_id"]),
        "final_status": "CANCELLED",
        "dispatch_claim_count": 1,
        "launch_authorization_count": 1,
        "launch_lookup_count": 1,
        "external_runtime_execution_count": 1,
        "post_cancel_persist_authority_count": 0,
        "claim_sequence": claim_sequence,
        "authorization_sequence": authorization_sequence,
        "cancel_sequence": cancel_sequence,
        "lookup_sequence": lookup_sequence,
        "measurements": {
            "duplicate_external_effect_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        "overall_issue_221_pass": False,
    }
    before_detail = {
        key: value
        for key, value in before.items()
        if key not in {"schema_version", "overall_issue_221_pass"}
    }
    pair = {
        "schema_version": "ai-sdlc.v03-live-launch-cancel-pair/v1",
        "status": "PASS",
        "completed_issue_221_scenarios": list(PAIR_SCENARIOS),
        "before_authorization": before_detail,
        "after_authorization": after,
        "overall_issue_221_pass": False,
    }
    _write_json(pair_path, pair)

    run_id = github_workflow_run_id if github_workflow_run_id is not None else os.environ["GITHUB_RUN_ID"]
    sha = workflow_sha if workflow_sha is not None else os.environ["GITHUB_SHA"]
    authority_document, provenance_document = envelope_writer(
        preflight=preflight,
        evidence_path=pair_path,
        provenance_path=provenance_path,
        authority_path=authority_path,
        github_workflow_run_id=run_id,
        workflow_sha=sha,
        record_id=None,
    )
    raw = pair_path.read_bytes()
    authority = ReleaseAuthority.from_document(authority_document)
    ledger = ledger_evaluator(
        authority=authority,
        evidence=[(raw, json.loads(raw.decode("utf-8")), provenance_document)],
    )
    if ledger.get("status") != "PENDING" or ledger.get("satisfied_scenarios") != list(PAIR_SCENARIOS):
        raise V03LaunchCancelLiveError("launch/cancel pair partial ledger satisfied unexpected Issue #221 rows")
    if len(ledger.get("unresolved_scenarios") or []) != 11 or ledger.get("overall_issue_221_pass") is not False:
        raise V03LaunchCancelLiveError("launch/cancel pair partial ledger overclaimed Issue #221 completion")
    _write_json(ledger_path, ledger)
    return pair


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("before-authorization", "after-authorization-finalize"))
    args = parser.parse_args()
    preflight = assemble_live_preflight(env=os.environ, checkout_sha=os.environ["GITHUB_SHA"])
    if args.phase == "before-authorization":
        print(json.dumps(run_cancel_before_authorization(preflight=preflight), indent=2, sort_keys=True))
        return
    print(json.dumps(run_authorized_before_cancel(preflight=preflight), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
