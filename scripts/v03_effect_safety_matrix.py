#!/usr/bin/env python3
"""Executable v0.3 effect-safety fault matrix.

This module separates deterministic support evidence from real-runtime release
evidence. Deterministic scenarios may prove control invariants and harness logic,
but they NEVER become release-eligible merely by passing locally.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

from operator_store import (
    StoreCommandError,
    plan_authorize_launch,
    plan_cancel,
    plan_dispatch_claim,
    plan_launch_lookup,
    plan_operation_start,
)
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, operation_events, rebuild_projection
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_recovery import plan_vertical_callback_record, plan_vertical_takeover
from operator_vertical_store import (
    plan_vertical_persist_confirmed,
    plan_vertical_persist_linearized,
    plan_vertical_persist_requested,
    plan_vertical_semantic_reservation,
    vertical_projection,
)

NOW = "2026-08-11T04:15:00Z"
REPOSITORY = "DREAM-XIN/ai-sdlc"
FEATURE_ID = "F-V03-EFFECT-SAFETY-MATRIX"
TARGET_REF = "feature/F-V03-EFFECT-SAFETY-MATRIX"
HEAD_A = "a" * 40
HEAD_B = "b" * 40
REVISION = 10

RELEASE_REQUIRED_SCENARIOS = (
    "lost-ack-crash-takeover",
    "cancel-before-launch-authorization",
    "launch-authorized-before-cancel",
    "cancel-before-persist-linearization",
    "persist-linearized-before-cancel",
    "persist-ack-loss-recovery",
    "unknown-takeover",
    "duplicate-callback",
    "out-of-order-callback",
    "concurrent-resume",
    "stale-candidate-result",
)


@dataclass(frozen=True)
class ScenarioEvidence:
    scenario: str
    evidence_level: str
    status: str
    release_eligible: bool
    duplicate_external_effect_count: int
    unauthorized_lifecycle_transition_count: int
    stale_evidence_accepted_count: int
    speculative_retry_under_unknown_count: int
    assertions: tuple[str, ...]
    remaining_release_proof: tuple[str, ...] = ()


def _apply(snapshot: StoreSnapshot, plan, sha: str) -> StoreSnapshot:
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=sha)


def _start() -> tuple[StoreSnapshot, str]:
    snapshot = StoreSnapshot(ref_sha="s0")
    plan = plan_operation_start(
        snapshot,
        target_repository=REPOSITORY,
        feature_id=FEATURE_ID,
        expected_revision=REVISION,
        idempotency_key="matrix-operation",
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    return _apply(snapshot, plan, "s1"), plan.result["operation_id"]


def _reserve_claim(
    snapshot: StoreSnapshot,
    operation_id: str,
    *,
    candidate_head_sha: str = HEAD_A,
    stage: str = "implementation",
    role: str = "developer",
    task_identity: str = "matrix:implementation:10",
) -> tuple[StoreSnapshot, dict, dict]:
    reservation = plan_vertical_semantic_reservation(
        snapshot,
        operation_id=operation_id,
        generation=vertical_projection(snapshot, operation_id)["generation"],
        target_repository=REPOSITORY,
        feature_id=FEATURE_ID,
        expected_revision=vertical_projection(snapshot, operation_id)["expected_feature_revision"],
        current_stage=stage,
        task_identity=task_identity,
        role=role,
        candidate_head_sha=candidate_head_sha,
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    snapshot = _apply(snapshot, reservation, "s-reserve")
    generation = vertical_projection(snapshot, operation_id)["generation"]
    claim = plan_dispatch_claim(
        snapshot,
        operation_id=operation_id,
        generation=generation,
        effect_key=reservation.result["semantic_effect_key"],
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    snapshot = _apply(snapshot, claim, "s-claim")
    return snapshot, reservation.result, claim.result


def _authorize(
    snapshot: StoreSnapshot,
    operation_id: str,
    claim: dict,
    *,
    candidate_head_sha: str = HEAD_A,
    stage: str = "implementation",
    dispatch_id: str = "matrix-dispatch",
) -> StoreSnapshot:
    projection = vertical_projection(snapshot, operation_id)
    plan = plan_authorize_launch(
        snapshot,
        operation_id=operation_id,
        generation=projection["generation"],
        claim_id=claim["claim_id"],
        dispatch_id=dispatch_id,
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
        verified_expected_revision=projection["expected_feature_revision"],
        verified_stage=stage,
        verified_candidate_head_sha=candidate_head_sha,
    )
    return _apply(snapshot, plan, "s-authorized")


def _support(
    scenario: str,
    *assertions: str,
    remaining: tuple[str, ...],
) -> ScenarioEvidence:
    return ScenarioEvidence(
        scenario=scenario,
        evidence_level="deterministic-support",
        status="PASS",
        release_eligible=False,
        duplicate_external_effect_count=0,
        unauthorized_lifecycle_transition_count=0,
        stale_evidence_accepted_count=0,
        speculative_retry_under_unknown_count=0,
        assertions=tuple(assertions),
        remaining_release_proof=remaining,
    )


def scenario_cancel_before_launch_authorization() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = _apply(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="matrix-cancel-before-launch",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-cancel",
    )
    try:
        _authorize(snapshot, operation_id, claim)
        raise AssertionError("cancel-before-launch unexpectedly authorized external dispatch")
    except StoreCommandError as exc:
        if exc.code != "CANCELLED_OPERATION":
            raise
    projection = rebuild_projection(snapshot, operation_id)
    if reservation["external_dispatch_key"] in projection["authorized_dispatches"]:
        raise AssertionError("cancel-before-launch left an authorized dispatch")
    return _support(
        "cancel-before-launch-authorization",
        "cancel durable before launch authorization",
        "subsequent launch authorization rejected",
        "external dispatch key never entered authorized_dispatches",
        remaining=(
            "run against live protected Store and verify zero matching supported-runtime executions",
        ),
    )


def scenario_launch_authorized_before_cancel() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = _authorize(snapshot, operation_id, claim)
    key = reservation["external_dispatch_key"]
    snapshot = _apply(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="matrix-cancel-after-launch-authorization",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-cancel",
    )
    projection = rebuild_projection(snapshot, operation_id)
    if key not in projection["authorized_dispatches"]:
        raise AssertionError("cancellation erased a previously linearized launch authorization")

    # Only exact post-linearization observation is still allowed after cancel.
    snapshot = _apply(
        snapshot,
        plan_launch_lookup(
            snapshot,
            operation_id=operation_id,
            generation=projection["generation"],
            external_dispatch_key_value=key,
            lookup_state="LAUNCHED",
            receipt_id="matrix-runtime-receipt",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-lookup",
    )
    try:
        plan_dispatch_claim(
            snapshot,
            operation_id=operation_id,
            generation=projection["generation"],
            effect_key=reservation["semantic_effect_key"],
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        )
        raise AssertionError("cancelled operation unexpectedly created/reused dispatch claim for new processing")
    except StoreCommandError as exc:
        if exc.code != "CANCELLED_OPERATION":
            raise
    return _support(
        "launch-authorized-before-cancel",
        "pre-cancel launch authorization remains durable",
        "exact authorized receipt observation remains recordable after cancellation",
        "cancelled operation rejects new dispatch processing",
        remaining=(
            "launch one real supported-runtime execution before cancellation and prove exactly one exact-key run",
            "prove eventual Worker result gains no automatic Feature Persist authority",
        ),
    )


def scenario_cancel_before_persist_linearization() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    snapshot = _apply(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="matrix-cancel-before-persist",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-cancel",
    )
    try:
        plan_vertical_persist_requested(
            snapshot,
            operation_id=operation_id,
            generation=0,
            feature_event_id="EVT-MATRIX-CANCEL-BEFORE-PERSIST",
            expected_revision=REVISION,
            target_ref=TARGET_REF,
            candidate_head_sha=HEAD_A,
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        )
        raise AssertionError("cancel-before-persist unexpectedly accepted Persist request")
    except StoreCommandError as exc:
        if exc.code != "CANCELLED_OPERATION":
            raise
    return _support(
        "cancel-before-persist-linearization",
        "cancelled operation rejects Persist request before linearization",
        remaining=(
            "exercise trusted Feature Persist gateway on live protected Store and prove zero Feature writes",
        ),
    )


def scenario_persist_linearized_before_cancel() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    common = dict(
        operation_id=operation_id,
        generation=0,
        feature_event_id="EVT-MATRIX-PERSIST-LINEARIZED",
        expected_revision=REVISION,
        target_ref=TARGET_REF,
        candidate_head_sha=HEAD_A,
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    snapshot = _apply(snapshot, plan_vertical_persist_requested(snapshot, **common), "s-request")
    snapshot = _apply(snapshot, plan_vertical_persist_linearized(snapshot, **common), "s-linearized")
    snapshot = _apply(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="matrix-cancel-after-persist-linearized",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-cancel",
    )
    snapshot = _apply(
        snapshot,
        plan_vertical_persist_confirmed(snapshot, result_revision=REVISION + 1, **common),
        "s-confirmed",
    )
    projection = vertical_projection(snapshot, operation_id)
    if common["feature_event_id"] not in projection["confirmed_persists"]:
        raise AssertionError("exact pre-cancel linearized Persist could not be confirmed")
    if projection["expected_feature_revision"] != REVISION + 1:
        raise AssertionError("confirmed exact Persist did not advance vertical revision fence")
    return _support(
        "persist-linearized-before-cancel",
        "exact pre-cancel Persist linearization survives cancellation",
        "only exact linearized Feature Event confirmation advances revision fence",
        remaining=(
            "execute exact trusted Feature Event write on live target and prove no subsequent automatic progression",
        ),
    )


def scenario_unknown_takeover() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = _authorize(snapshot, operation_id, claim)
    key = reservation["external_dispatch_key"]
    snapshot = _apply(
        snapshot,
        plan_launch_lookup(
            snapshot,
            operation_id=operation_id,
            generation=0,
            external_dispatch_key_value=key,
            lookup_state="UNKNOWN",
            receipt_id=None,
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-unknown",
    )
    before = vertical_projection(snapshot, operation_id)
    if before["status"] != "BLOCKED" or key not in before["unresolved_unknown"]:
        raise AssertionError("UNKNOWN launch did not fail closed")
    snapshot = _apply(
        snapshot,
        plan_vertical_takeover(
            snapshot,
            operation_id=operation_id,
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-takeover",
    )
    after = vertical_projection(snapshot, operation_id)
    if after["generation"] != 1 or after["status"] != "BLOCKED" or key not in after["unresolved_unknown"]:
        raise AssertionError("UNKNOWN state did not survive generation takeover")
    return _support(
        "unknown-takeover",
        "UNKNOWN launch blocks operation",
        "generation takeover preserves the same unresolved external dispatch key",
        remaining=(
            "induce a real trusted runtime lookup UNKNOWN after durable launch authorization and prove no speculative second run",
        ),
    )


def scenario_duplicate_callback() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = _authorize(snapshot, operation_id, claim)
    key = reservation["external_dispatch_key"]
    snapshot = _apply(
        snapshot,
        plan_launch_lookup(
            snapshot,
            operation_id=operation_id,
            generation=0,
            external_dispatch_key_value=key,
            lookup_state="LAUNCHED",
            receipt_id="matrix-run",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-launched",
    )
    context = {
        "operation_id": operation_id,
        "operation_generation": 0,
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": reservation["semantic_effect_key"],
        "external_dispatch_key": key,
        "dispatch_id": "matrix-dispatch",
        "runtime_receipt_identity": "matrix-run",
        "target_repository": REPOSITORY,
        "target_ref": TARGET_REF,
        "feature_id": FEATURE_ID,
        "expected_revision": REVISION,
        "feature_stage": "implementation",
        "task_id": "matrix:implementation:10",
        "role": "developer",
        "candidate_pr_number": 1,
        "candidate_head_sha": HEAD_A,
        "worker_identity": "matrix-worker",
        "collector_identity": "matrix-collector",
    }
    from operator_vertical import TrustedDispatchContext
    trusted = TrustedDispatchContext(**context)
    payload = {"status": "COMPLETED", "summary": "matrix", "outputs": []}
    first = plan_vertical_callback_record(
        snapshot,
        context=trusted,
        callback_id="matrix-callback",
        worker_payload=payload,
        receipts=[],
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    snapshot = _apply(snapshot, first, "s-callback-1")
    count_before = sum(1 for event in operation_events(snapshot, operation_id) if event["event_type"] == "worker.callback.recorded")
    duplicate = plan_vertical_callback_record(
        snapshot,
        context=trusted,
        callback_id="matrix-callback",
        worker_payload=payload,
        receipts=[],
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    snapshot = _apply(snapshot, duplicate, "s-callback-2")
    count_after = sum(1 for event in operation_events(snapshot, operation_id) if event["event_type"] == "worker.callback.recorded")
    if count_before != 1 or count_after != 1:
        raise AssertionError("exact duplicate callback created a second durable callback fact")
    return _support(
        "duplicate-callback",
        "exact duplicate callback converges on one immutable callback fact",
        remaining=(
            "deliver duplicate callback through the real trusted collector/gateway and prove one lifecycle translation/Persist at most",
        ),
    )


def scenario_stale_candidate_result() -> ScenarioEvidence:
    snapshot, operation_id = _start()
    snapshot, _, claim = _reserve_claim(
        snapshot,
        operation_id,
        candidate_head_sha=HEAD_A,
        stage="code-review",
        role="reviewer",
        task_identity="matrix:code-review:" + HEAD_A,
    )
    try:
        _authorize(
            snapshot,
            operation_id,
            claim,
            candidate_head_sha=HEAD_B,
            stage="code-review",
            dispatch_id="matrix-stale-candidate",
        )
        raise AssertionError("stale candidate head unexpectedly authorized reviewer dispatch")
    except StoreCommandError as exc:
        if exc.code != "STALE_REVISION":
            raise
    return _support(
        "stale-candidate-result",
        "candidate-bound reservation rejects mismatched verified candidate before launch linearization",
        remaining=(
            "change a real PR head after candidate-bound work and prove stale Reviewer/QA result cannot persist Feature state",
        ),
    )


DETERMINISTIC_SCENARIOS: tuple[Callable[[], ScenarioEvidence], ...] = (
    scenario_cancel_before_launch_authorization,
    scenario_launch_authorized_before_cancel,
    scenario_cancel_before_persist_linearization,
    scenario_persist_linearized_before_cancel,
    scenario_unknown_takeover,
    scenario_duplicate_callback,
    scenario_stale_candidate_result,
)


def run_deterministic_support_matrix() -> dict:
    rows = [scenario() for scenario in DETERMINISTIC_SCENARIOS]
    by_id = {row.scenario: asdict(row) for row in rows}
    missing = [scenario for scenario in RELEASE_REQUIRED_SCENARIOS if scenario not in by_id]
    return {
        "schema_version": "ai-sdlc.real-runtime-effect-safety-matrix/v1",
        "evidence_kind": "deterministic-support",
        "release_eligible": False,
        "scenarios": by_id,
        "release_required_scenarios": list(RELEASE_REQUIRED_SCENARIOS),
        "release_scenarios_without_deterministic_support_yet": missing,
        "aggregate": {
            "duplicate_external_effect_count": sum(row.duplicate_external_effect_count for row in rows),
            "unauthorized_lifecycle_transition_count": sum(row.unauthorized_lifecycle_transition_count for row in rows),
            "stale_evidence_accepted_count": sum(row.stale_evidence_accepted_count for row in rows),
            "speculative_retry_under_unknown_count": sum(row.speculative_retry_under_unknown_count for row in rows),
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_deterministic_support_matrix(), indent=2, sort_keys=True))
