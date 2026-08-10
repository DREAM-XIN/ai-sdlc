#!/usr/bin/env python3
"""Deterministic fault/replay validation for the trusted vertical recovery wrapper."""
from __future__ import annotations

from operator_store import (
    StoreCommandError,
    plan_authorize_launch,
    plan_cancel,
    plan_dispatch_claim,
    plan_launch_lookup,
    plan_operation_fact,
    plan_operation_start,
)
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, digest_json, operation_events
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from operator_vertical_recovery import derive_role_independence_policy, plan_vertical_callback_record
from operator_vertical_store import (
    plan_vertical_persist_linearized,
    plan_vertical_persist_requested,
    plan_vertical_semantic_reservation,
    vertical_projection,
)
from validate_operator_vertical import HEAD, NOW, REPO, FEATURE, _base_manifest, _feature

HEAD2 = "e" * 40


def _apply(snapshot, plan, index):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=f"s{index}")


def _authorized_without_lookup(*, candidate=HEAD, role="developer", stage="implementation", task_identity=None):
    snapshot = StoreSnapshot(ref_sha="s0")
    start = plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        idempotency_key=f"idem-{role}-{stage}",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = _apply(snapshot, start, 1)
    operation_id = start.result["operation_id"]
    identity = task_identity or (
        "vertical:implementation:10" if role == "developer" else f"vertical:code-review:{candidate}"
    )
    reserve = plan_vertical_semantic_reservation(
        snapshot,
        operation_id=operation_id,
        generation=0,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        current_stage=stage,
        task_identity=identity,
        role=role,
        candidate_head_sha=candidate,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, reserve, 2)
    effect_key = reserve.result["semantic_effect_key"]
    claim = plan_dispatch_claim(
        snapshot,
        operation_id=operation_id,
        generation=0,
        effect_key=effect_key,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, claim, 3)
    dispatch_id = "vertical-" + digest_json(
        {"operation_id": operation_id, "generation": 0, "semantic_effect_key": effect_key}
    )[:32]
    auth = plan_authorize_launch(
        snapshot,
        operation_id=operation_id,
        generation=0,
        claim_id=claim.result["claim_id"],
        dispatch_id=dispatch_id,
        occurred_at=NOW,
        trusted_context_digest="trusted",
        verified_expected_revision=10,
        verified_stage=stage,
        verified_candidate_head_sha=candidate,
    )
    snapshot = _apply(snapshot, auth, 4)
    return snapshot, operation_id, effect_key, claim.result["external_dispatch_key"], dispatch_id, identity


class Backend:
    repository = REPO
    state_ref = "refs/heads/ai-sdlc-operator-state"

    def __init__(self, snapshot):
        self.snapshot = snapshot

    def read_snapshot(self):
        return self.snapshot


class Runtime:
    def __init__(self, snapshot):
        self.backend = Backend(snapshot)
        self.sequence = 10

    def clock(self):
        return NOW

    def commit_replanned(self, planner):
        plan = planner(self.backend.snapshot)
        self.sequence += 1
        self.backend.snapshot = apply_plan_to_snapshot(
            self.backend.snapshot,
            plan,
            new_ref_sha=f"r{self.sequence}",
        )
        return plan


class FeatureGateway:
    def __init__(self, *, head=HEAD):
        self.manifest = _base_manifest()
        self.head = head

    def read_feature(self, *, operation_id):
        return _feature(self.manifest, head=self.head), self.manifest


class PersistGateway:
    def __init__(self, *, existing=None):
        self.existing = dict(existing or {})
        self.persisted = []

    def lookup_feature_event(self, *, event_id, target_ref):
        return self.existing.get(event_id)

    def persist_feature_event(self, *, event, target_ref):
        self.persisted.append((event, target_ref))
        receipt = {"event_id": event["id"], "result_revision": event["expected_revision"] + 1}
        self.existing[event["id"]] = receipt
        return receipt


class DispatchGateway:
    def __init__(self, lookup_state):
        self.lookup_state = lookup_state
        self.lookup_calls = []
        self.launch_calls = []

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(external_dispatch_key)
        return {"lookup_state": self.lookup_state, "receipt_id": None}

    def launch(self, *, dispatch):
        self.launch_calls.append(dict(dispatch))
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-retry"}


def _executor(snapshot, dispatch_gateway, *, persist_gateway=None, feature_gateway=None):
    runtime = Runtime(snapshot)
    feature_gateway = feature_gateway or FeatureGateway()
    persist_gateway = persist_gateway or PersistGateway()
    base = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=persist_gateway,
        dispatch_gateway=dispatch_gateway,
        config=TrustedVerticalExecutorConfig(
            target_ref="feature/test",
            trusted_context_digest="trusted",
            max_auto_steps=8,
        ),
    )
    wrapper = TrustedRecoveringVerticalExecutor(
        base_executor=base,
        content_loader=lambda uri: b"",
        trusted_role_policy="separated-role-identities/v1",
        collector_namespace_policy="feature-worker-runs/v1",
    )
    return wrapper, runtime, persist_gateway


