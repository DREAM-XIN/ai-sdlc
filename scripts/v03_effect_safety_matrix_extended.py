#!/usr/bin/env python3
"""Additional deterministic support scenarios for the v0.3 effect-safety matrix."""
from __future__ import annotations

from operator_store import StoreCommandError, plan_launch_lookup, plan_needs_user
from operator_store_git import CasConflict, MemoryStateRefBackend
from operator_store_model import operation_events
from operator_store_protection import PROTECTED, ProtectionReceipt
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE
from operator_vertical_recovery import plan_vertical_callback_record, plan_vertical_takeover
from operator_vertical_store import (
    plan_vertical_persist_confirmed,
    plan_vertical_persist_linearized,
    plan_vertical_persist_requested,
    vertical_projection,
)
from v03_effect_safety_matrix import (
    HEAD_A,
    NOW,
    REPOSITORY,
    REVISION,
    TARGET_REF,
    _apply,
    _authorize,
    _reserve_claim,
    _start,
    _support,
    run_deterministic_support_matrix,
)

STATE_REF = "refs/heads/ai-sdlc-operator-state"


def _trusted_context(operation_id: str, reservation: dict, *, generation: int = 0) -> TrustedDispatchContext:
    return TrustedDispatchContext(
        operation_id=operation_id,
        operation_generation=generation,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=reservation["semantic_effect_key"],
        external_dispatch_key=reservation["external_dispatch_key"],
        dispatch_id="matrix-dispatch",
        runtime_receipt_identity="matrix-run",
        target_repository=REPOSITORY,
        target_ref=TARGET_REF,
        feature_id="F-V03-EFFECT-SAFETY-MATRIX",
        expected_revision=REVISION,
        feature_stage="implementation",
        task_id="matrix:implementation:10",
        role="developer",
        candidate_pr_number=1,
        candidate_head_sha=HEAD_A,
        worker_identity="matrix-worker",
        collector_identity="matrix-collector",
    )


