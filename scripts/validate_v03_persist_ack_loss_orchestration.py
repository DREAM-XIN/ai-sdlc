#!/usr/bin/env python3
"""Two-process deterministic orchestration for Persist ACK-loss recovery."""
from __future__ import annotations

from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_events, operation_id_for
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import select_vertical_action
from operator_vertical_store import vertical_projection
from v03_real_runtime_fault_injection import InjectedPersistRunnerCrash, LostAckCrashAfterPersistGateway
from validate_v03_persist_cancel_orchestration import PlainFeatureGateway, _make_executor
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    REF,
    REPOSITORY,
    Clock,
    make_bundle,
    make_runtime,
    manifest,
)

STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _events(backend, operation_id, event_type):
    return [
        event
        for event in operation_events(backend.read_snapshot(), operation_id)
        if event["event_type"] == event_type
    ]


def _start_request(idempotency_key):
    return {
        "idempotency_key": idempotency_key,
        "target": {"repository": REPOSITORY, "feature_id": FEATURE},
        "context": {"expected_feature_revision": 11},
    }


def _trusted_context(bundle):
    return bundle.write_bundle.read_bundle.trusted_context_provider.for_request(
        {"repository": REPOSITORY, "feature_id": FEATURE}
    )


def _expected_event_id(fixture):
    feature = FeatureSnapshot.from_manifest(
        repository=REPOSITORY,
        target_ref=REF,
        manifest=fixture,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
    )
    action = select_vertical_action(
        feature=feature,
        manifest=fixture,
        occurred_at="2026-08-11T00:00:00Z",
    )
    require(action.kind == "persist" and isinstance(action.feature_event, dict), "Persist ACK-loss fixture is not persist-ready")
    return str(action.feature_event["id"])


class ExternalFeatureStore:
    def __init__(self):
        self.events = {}
        self.write_count = 0


class ExactPersistDelegate:
    def __init__(self, external):
        self.external = external
        self.persist_calls = 0
        self.lookup_calls = 0

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls += 1
        event_id = str(event["id"])
        existing = self.external.events.get(event_id)
        require(existing is None or existing == event, "external Feature Store saw conflicting exact Event identity")
        if existing is None:
            self.external.write_count += 1
            self.external.events[event_id] = dict(event)
        return {"event_id": event_id, "target_ref": target_ref, "result_revision": 12}

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        event = self.external.events.get(str(event_id))
        if event is None:
            return None
        return {"event_id": str(event_id), "target_ref": target_ref, "result_revision": 12}


class LookupOnlyRecoveryPersistGateway:
    def __init__(self, external, expected_event_id):
        self.external = external
        self.expected_event_id = expected_event_id
        self.lookup_calls = 0
        self.persist_calls = 0

    def lookup_feature_event(self, *, event_id, target_ref):
        require(str(event_id) == self.expected_event_id and target_ref == REF, "fresh recovery lookup lost exact Event/ref binding")
        self.lookup_calls += 1
        event = self.external.events.get(self.expected_event_id)
        require(event is not None, "modeled external Feature Event disappeared before recovery")
        return {"event_id": self.expected_event_id, "target_ref": target_ref, "result_revision": 12}

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls += 1
        raise AssertionError("fresh Persist recovery retried external write after exact receipt lookup succeeded")


