#!/usr/bin/env python3
"""Executor-level deterministic orchestration for concurrent resume convergence."""
from __future__ import annotations

from operator_store_backends import OperationStartBackend
from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_events, operation_id_for
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE
from operator_vertical_controller import select_vertical_action
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    REF,
    REPOSITORY,
    TRUSTED_DIGEST,
    Clock,
    ExternalRuntime,
    FeatureGateway,
    LookupFirstDispatchGateway,
    make_executor,
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


def main():
    fixture = manifest()
    idempotency = "fi-concurrent-resume"
    operation_id = operation_id_for(REPOSITORY, FEATURE, idempotency)
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    external = ExternalRuntime()
    feature_gateway = FeatureGateway(fixture)

    runtime_a = make_runtime(backend, clock)
    runtime_b = make_runtime(backend, clock)
    gateway_a = LookupFirstDispatchGateway(external)
    gateway_b = LookupFirstDispatchGateway(external)
    executor_a = make_executor(runtime_a, feature_gateway, gateway_a)
    executor_b = make_executor(runtime_b, feature_gateway, gateway_b)

    start = OperationStartBackend(runtime_a, operation_profile=VERTICAL_PROFILE)
    started = start.invoke(
        {
            "idempotency_key": idempotency,
            "target": {"repository": REPOSITORY, "feature_id": FEATURE},
            "context": {"expected_feature_revision": 11},
        },
        {
            "trusted_context_digest": TRUSTED_DIGEST,
            "feature_verification": {
                "repository": REPOSITORY,
                "feature_id": FEATURE,
                "revision": 11,
            },
        },
    )
    require(started["operation_id"] == operation_id, "concurrent fixture started unexpected Operation id")

    # Both runners select from the same Feature truth and pre-effect Store state.
    feature_a, manifest_a = feature_gateway.read_feature(operation_id=operation_id)
    feature_b, manifest_b = feature_gateway.read_feature(operation_id=operation_id)
    action_a = select_vertical_action(feature=feature_a, manifest=manifest_a, occurred_at=clock())
    action_b = select_vertical_action(feature=feature_b, manifest=manifest_b, occurred_at=clock())
    require(action_a == action_b and action_a.kind == "dispatch", "concurrent runners did not preselect the same exact dispatch action")

    result_a = executor_a.advance_action(operation_id=operation_id, action=action_a)
    require(result_a["status"] == "WAITING_EXTERNAL", "runner A did not stop at WAITING_EXTERNAL")
    require(external.post_count == 1, "runner A did not create exactly one modeled external effect")

    # Runner B now executes its already-selected action. Its runtime must re-plan
    # every Store write against the current head and converge on the same exact
    # reservation/claim/authorization/receipt instead of producing a second effect.
    result_b = executor_b.advance_action(operation_id=operation_id, action=action_b)
    require(result_b["status"] == "WAITING_EXTERNAL", "runner B stale action did not converge to WAITING_EXTERNAL")
    require(external.post_count == 1, "runner B stale action created a duplicate external effect")
    require(gateway_a.launch_calls == gateway_b.launch_calls and len(gateway_a.launch_calls) == 1, "concurrent runners used different external dispatch keys")
    exact_key = gateway_a.launch_calls[0]
    require(gateway_b.lookup_calls == [], "lookup-first stale runner unexpectedly used explicit recovery lookup")

    reservations = [
        value
        for path, value in backend.read_snapshot().files.items()
        if path.startswith("state/operator/v1/reservations/external/")
        and isinstance(value, dict)
        and value.get("external_dispatch_key") == exact_key
    ]
    claims = [
        value
        for path, value in backend.read_snapshot().files.items()
        if path.startswith("state/operator/v1/claims/dispatch/")
        and isinstance(value, dict)
        and value.get("external_dispatch_key") == exact_key
    ]
    authorizations = [
        event for event in _events(backend, operation_id, "dispatch.launch.authorized")
        if (event.get("payload") or {}).get("external_dispatch_key") == exact_key
    ]
    lookups = [
        event for event in _events(backend, operation_id, "dispatch.launch.lookup-recorded")
        if (event.get("payload") or {}).get("external_dispatch_key") == exact_key
    ]
    selected = _events(backend, operation_id, "loop.step.selected")

    require(len(reservations) == 1, "concurrent stale action created duplicate semantic reservations")
    require(len(claims) == 1, "concurrent stale action created duplicate dispatch claims")
    require(len(authorizations) == 1, "concurrent stale action created duplicate launch authorizations")
    require(len(lookups) == 1, "concurrent stale action created duplicate launch receipt facts")
    require(len(selected) == 1, "concurrent stale action created duplicate selected-step facts")
    require((lookups[0].get("payload") or {}).get("receipt_id") == "run-1", "concurrent convergence lost original runtime receipt")
    require(not [event for event in operation_events(backend.read_snapshot(), operation_id) if event["event_type"].startswith("persist.")], "concurrent dispatch race created Persist authority")

    print("v0.3 concurrent resume stale-action orchestration validation passed")
    print("- two independent runtimes preselect the same exact dispatch from one durable pre-effect state")
    print("- runner A creates one modeled external run; runner B re-plans every write on the current Store head")
    print("- reservation, claim, authorization, selected-step and launch-receipt facts each remain exactly one")
    print("- lookup-first external transport keeps modeled external POST count at one")
    print("- deterministic harness evidence only; real protected-state runner race remains required")
    return {
        "scenario_id": "concurrent-resume",
        "operation_id": operation_id,
        "external_dispatch_key": exact_key,
        "semantic_effect_key": str(reservations[0]["semantic_effect_key"]),
        "external_post_count": external.post_count,
        "reservation_count": len(reservations),
        "claim_count": len(claims),
        "authorization_count": len(authorizations),
        "launch_receipt_count": len(lookups),
        "final_status": result_b["status"],
    }


if __name__ == "__main__":
    main()
