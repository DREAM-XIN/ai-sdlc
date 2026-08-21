#!/usr/bin/env python3
"""Coordinator-level deterministic orchestration for v0.3 callback safety scenarios."""
from __future__ import annotations

from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_events, rebuild_projection, reservation_path
from operator_vertical import FeatureSnapshot, TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_callback import TrustedVerticalCallbackCoordinator
from operator_vertical_controller import select_vertical_action
from operator_vertical_recovery import plan_vertical_takeover
from v03_real_runtime_lost_ack_orchestration import derive_lost_ack_dispatch_binding
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    REF,
    REPOSITORY,
    TRUSTED_DIGEST,
    Clock,
    make_bundle,
    make_executor,
    make_runtime,
    manifest,
)

STATE_REF = "refs/heads/ai-sdlc-operator-state"
CANDIDATE_B = "b" * 40


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


class MutableFeatureGateway:
    def __init__(self, manifest_doc, candidate=CANDIDATE):
        self.manifest = manifest_doc
        self.candidate = candidate

    def read_feature(self, *, operation_id):
        return (
            FeatureSnapshot.from_manifest(
                repository=REPOSITORY,
                target_ref=REF,
                manifest=self.manifest,
                candidate_pr_number=230,
                candidate_head_sha=self.candidate,
            ),
            self.manifest,
        )


class OneRunGateway:
    def __init__(self, receipt_id):
        self.receipt_id = receipt_id
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        key = str(dispatch["external_dispatch_key"])
        self.launch_calls.append(key)
        return {"lookup_state": "LAUNCHED", "receipt_id": self.receipt_id}

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(str(external_dispatch_key))
        return {"lookup_state": "LAUNCHED", "receipt_id": self.receipt_id}


def _setup_waiting_external(*, idempotency_key, candidate=CANDIDATE, receipt_id="run-callback-1"):
    fixture = manifest()
    binding = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        manifest=fixture,
        candidate_pr_number=230,
        candidate_head_sha=candidate,
        idempotency_key=idempotency_key,
        occurred_at="2026-08-11T00:00:00Z",
    )
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    feature_gateway = MutableFeatureGateway(fixture, candidate=candidate)
    dispatch_gateway = OneRunGateway(receipt_id)
    runtime = make_runtime(backend, clock)
    executor = make_executor(runtime, feature_gateway, dispatch_gateway)
    bundle = make_bundle(runtime, executor)
    result = bundle.backends["operation.start"].invoke(_start_request(idempotency_key), _trusted_context(bundle))
    require(result["status"] == "WAITING_EXTERNAL", "callback fixture did not stop at WAITING_EXTERNAL")
    require(dispatch_gateway.launch_calls == [binding.external_dispatch_key], "callback fixture launched wrong external key")

    authorizations = _events(backend, binding.operation_id, "dispatch.launch.authorized")
    lookups = _events(backend, binding.operation_id, "dispatch.launch.lookup-recorded")
    require(len(authorizations) == 1 and len(lookups) == 1, "callback fixture lacks exact launch authorization/receipt")
    auth = authorizations[0].get("payload") or {}
    lookup = lookups[0].get("payload") or {}
    require(auth.get("external_dispatch_key") == binding.external_dispatch_key, "authorization key drifted")
    require(lookup.get("receipt_id") == receipt_id and lookup.get("lookup_state") == "LAUNCHED", "launch receipt drifted")

    context = TrustedDispatchContext(
        operation_id=binding.operation_id,
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=binding.semantic_effect_key,
        external_dispatch_key=binding.external_dispatch_key,
        dispatch_id=str(auth["dispatch_id"]),
        runtime_receipt_identity=receipt_id,
        target_repository=REPOSITORY,
        target_ref=REF,
        feature_id=FEATURE,
        expected_revision=11,
        feature_stage="implementation",
        task_id=binding.task_id,
        role="developer",
        candidate_pr_number=230,
        candidate_head_sha=candidate,
        worker_identity="fixture-developer-worker",
        collector_identity="fixture-collector",
    )
    coordinator = TrustedVerticalCallbackCoordinator(
        executor=executor,
        trusted_role_policy="fixture-role-policy",
        collector_namespace_policy="fixture-collector-policy",
        content_loader=lambda _uri: b"unused",
    )
    return binding, backend, clock, feature_gateway, executor, coordinator, context


def _blocked_payload(summary):
    return {"status": "BLOCKED", "summary": summary, "outputs": []}


