#!/usr/bin/env python3
"""Executor-level deterministic orchestration for UNKNOWN launch takeover."""
from __future__ import annotations

from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_events, rebuild_projection
from operator_vertical_recovery import plan_vertical_takeover
from operator_vertical_store import vertical_projection
from v03_real_runtime_lost_ack_orchestration import derive_lost_ack_dispatch_binding
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    REF,
    REPOSITORY,
    TRUSTED_DIGEST,
    Clock,
    FeatureGateway,
    make_bundle,
    make_executor,
    make_runtime,
    manifest,
)

STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


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


def _events(backend, operation_id, event_type, generation=None):
    rows = [
        event
        for event in operation_events(backend.read_snapshot(), operation_id)
        if event["event_type"] == event_type
    ]
    if generation is not None:
        rows = [event for event in rows if int(event["operation_generation"]) == generation]
    return rows


class UnknownLaunchGateway:
    def __init__(self):
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        key = str(dispatch["external_dispatch_key"])
        self.launch_calls.append(key)
        return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(str(external_dispatch_key))
        return {"lookup_state": "UNKNOWN", "receipt_id": None}


class NoRecoveryExternalAccessGateway:
    def __init__(self):
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        self.launch_calls.append(str(dispatch["external_dispatch_key"]))
        raise AssertionError("UNKNOWN takeover speculatively relaunched external effect")

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(str(external_dispatch_key))
        raise AssertionError("UNKNOWN takeover bypassed durable BLOCKED state with a fresh lookup")


def main():
    fixture = manifest()
    idempotency = "fi-unknown-takeover"
    binding = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        manifest=fixture,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
        idempotency_key=idempotency,
        occurred_at="2026-08-11T00:00:00Z",
    )
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    feature_gateway = FeatureGateway(fixture)

    g0_gateway = UnknownLaunchGateway()
    runtime_g0 = make_runtime(backend, clock)
    executor_g0 = make_executor(runtime_g0, feature_gateway, g0_gateway)
    bundle_g0 = make_bundle(runtime_g0, executor_g0)
    result_g0 = bundle_g0.backends["operation.start"].invoke(_start_request(idempotency), _trusted_context(bundle_g0))
    require(result_g0["status"] == "BLOCKED", "UNKNOWN launch did not fail closed in G0")
    g0 = vertical_projection(backend.read_snapshot(), binding.operation_id)
    require(g0["generation"] == 0 and g0["status"] == "BLOCKED", "G0 projection is not BLOCKED")
    require(binding.external_dispatch_key in g0["unresolved_unknown"], "G0 lost unresolved UNKNOWN external key")
    require(g0_gateway.launch_calls == [binding.external_dispatch_key], "G0 UNKNOWN launch used wrong key")
    require(g0_gateway.lookup_calls == [], "direct UNKNOWN launch unexpectedly performed fallback lookup")
    lookups_g0 = _events(backend, binding.operation_id, "dispatch.launch.lookup-recorded", 0)
    require(len(lookups_g0) == 1, "G0 UNKNOWN was not durably recorded once")
    require((lookups_g0[0].get("payload") or {}).get("lookup_state") == "UNKNOWN", "G0 durable lookup is not UNKNOWN")

    runtime_g0.commit_replanned(
        lambda snapshot: plan_vertical_takeover(
            snapshot,
            operation_id=binding.operation_id,
            occurred_at=clock(),
            trusted_context_digest=TRUSTED_DIGEST,
        )
    )
    after_takeover = vertical_projection(backend.read_snapshot(), binding.operation_id)
    require(after_takeover["generation"] == 1, "trusted UNKNOWN takeover did not create G1")
    require(after_takeover["status"] == "BLOCKED", "UNKNOWN takeover cleared durable BLOCKED state")
    require(binding.external_dispatch_key in after_takeover["unresolved_unknown"], "UNKNOWN external key did not survive takeover")

    g1_gateway = NoRecoveryExternalAccessGateway()
    runtime_g1 = make_runtime(backend, clock)
    executor_g1 = make_executor(runtime_g1, feature_gateway, g1_gateway)
    result_g1 = executor_g1.advance_until_stop(operation_id=binding.operation_id)
    require(result_g1["status"] == "BLOCKED", "fresh G1 executor escaped UNKNOWN BLOCKED state")
    require(g1_gateway.launch_calls == [] and g1_gateway.lookup_calls == [], "fresh G1 touched external runtime under UNKNOWN")
    require(len(_events(backend, binding.operation_id, "dispatch.claimed", 1)) == 0, "G1 created a new dispatch claim under unresolved UNKNOWN")
    require(len(_events(backend, binding.operation_id, "dispatch.launch.authorized", 1)) == 0, "G1 authorized a new launch under unresolved UNKNOWN")
    require(len(_events(backend, binding.operation_id, "dispatch.launch.lookup-recorded", 1)) == 0, "G1 created new lookup evidence under unresolved UNKNOWN")
    final = rebuild_projection(backend.read_snapshot(), binding.operation_id)
    require(final["generation"] == 1 and final["status"] == "BLOCKED", "final UNKNOWN takeover projection drifted")

    print("v0.3 UNKNOWN takeover executor orchestration validation passed")
    print("- G0 exact launch returns UNKNOWN and durably blocks on the same external dispatch key")
    print("- trusted takeover advances to G1 without clearing unresolved UNKNOWN")
    print("- fresh G1 executor performs zero launch, zero lookup, zero new claim/authorization")
    print("- deterministic harness evidence only; trusted resolution authority remains required")
    return {
        "scenario_id": "unknown-takeover",
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "operation_generation": 1,
        "final_status": final["status"],
        "external_launch_attempt_count": 1,
        "g1_external_access_count": 0,
    }


if __name__ == "__main__":
    main()
