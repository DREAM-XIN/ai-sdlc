#!/usr/bin/env python3
"""Executor-level deterministic orchestration for v0.3 launch/cancel races.

This validates the accepted lineage-required Vertical executor and real Store
reducers. It is harness evidence only: external execution is modeled in-memory
and the resulting records are never release-eligible.
"""
from __future__ import annotations

from operator_store import plan_cancel
from operator_store_backends import StoreBackendError
from operator_store_model import operation_events, rebuild_projection
from operator_vertical import FeatureSnapshot
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
from operator_store_git import MemoryStateRefBackend

STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _feature(manifest_doc):
    return FeatureSnapshot.from_manifest(
        repository=REPOSITORY,
        target_ref=REF,
        manifest=manifest_doc,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
    )


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


class NeverLaunchGateway:
    def __init__(self):
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        self.launch_calls.append(dict(dispatch))
        raise AssertionError("cancel-before-authorization reached external launch")

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(str(external_dispatch_key))
        raise AssertionError("cancel-before-authorization reached external lookup")


class CancelBeforeAuthorizationFeatureGateway:
    """Durably cancel only after the executor has created its dispatch claim."""

    def __init__(self, manifest_doc, clock):
        self.manifest = manifest_doc
        self.clock = clock
        self.runtime = None
        self.cancel_count = 0

    def read_feature(self, *, operation_id):
        require(self.runtime is not None, "cancel-before-authorization gateway lacks runtime")
        snapshot = self.runtime.backend.read_snapshot()
        events = operation_events(snapshot, operation_id)
        claimed = any(event["event_type"] == "dispatch.claimed" for event in events)
        authorized = any(event["event_type"] == "dispatch.launch.authorized" for event in events)
        cancelled = any(event["event_type"] == "operation.cancelled" for event in events)
        if claimed and not authorized and not cancelled:
            self.runtime.commit_replanned(
                lambda current: plan_cancel(
                    current,
                    operation_id=operation_id,
                    reason="fault-injection: cancel before launch authorization",
                    occurred_at=self.clock(),
                    trusted_context_digest=TRUSTED_DIGEST,
                )
            )
            self.cancel_count += 1
        return _feature(self.manifest), self.manifest


class PlainFeatureGateway:
    def __init__(self, manifest_doc):
        self.manifest = manifest_doc

    def read_feature(self, *, operation_id):
        return _feature(self.manifest), self.manifest


class CancelAfterAuthorizationGateway:
    """Model one exact external launch while cancellation races after authorization."""

    def __init__(self, clock):
        self.clock = clock
        self.runtime = None
        self.post_count = 0
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        require(self.runtime is not None, "cancel-after-authorization gateway lacks runtime")
        key = str(dispatch["external_dispatch_key"])
        generation = int(dispatch["operation_generation"])
        operation_id = str(dispatch["operation_id"])
        authorized = [
            event
            for event in operation_events(self.runtime.backend.read_snapshot(), operation_id)
            if event["event_type"] == "dispatch.launch.authorized"
            and int(event["operation_generation"]) == generation
            and (event.get("payload") or {}).get("external_dispatch_key") == key
        ]
        require(len(authorized) == 1, "external launch occurred without exactly one durable authorization")
        self.launch_calls.append(key)
        self.post_count += 1
        require(self.post_count == 1, "modeled runtime received a duplicate external POST")
        self.runtime.commit_replanned(
            lambda current: plan_cancel(
                current,
                operation_id=operation_id,
                reason="fault-injection: cancel after launch authorization",
                occurred_at=self.clock(),
                trusted_context_digest=TRUSTED_DIGEST,
            )
        )
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-launch-before-cancel-1"}

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(str(external_dispatch_key))
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-launch-before-cancel-1"}