def scenario_duplicate_callback():
    binding, backend, _clock, _feature, _executor, coordinator, context = _setup_waiting_external(
        idempotency_key="fi-duplicate-callback",
        receipt_id="run-duplicate-callback",
    )
    callback_id = "callback-duplicate-exact"
    payload = _blocked_payload("worker reported a deterministic blocked state")

    first = coordinator.handle(context=context, callback_id=callback_id, worker_payload=payload, receipts=[])
    require(first["status"] == "BLOCKED", "first BLOCKED callback did not create stable stop")
    second = coordinator.handle(context=context, callback_id=callback_id, worker_payload=payload, receipts=[])
    require(second["status"] == "BLOCKED", "duplicate callback did not converge on same stable stop")

    callbacks = [
        event for event in _events(backend, binding.operation_id, "worker.callback.recorded")
        if (event.get("payload") or {}).get("callback_id") == callback_id
    ]
    validated = [
        event for event in _events(backend, binding.operation_id, "worker.result.validated")
        if (event.get("payload") or {}).get("callback_id") == callback_id
    ]
    require(len(callbacks) == 1, "exact duplicate callback created more than one durable callback fact")
    require(len(validated) == 1, "exact duplicate callback created more than one validated result fact")
    require(len(_events(backend, binding.operation_id, "feature.event.translated")) == 0, "BLOCKED duplicate callback translated lifecycle authority")
    require(not [event for event in operation_events(backend.read_snapshot(), binding.operation_id) if event["event_type"].startswith("persist.")], "BLOCKED duplicate callback created Persist authority")
    return {
        "scenario_id": "duplicate-callback",
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "callback_id": callback_id,
        "durable_callback_count": len(callbacks),
        "durable_validated_count": len(validated),
        "final_status": rebuild_projection(backend.read_snapshot(), binding.operation_id)["status"],
    }


def scenario_out_of_order_callback():
    binding, backend, clock, _feature, executor, coordinator, context = _setup_waiting_external(
        idempotency_key="fi-out-of-order-callback",
        receipt_id="run-out-of-order-callback",
    )
    executor.runtime.commit_replanned(
        lambda snapshot: plan_vertical_takeover(
            snapshot,
            operation_id=binding.operation_id,
            occurred_at=clock(),
            trusted_context_digest=TRUSTED_DIGEST,
        )
    )
    projection = rebuild_projection(backend.read_snapshot(), binding.operation_id)
    require(projection["generation"] == 1, "out-of-order callback fixture did not enter G1")

    try:
        coordinator.handle(
            context=context,
            callback_id="callback-stale-g0",
            worker_payload=_blocked_payload("late G0 callback"),
            receipts=[],
        )
    except VerticalInvariantError as exc:
        require(exc.code == "SUPERSEDED_GENERATION", f"stale G0 callback failed with wrong code: {exc.code}")
    else:
        raise AssertionError("superseded G0 callback entered coordinator after G1 takeover")

    callbacks = _events(backend, binding.operation_id, "worker.callback.recorded")
    require(len(callbacks) == 0, "superseded G0 callback created a durable callback fact")
    require(len(_events(backend, binding.operation_id, "worker.result.validated")) == 0, "superseded G0 callback was validated")
    require(len(_events(backend, binding.operation_id, "feature.event.translated")) == 0, "superseded G0 callback translated a Feature Event")
    require(not [event for event in operation_events(backend.read_snapshot(), binding.operation_id) if event["event_type"].startswith("persist.")], "superseded G0 callback created Persist authority")
    return {
        "scenario_id": "out-of-order-callback",
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "operation_generation": 1,
        "stale_callback_durable_count": 0,
        "final_status": projection["status"],
    }