def scenario_out_of_order_callback():
    snapshot, operation_id = _start()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = _authorize(snapshot, operation_id, claim)
    snapshot = _apply(
        snapshot,
        plan_launch_lookup(
            snapshot,
            operation_id=operation_id,
            generation=0,
            external_dispatch_key_value=reservation["external_dispatch_key"],
            lookup_state="LAUNCHED",
            receipt_id="matrix-run",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        "s-launched",
    )
    stale_context = _trusted_context(operation_id, reservation, generation=0)
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
    if vertical_projection(snapshot, operation_id)["generation"] != 1:
        raise AssertionError("takeover did not advance generation")
    try:
        plan_vertical_callback_record(
            snapshot,
            context=stale_context,
            callback_id="matrix-stale-generation-callback",
            worker_payload={"status": "COMPLETED", "summary": "late", "outputs": []},
            receipts=[],
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        )
        raise AssertionError("superseded-generation callback unexpectedly entered durable Store")
    except StoreCommandError as exc:
        if exc.code != "SUPERSEDED_GENERATION":
            raise
    callbacks = [event for event in operation_events(snapshot, operation_id) if event["event_type"] == "worker.callback.recorded"]
    if callbacks:
        raise AssertionError("out-of-order stale callback created a durable callback fact")
    return _support(
        "out-of-order-callback",
        "callback from superseded generation rejected before durable record",
        "no stale callback fact appended",
        remaining=(
            "deliver a late real collector callback after trusted takeover and prove no translation/Persist occurs",
        ),
    )


def scenario_concurrent_resume():
    snapshot, operation_id = _start()
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF, snapshot=snapshot)
    receipt = ProtectionReceipt(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        status=PROTECTED,
        verifier_identity="matrix-protection",
        verified_at=NOW,
        policy_digest="matrix-policy",
    )

    # Two runners read the same durable head and independently select the same
    # stable stop. The stale second commit must lose the CAS race.
    read_a = backend.read_snapshot()
    read_b = backend.read_snapshot()
    plan_a = plan_needs_user(
        read_a,
        operation_id=operation_id,
        generation=0,
        reason_code="MATRIX_CONCURRENT_RESUME",
        summary="same stable stop",
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    plan_b = plan_needs_user(
        read_b,
        operation_id=operation_id,
        generation=0,
        reason_code="MATRIX_CONCURRENT_RESUME",
        summary="same stable stop",
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    backend.commit(plan_a, receipt)
    try:
        backend.commit(plan_b, receipt)
        raise AssertionError("second concurrent resume unexpectedly committed against stale ref")
    except CasConflict:
        pass

    # Re-plan on the current head converges to the same deterministic event
    # identity instead of appending a second semantic stop.
    backend.commit_replanned(
        lambda current: plan_needs_user(
            current,
            operation_id=operation_id,
            generation=0,
            reason_code="MATRIX_CONCURRENT_RESUME",
            summary="same stable stop",
            occurred_at=NOW,
            trusted_context_digest="matrix-trusted",
        ),
        receipt,
    )
    durable = backend.read_snapshot()
    stops = [
        event for event in operation_events(durable, operation_id)
        if event["event_type"] == "operation.needs-user"
        and (event.get("payload") or {}).get("reason_code") == "MATRIX_CONCURRENT_RESUME"
    ]
    if len(stops) != 1:
        raise AssertionError(f"concurrent resume created {len(stops)} durable stable-stop events")
    return _support(
        "concurrent-resume",
        "stale concurrent Store write rejected by CAS",
        "re-plan converges to one deterministic durable event identity",
        remaining=(
            "race two real resume runners against the protected shared state ref and prove one external/lifecycle effect",
        ),
    )


class _ExactPersistGateway:
    def __init__(self):
        self.events: dict[str, dict] = {}
        self.persist_calls = 0
        self.lookup_calls = 0

    def persist_feature_event(self, *, event: dict, target_ref: str) -> dict:
        self.persist_calls += 1
        event_id = str(event["id"])
        existing = self.events.get(event_id)
        if existing is not None and existing != event:
            raise AssertionError("conflicting exact Feature Event identity")
        self.events[event_id] = dict(event)
        return {"event_id": event_id, "target_ref": target_ref, "result_revision": REVISION + 1}

    def lookup_feature_event(self, *, event_id: str, target_ref: str) -> dict | None:
        self.lookup_calls += 1
        event = self.events.get(event_id)
        if event is None:
            return None
        return {"event_id": event_id, "target_ref": target_ref, "result_revision": REVISION + 1}


def scenario_persist_ack_loss_recovery():
    snapshot, operation_id = _start()
    event_id = "EVT-MATRIX-PERSIST-ACK-LOSS"
    event = {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": "F-V03-EFFECT-SAFETY-MATRIX",
        "expected_revision": REVISION,
        "occurred_at": NOW,
        "changes": [{"kind": "stage", "id": "implementation", "status": "DONE"}],
    }
    common = dict(
        operation_id=operation_id,
        generation=0,
        feature_event_id=event_id,
        expected_revision=REVISION,
        target_ref=TARGET_REF,
        candidate_head_sha=HEAD_A,
        occurred_at=NOW,
        trusted_context_digest="matrix-trusted",
    )
    snapshot = _apply(snapshot, plan_vertical_persist_requested(snapshot, **common), "s-persist-request")
    snapshot = _apply(snapshot, plan_vertical_persist_linearized(snapshot, **common), "s-persist-linearized")

    gateway = _ExactPersistGateway()
    _discarded_ack = gateway.persist_feature_event(event=event, target_ref=TARGET_REF)
    # Simulated process crash: local Store has no persist.confirmed yet. A fresh
    # reconciler MUST query the exact event id before contemplating another write.
    projection = vertical_projection(snapshot, operation_id)
    if event_id in projection["confirmed_persists"]:
        raise AssertionError("lost-ACK fixture unexpectedly confirmed Persist locally")
    recovered = gateway.lookup_feature_event(event_id=event_id, target_ref=TARGET_REF)
    if recovered is None or recovered["event_id"] != event_id:
        raise AssertionError("fresh Persist reconciliation did not recover exact Event receipt")
    snapshot = _apply(
        snapshot,
        plan_vertical_persist_confirmed(
            snapshot,
            result_revision=int(recovered["result_revision"]),
            **common,
        ),
        "s-persist-confirmed",
    )
    projection = vertical_projection(snapshot, operation_id)
    if event_id not in projection["confirmed_persists"] or projection["expected_feature_revision"] != REVISION + 1:
        raise AssertionError("exact recovered Persist receipt did not confirm once")
    if gateway.persist_calls != 1 or gateway.lookup_calls != 1:
        raise AssertionError("Persist ACK-loss recovery retried write instead of exact receipt lookup")
    return _support(
        "persist-ack-loss-recovery",
        "external Feature Event write succeeded while local acknowledgement was discarded",
        "fresh recovery queried exact Feature Event identity before any retry",
        "exact recovered receipt confirmed one revision advance",
        remaining=(
            "repeat against the real trusted Feature Event Persist gateway and target branch, proving one persisted lifecycle transition",
        ),
    )


EXTRA_SCENARIOS = (
    scenario_persist_ack_loss_recovery,
    scenario_out_of_order_callback,
    scenario_concurrent_resume,
)


def run_complete_deterministic_support_matrix() -> dict:
    matrix = run_deterministic_support_matrix()
    for scenario_fn in EXTRA_SCENARIOS:
        row = scenario_fn()
        matrix["scenarios"][row.scenario] = {
            "scenario": row.scenario,
            "evidence_level": row.evidence_level,
            "status": row.status,
            "release_eligible": row.release_eligible,
            "duplicate_external_effect_count": row.duplicate_external_effect_count,
            "unauthorized_lifecycle_transition_count": row.unauthorized_lifecycle_transition_count,
            "stale_evidence_accepted_count": row.stale_evidence_accepted_count,
            "speculative_retry_under_unknown_count": row.speculative_retry_under_unknown_count,
            "assertions": list(row.assertions),
            "remaining_release_proof": list(row.remaining_release_proof),
        }
    missing = [
        scenario for scenario in matrix["release_required_scenarios"]
        if scenario not in matrix["scenarios"]
    ]
    matrix["release_scenarios_without_deterministic_support_yet"] = missing
    matrix["aggregate"] = {
        key: sum(int(row[key]) for row in matrix["scenarios"].values())
        for key in (
            "duplicate_external_effect_count",
            "unauthorized_lifecycle_transition_count",
            "stale_evidence_accepted_count",
            "speculative_retry_under_unknown_count",
        )
    }
    return matrix


if __name__ == "__main__":
    import json
    print(json.dumps(run_complete_deterministic_support_matrix(), indent=2, sort_keys=True))