def _context(operation_id, effect_key, external_key, dispatch_id, task_id, *, role="developer", candidate=HEAD, worker="worker-1"):
    return TrustedDispatchContext(
        operation_id=operation_id,
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=effect_key,
        external_dispatch_key=external_key,
        dispatch_id=dispatch_id,
        runtime_receipt_identity="runtime-1",
        target_repository=REPO,
        target_ref="feature/test",
        feature_id=FEATURE,
        expected_revision=10,
        feature_stage="implementation" if role == "developer" else "code-review",
        task_id=task_id,
        role=role,
        candidate_pr_number=1,
        candidate_head_sha=candidate,
        worker_identity=worker,
        collector_identity="collector-1",
    )


def validate_launch_ack_recovery_and_unknown_boundary():
    snapshot, operation_id, _, external_key, _, _ = _authorized_without_lookup()
    dispatch = DispatchGateway("NOT_LAUNCHED")
    executor, _, _ = _executor(snapshot, dispatch)
    result = executor.advance_until_stop(operation_id=operation_id)
    assert result["status"] == "WAITING_EXTERNAL"
    assert dispatch.lookup_calls == [external_key]
    assert len(dispatch.launch_calls) == 1
    assert dispatch.launch_calls[0]["external_dispatch_key"] == external_key

    snapshot, operation_id, _, external_key, _, _ = _authorized_without_lookup()
    dispatch = DispatchGateway("LAUNCHED")
    executor, _, _ = _executor(snapshot, dispatch)
    result = executor.advance_until_stop(operation_id=operation_id)
    assert result["status"] == "WAITING_EXTERNAL"
    assert dispatch.lookup_calls == [external_key]
    assert not dispatch.launch_calls

    snapshot, operation_id, _, external_key, _, _ = _authorized_without_lookup()
    dispatch = DispatchGateway("UNKNOWN")
    executor, _, _ = _executor(snapshot, dispatch)
    result = executor.advance_until_stop(operation_id=operation_id)
    assert result["status"] == "BLOCKED"
    assert dispatch.lookup_calls == [external_key]
    assert not dispatch.launch_calls
    result = executor.advance_until_stop(operation_id=operation_id)
    assert result["status"] == "BLOCKED"
    assert dispatch.lookup_calls == [external_key]


def validate_cancel_fences_missing_launch():
    snapshot, operation_id, _, external_key, _, _ = _authorized_without_lookup()
    snapshot = _apply(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="operator cancel",
            occurred_at=NOW,
            trusted_context_digest="trusted",
        ),
        5,
    )
    dispatch = DispatchGateway("NOT_LAUNCHED")
    executor, _, _ = _executor(snapshot, dispatch)
    result = executor.advance_until_stop(operation_id=operation_id)
    assert result["status"] == "CANCELLED"
    assert dispatch.lookup_calls == [external_key]
    assert not dispatch.launch_calls


def validate_callback_binding_and_replay():
    snapshot, operation_id, effect_key, external_key, dispatch_id, task_identity = _authorized_without_lookup()
    snapshot = _apply(
        snapshot,
        plan_launch_lookup(
            snapshot,
            operation_id=operation_id,
            generation=0,
            external_dispatch_key_value=external_key,
            lookup_state="LAUNCHED",
            receipt_id="run-1",
            occurred_at=NOW,
            trusted_context_digest="trusted",
        ),
        5,
    )
    context = _context(
        operation_id,
        effect_key,
        external_key,
        dispatch_id,
        task_identity,
        candidate=HEAD2,
        worker="developer-worker",
    )
    plan = plan_vertical_callback_record(
        snapshot,
        context=context,
        callback_id="callback-1",
        worker_payload={"status": "BLOCKED", "summary": "fixture stop", "outputs": []},
        receipts=[],
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, plan, 6)

    bad = _context(
        operation_id,
        effect_key,
        external_key,
        "wrong-dispatch",
        task_identity,
        candidate=HEAD2,
        worker="developer-worker",
    )
    try:
        plan_vertical_callback_record(
            snapshot,
            context=bad,
            callback_id="callback-bad",
            worker_payload={"status": "BLOCKED", "summary": "bad", "outputs": []},
            receipts=[],
            occurred_at=NOW,
            trusted_context_digest="trusted",
        )
        raise AssertionError("misbound callback unexpectedly accepted")
    except StoreCommandError as exc:
        assert exc.code == "STALE_REVISION"

    feature_gateway = FeatureGateway(head=HEAD2)
    dispatch = DispatchGateway("LAUNCHED")
    executor, runtime, _ = _executor(snapshot, dispatch, feature_gateway=feature_gateway)
    result = executor.advance_until_stop(operation_id=operation_id)
    assert result["status"] == "BLOCKED"
    events = operation_events(runtime.backend.read_snapshot(), operation_id)
    assert any(
        event["event_type"] == "worker.result.validated"
        and (event.get("payload") or {}).get("callback_id") == "callback-1"
        for event in events
    )
    policy = derive_role_independence_policy(runtime.backend.read_snapshot(), operation_id=operation_id)
    assert policy.developer_identity == "developer-worker"