def scenario_stale_candidate_result():
    binding, backend, clock, feature_gateway, executor, coordinator, context = _setup_waiting_external(
        idempotency_key="fi-stale-candidate-result",
        receipt_id="run-stale-candidate",
    )
    feature_gateway.candidate = CANDIDATE_B
    callback_id = "callback-stale-candidate-a"
    payload = _blocked_payload("candidate A worker completed after candidate B became current")

    result = coordinator.handle(context=context, callback_id=callback_id, worker_payload=payload, receipts=[])
    require(result["status"] == "BLOCKED", "stale candidate callback did not fail closed")
    callbacks = [
        event for event in _events(backend, binding.operation_id, "worker.callback.recorded")
        if (event.get("payload") or {}).get("callback_id") == callback_id
    ]
    rejected = [
        event for event in _events(backend, binding.operation_id, "worker.result.rejected")
        if (event.get("payload") or {}).get("callback_id") == callback_id
    ]
    require(len(callbacks) == 1, "stale candidate callback envelope was not durably recorded exactly once")
    require(len(rejected) == 1, "stale candidate result was not durably rejected exactly once")
    reject_payload = rejected[0].get("payload") or {}
    require(reject_payload.get("code") == "STALE_REVISION", "stale candidate rejection did not use STALE_REVISION")
    require(len(_events(backend, binding.operation_id, "worker.result.validated")) == 0, "stale candidate result was incorrectly validated")
    require(len(_events(backend, binding.operation_id, "feature.event.translated")) == 0, "stale candidate result translated lifecycle authority")
    require(not [event for event in operation_events(backend.read_snapshot(), binding.operation_id) if event["event_type"].startswith("persist.")], "stale candidate result created Persist authority")

    # Candidate B is new exact work in the same causal slot. The accepted lineage
    # planner may record its proposal, but overlapping candidate A remains the leaf;
    # B must not receive an external reservation until trusted resolution allows it.
    feature_b, manifest_b = feature_gateway.read_feature(operation_id=binding.operation_id)
    action_b = select_vertical_action(feature=feature_b, manifest=manifest_b, occurred_at=clock())
    require(action_b.kind == "dispatch" and action_b.candidate_head_sha == CANDIDATE_B, "fresh candidate B action is not exact-bound")
    binding_b = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        manifest=feature_gateway.manifest,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE_B,
        idempotency_key=binding.idempotency_key,
        occurred_at=clock(),
    )
    require(binding_b.semantic_effect_key != binding.semantic_effect_key, "candidate change did not produce a new exact semantic key")
    policy = executor.resolution_policy_verifier.verify_current()
    proposal = executor.runtime.commit_replanned(
        lambda snapshot: plan_lineage_gated_reservation(
            snapshot,
            operation_id=binding.operation_id,
            generation=0,
            target_repository=REPOSITORY,
            feature_id=FEATURE,
            expected_revision=11,
            current_stage="implementation",
            task_identity=str(action_b.task_identity),
            role=str(action_b.role),
            candidate_head_sha=CANDIDATE_B,
            current_target_ref=REF,
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            logical_work_slot=action_b.step,
            task_id=action_b.task_id,
            occurred_at=clock(),
            trusted_context_digest=TRUSTED_DIGEST,
            trusted_profile_digest=policy.proposal_profile_digest,
        )
    ).result
    require(proposal.get("status") == "BLOCKED" and proposal.get("reason") == "UNRESOLVED_PREDECESSOR", "fresh candidate B bypassed unresolved predecessor")
    require(proposal.get("predecessor_semantic_effect_key") == binding.semantic_effect_key, "fresh candidate proposal lost predecessor A binding")
    require(proposal.get("proposed_semantic_effect_key") == binding_b.semantic_effect_key, "fresh candidate proposal lost exact B semantic key")
    require(backend.read_snapshot().get(reservation_path(binding_b.semantic_effect_key)) is None, "fresh candidate B obtained external reservation while A predecessor remained unresolved")
    lineage_blocks = _events(backend, binding.operation_id, "effect.lineage.blocked")
    require(len(lineage_blocks) == 1, "fresh candidate B did not create one durable lineage-blocked proposal fact")
    require(rebuild_projection(backend.read_snapshot(), binding.operation_id)["status"] == "BLOCKED", "fresh candidate proposal escaped BLOCKED state")

    return {
        "scenario_id": "stale-candidate-result",
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "callback_id": callback_id,
        "old_candidate_head_sha": CANDIDATE,
        "current_candidate_head_sha": CANDIDATE_B,
        "stale_callback_durable_count": 1,
        "stale_result_rejected_count": 1,
        "fresh_candidate_semantic_effect_key": binding_b.semantic_effect_key,
        "fresh_candidate_proposal_id": str(proposal["proposal_id"]),
        "fresh_candidate_external_reservation_count": 0,
        "final_status": rebuild_projection(backend.read_snapshot(), binding.operation_id)["status"],
    }


def main():
    duplicate = scenario_duplicate_callback()
    out_of_order = scenario_out_of_order_callback()
    stale = scenario_stale_candidate_result()
    print("v0.3 callback coordinator orchestration validation passed")
    print("- exact duplicate callback converges to one durable callback/result and zero Persist authority")
    print("- superseded-generation callback is rejected before any durable callback record")
    print("- stale candidate A result is rejected; candidate B becomes an exact lineage proposal with zero external reservation")
    print("- deterministic harness evidence only; real collector/runtime proof remains required")
    return {row["scenario_id"]: row for row in (duplicate, out_of_order, stale)}


if __name__ == "__main__":
    main()