def scenario_cancel_before_launch_authorization():
    fixture = manifest()
    idempotency = "fi-cancel-before-launch-auth"
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
    feature_gateway = CancelBeforeAuthorizationFeatureGateway(fixture, clock)
    dispatch_gateway = NeverLaunchGateway()
    runtime = make_runtime(backend, clock)
    feature_gateway.runtime = runtime
    executor = make_executor(runtime, feature_gateway, dispatch_gateway)
    bundle = make_bundle(runtime, executor)

    try:
        bundle.backends["operation.start"].invoke(_start_request(idempotency), _trusted_context(bundle))
    except StoreBackendError as exc:
        require(exc.code == "CANCELLED_OPERATION", f"unexpected cancel-before-authorization error: {exc.code}")
    else:
        raise AssertionError("cancel-before-authorization did not stop launch authorization")

    projection = rebuild_projection(backend.read_snapshot(), binding.operation_id)
    require(projection["status"] == "CANCELLED", "cancel-before-authorization did not leave Operation CANCELLED")
    require(feature_gateway.cancel_count == 1, "cancel-before-authorization was not injected exactly once")
    require(len(_events(backend, binding.operation_id, "dispatch.claimed")) == 1, "expected one pre-cancel dispatch claim")
    require(len(_events(backend, binding.operation_id, "dispatch.launch.authorized")) == 0, "cancelled race still authorized launch")
    require(len(_events(backend, binding.operation_id, "dispatch.launch.lookup-recorded")) == 0, "cancelled race recorded launch lookup")
    require(dispatch_gateway.launch_calls == [] and dispatch_gateway.lookup_calls == [], "cancel-before-authorization touched external runtime")
    require(not [event for event in operation_events(backend.read_snapshot(), binding.operation_id) if event["event_type"].startswith("persist.")], "cancel-before-authorization created Persist authority")
    return {
        "scenario_id": "cancel-before-launch-authorization",
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "external_post_count": 0,
        "final_status": projection["status"],
    }


def scenario_launch_authorized_before_cancel():
    fixture = manifest()
    idempotency = "fi-launch-auth-before-cancel"
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
    feature_gateway = PlainFeatureGateway(fixture)
    dispatch_gateway = CancelAfterAuthorizationGateway(clock)
    runtime = make_runtime(backend, clock)
    dispatch_gateway.runtime = runtime
    executor = make_executor(runtime, feature_gateway, dispatch_gateway)
    bundle = make_bundle(runtime, executor)

    result = bundle.backends["operation.start"].invoke(_start_request(idempotency), _trusted_context(bundle))
    require(result["status"] == "CANCELLED", "launch-authorized-before-cancel did not converge to CANCELLED")
    projection = rebuild_projection(backend.read_snapshot(), binding.operation_id)
    require(projection["status"] == "CANCELLED", "durable projection is not CANCELLED")
    require(dispatch_gateway.post_count == 1, "authorized-before-cancel did not create exactly one modeled external run")
    require(dispatch_gateway.launch_calls == [binding.external_dispatch_key], "external launch used a different key")
    require(dispatch_gateway.lookup_calls == [], "successful exact launch unexpectedly used fallback lookup")

    authorized = _events(backend, binding.operation_id, "dispatch.launch.authorized")
    cancelled = _events(backend, binding.operation_id, "operation.cancelled")
    looked_up = _events(backend, binding.operation_id, "dispatch.launch.lookup-recorded")
    require(len(authorized) == len(cancelled) == len(looked_up) == 1, "expected one authorization, cancel and lookup event")
    require(
        authorized[0]["sequence"] < cancelled[0]["sequence"] < looked_up[0]["sequence"],
        "durable ordering is not authorization -> cancel -> exact receipt lookup",
    )
    lookup_payload = looked_up[0].get("payload") or {}
    require(lookup_payload.get("lookup_state") == "LAUNCHED", "post-cancel exact authorized receipt was not recorded as LAUNCHED")
    require(lookup_payload.get("receipt_id") == "run-launch-before-cancel-1", "post-cancel receipt identity drifted")
    require(not [event for event in operation_events(backend.read_snapshot(), binding.operation_id) if event["event_type"].startswith("persist.")], "authorized launch gained automatic Persist authority after cancel")
    return {
        "scenario_id": "launch-authorized-before-cancel",
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "runtime_receipt_identity": "run-launch-before-cancel-1",
        "external_post_count": 1,
        "final_status": projection["status"],
    }


def main():
    before = scenario_cancel_before_launch_authorization()
    after = scenario_launch_authorized_before_cancel()
    print("v0.3 launch/cancel executor orchestration validation passed")
    print("- cancel-before-authorization: durable cancel after claim => zero authorization, zero external launch")
    print("- authorized-before-cancel: one exact launch => cancel => legal exact receipt observation")
    print("- both scenarios finish CANCELLED and create zero automatic Persist authority")
    print("- deterministic harness evidence only; no Issue #221 release PASS is claimed")
    return {before["scenario_id"]: before, after["scenario_id"]: after}


if __name__ == "__main__":
    main()