def _translated_persist_snapshot(*, linearized: bool, cancelled: bool):
    snapshot = StoreSnapshot(ref_sha="p0")
    start = plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        idempotency_key=f"persist-{linearized}-{cancelled}",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = _apply(snapshot, start, 1)
    operation_id = start.result["operation_id"]
    event = {
        "version": "0.1.0",
        "id": "EVT-PERSIST-RECOVERY",
        "feature_id": FEATURE,
        "expected_revision": 10,
        "occurred_at": NOW,
        "changes": [{"kind": "stage", "id": "implementation", "status": "DONE"}],
    }
    feature = _feature(_base_manifest(), head=HEAD)
    translated = {
        "feature_event_id": event["id"],
        "feature_event_digest": digest_json(event),
        "feature_event": event,
        "feature_revision": feature.revision,
        "feature_stage": feature.current_stage,
        "feature_manifest_digest": feature.manifest_digest,
        "candidate_head_sha": feature.candidate_head_sha,
        "target_ref": feature.target_ref,
    }
    snapshot = _apply(
        snapshot,
        plan_operation_fact(
            snapshot,
            operation_id=operation_id,
            generation=0,
            event_type="feature.event.translated",
            payload=translated,
            occurred_at=NOW,
            trusted_context_digest="trusted",
        ),
        2,
    )
    common = dict(
        operation_id=operation_id,
        generation=0,
        feature_event_id=event["id"],
        expected_revision=10,
        target_ref="feature/test",
        candidate_head_sha=HEAD,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, plan_vertical_persist_requested(snapshot, **common), 3)
    if linearized:
        snapshot = _apply(snapshot, plan_vertical_persist_linearized(snapshot, **common), 4)
    if cancelled:
        snapshot = _apply(
            snapshot,
            plan_cancel(
                snapshot,
                operation_id=operation_id,
                reason="cancel around persist",
                occurred_at=NOW,
                trusted_context_digest="trusted",
            ),
            5,
        )
    return snapshot, operation_id, event


def validate_persist_linearization_recovery_and_cancel_order():
    snapshot, operation_id, event = _translated_persist_snapshot(linearized=True, cancelled=False)
    persist = PersistGateway()
    executor, runtime, _ = _executor(snapshot, DispatchGateway("LAUNCHED"), persist_gateway=persist)
    assert executor._reconcile_persist(operation_id) is True
    assert persist.persisted == [(event, "feature/test")]
    projection = vertical_projection(runtime.backend.read_snapshot(), operation_id)
    assert event["id"] in projection["confirmed_persists"]
    assert projection["expected_feature_revision"] == 11

    snapshot, operation_id, event = _translated_persist_snapshot(linearized=True, cancelled=False)
    persist = PersistGateway(existing={event["id"]: {"event_id": event["id"], "result_revision": 11}})
    executor, runtime, _ = _executor(snapshot, DispatchGateway("LAUNCHED"), persist_gateway=persist)
    assert executor._reconcile_persist(operation_id) is True
    assert not persist.persisted
    assert event["id"] in vertical_projection(runtime.backend.read_snapshot(), operation_id)["confirmed_persists"]

    snapshot, operation_id, _ = _translated_persist_snapshot(linearized=False, cancelled=True)
    persist = PersistGateway()
    executor, _, _ = _executor(snapshot, DispatchGateway("LAUNCHED"), persist_gateway=persist)
    assert executor._reconcile_persist(operation_id) is None
    assert not persist.persisted

    snapshot, operation_id, event = _translated_persist_snapshot(linearized=True, cancelled=True)
    persist = PersistGateway()
    executor, runtime, _ = _executor(snapshot, DispatchGateway("LAUNCHED"), persist_gateway=persist)
    assert executor._reconcile_persist(operation_id) is True
    assert persist.persisted == [(event, "feature/test")]
    assert vertical_projection(runtime.backend.read_snapshot(), operation_id)["status"] == "CANCELLED"


def main():
    validate_launch_ack_recovery_and_unknown_boundary()
    validate_cancel_fences_missing_launch()
    validate_callback_binding_and_replay()
    validate_persist_linearization_recovery_and_cancel_order()
    print("Operator vertical deterministic fault/replay validation passed")


if __name__ == "__main__":
    main()