def main():
    fixture = manifest(current_stage="code-review", stage_status="READY")
    idempotency = "fi-persist-ack-loss-recovery"
    operation_id = operation_id_for(REPOSITORY, FEATURE, idempotency)
    event_id = _expected_event_id(fixture)
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    feature_gateway = PlainFeatureGateway(fixture)
    external = ExternalFeatureStore()

    phase1_delegate = ExactPersistDelegate(external)
    phase1_fault = LostAckCrashAfterPersistGateway(
        delegate=phase1_delegate,
        expected_feature_event_id=event_id,
        expected_target_ref=REF,
    )
    runtime_g0 = make_runtime(backend, clock)
    executor_g0 = _make_executor(runtime_g0, feature_gateway, phase1_fault)
    bundle_g0 = make_bundle(runtime_g0, executor_g0)
    try:
        bundle_g0.backends["operation.start"].invoke(_start_request(idempotency), _trusted_context(bundle_g0))
    except InjectedPersistRunnerCrash as crash:
        require(crash.feature_event_id == event_id and crash.target_ref == REF, "Persist crash signal lost exact Event/ref binding")
    else:
        raise AssertionError("authoritative external Persist receipt did not trigger process-level ACK-loss crash")

    requested = _events(backend, operation_id, "persist.requested")
    linearized = _events(backend, operation_id, "persist.linearized")
    confirmed = _events(backend, operation_id, "persist.confirmed")
    require(len(requested) == 1 and len(linearized) == 1 and len(confirmed) == 0, "Phase 1 crash window is not requested+linearized+unconfirmed")
    require((requested[0].get("payload") or {}).get("feature_event_id") == event_id, "durable Persist request Event identity drifted")
    require((linearized[0].get("payload") or {}).get("feature_event_id") == event_id, "durable Persist linearization Event identity drifted")
    require(phase1_fault.injected is True, "Persist ACK-loss injector did not arm one-shot crash fence")
    require(phase1_delegate.persist_calls == 1 and phase1_delegate.lookup_calls == 0, "Phase 1 did not crash directly after one external write")
    require(external.write_count == 1 and event_id in external.events, "Phase 1 did not create exactly one modeled external Feature Event")

    # Fresh process/runtime: recovery gateway is lookup-only. Accepted reconciliation
    # must recover the exact receipt and confirm it without another external write.
    recovery_gateway = LookupOnlyRecoveryPersistGateway(external, event_id)
    runtime_g1 = make_runtime(backend, clock)
    executor_g1 = _make_executor(runtime_g1, feature_gateway, recovery_gateway)
    recovered = executor_g1._reconcile_persist(operation_id)
    require(recovered is True, "fresh Persist reconciliation did not consume exact external receipt")
    confirmed = _events(backend, operation_id, "persist.confirmed")
    require(len(confirmed) == 1, "fresh Persist reconciliation did not durably confirm exactly once")
    payload = confirmed[0].get("payload") or {}
    require(payload.get("feature_event_id") == event_id and payload.get("result_revision") == 12, "recovered Persist confirmation has wrong exact receipt")
    projection = vertical_projection(backend.read_snapshot(), operation_id)
    require(projection["expected_feature_revision"] == 12, "recovered exact receipt did not advance vertical revision fence")
    require(recovery_gateway.lookup_calls == 1 and recovery_gateway.persist_calls == 0, "fresh recovery did not use exact lookup-only convergence")
    require(external.write_count == 1, "fresh recovery created a duplicate external Feature write")

    # Re-running reconciliation after confirmation is idempotent and performs no
    # additional external lookup/write.
    again = executor_g1._reconcile_persist(operation_id)
    require(again is None, "already-confirmed Persist remained pending after recovery")
    require(recovery_gateway.lookup_calls == 1 and recovery_gateway.persist_calls == 0, "confirmed Persist touched external gateway again")

    print("v0.3 Persist ACK-loss fresh-process orchestration validation passed")
    print("- Phase 1 durably requests+linearizes, writes one exact Feature Event, then crashes before local acknowledgement")
    print("- fresh process performs exact Event lookup first, confirms result_revision=12, and executes zero second writes")
    print("- repeated reconciliation after confirmation is externally inert")
    print("- deterministic harness evidence only; real protected Store/Feature gateway proof remains required")
    return {
        "scenario_id": "persist-ack-loss-recovery",
        "operation_id": operation_id,
        "feature_event_id": event_id,
        "operation_generation": 0,
        "result_revision": 12,
        "external_feature_write_count": external.write_count,
        "fresh_lookup_count": recovery_gateway.lookup_calls,
        "fresh_retry_write_count": recovery_gateway.persist_calls,
    }


if __name__ == "__main__":
    main()
