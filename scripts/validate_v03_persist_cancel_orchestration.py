#!/usr/bin/env python3
"""Executor-level deterministic orchestration for v0.3 Persist/cancel races."""
from __future__ import annotations

from operator_store import plan_cancel
from operator_store_backends import StoreBackendError
from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_events, operation_id_for, rebuild_projection
from operator_vertical import FeatureSnapshot
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from operator_vertical_store import vertical_projection
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    REF,
    REPOSITORY,
    TRUSTED_DIGEST,
    Clock,
    make_bundle,
    make_runtime,
    manifest,
    resolution_verifier,
)

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


class NeverDispatchGateway:
    def launch(self, *, dispatch):
        raise AssertionError("Persist orchestration unexpectedly reached Worker dispatch")

    def lookup(self, *, external_dispatch_key):
        raise AssertionError("Persist orchestration unexpectedly reached Worker lookup")


class CancelBeforeLinearizationFeatureGateway:
    """Cancel only after persist.requested is durable and before linearization."""

    def __init__(self, manifest_doc, clock):
        self.manifest = manifest_doc
        self.clock = clock
        self.runtime = None
        self.cancel_count = 0

    def read_feature(self, *, operation_id):
        require(self.runtime is not None, "cancel-before-linearization gateway lacks runtime")
        events = operation_events(self.runtime.backend.read_snapshot(), operation_id)
        requested = any(event["event_type"] == "persist.requested" for event in events)
        linearized = any(event["event_type"] == "persist.linearized" for event in events)
        cancelled = any(event["event_type"] == "operation.cancelled" for event in events)
        if requested and not linearized and not cancelled:
            self.runtime.commit_replanned(
                lambda current: plan_cancel(
                    current,
                    operation_id=operation_id,
                    reason="fault-injection: cancel before Persist linearization",
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


class NeverPersistGateway:
    def __init__(self):
        self.persist_calls = 0
        self.lookup_calls = 0

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls += 1
        raise AssertionError("cancel-before-linearization reached external Feature write")

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        raise AssertionError("cancel-before-linearization reached external Persist lookup")


class CancelAfterLinearizationPersistGateway:
    """Allow one exact write only after durable linearization, then cancel before ACK is consumed."""

    def __init__(self, clock):
        self.clock = clock
        self.runtime = None
        self.operation_id = None
        self.persist_calls = 0
        self.lookup_calls = 0
        self.event_id = None

    def persist_feature_event(self, *, event, target_ref):
        require(self.runtime is not None and self.operation_id, "cancel-after-linearization gateway lacks durable binding")
        event_id = str(event["id"])
        linearized = [
            row
            for row in operation_events(self.runtime.backend.read_snapshot(), self.operation_id)
            if row["event_type"] == "persist.linearized"
            and (row.get("payload") or {}).get("feature_event_id") == event_id
        ]
        require(len(linearized) == 1, "external Feature write occurred without one exact durable Persist linearization")
        self.persist_calls += 1
        require(self.persist_calls == 1, "modeled Feature Persist executed more than once")
        self.event_id = event_id
        self.runtime.commit_replanned(
            lambda current: plan_cancel(
                current,
                operation_id=self.operation_id,
                reason="fault-injection: cancel after Persist linearization",
                occurred_at=self.clock(),
                trusted_context_digest=TRUSTED_DIGEST,
            )
        )
        return {"event_id": event_id, "target_ref": target_ref, "result_revision": 12}

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        raise AssertionError("successful exact Persist unexpectedly used fallback lookup")


def _make_executor(runtime, feature_gateway, persist_gateway):
    base = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=persist_gateway,
        dispatch_gateway=NeverDispatchGateway(),
        config=TrustedVerticalExecutorConfig(
            target_ref=REF,
            trusted_context_digest=TRUSTED_DIGEST,
            effect_lineage_required=True,
            old_writers_quiesced=True,
            rollout_policy_digest="fixture-lineage-policy",
            writer_fence_receipt_digest="fixture-writer-fence-digest",
            max_auto_steps=4,
        ),
        resolution_policy_verifier=resolution_verifier(),
    )
    return TrustedRecoveringVerticalExecutor(
        base_executor=base,
        content_loader=lambda *_args, **_kwargs: "unused",
        trusted_role_policy="fixture-role-policy",
        collector_namespace_policy="fixture-collector-policy",
    )


def scenario_cancel_before_persist_linearization():
    fixture = manifest(current_stage="code-review", stage_status="READY")
    idempotency = "fi-cancel-before-persist-linearized"
    operation_id = operation_id_for(REPOSITORY, FEATURE, idempotency)
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    feature_gateway = CancelBeforeLinearizationFeatureGateway(fixture, clock)
    persist_gateway = NeverPersistGateway()
    runtime = make_runtime(backend, clock)
    feature_gateway.runtime = runtime
    executor = _make_executor(runtime, feature_gateway, persist_gateway)
    bundle = make_bundle(runtime, executor)

    try:
        bundle.backends["operation.start"].invoke(_start_request(idempotency), _trusted_context(bundle))
    except StoreBackendError as exc:
        require(exc.code == "CANCELLED_OPERATION", f"unexpected cancel-before-linearization error: {exc.code}")
    else:
        raise AssertionError("cancel-before-linearization did not stop Persist linearization")

    projection = rebuild_projection(backend.read_snapshot(), operation_id)
    requested = _events(backend, operation_id, "persist.requested")
    require(projection["status"] == "CANCELLED", "cancel-before-linearization final status is not CANCELLED")
    require(feature_gateway.cancel_count == 1, "cancel-before-linearization was not injected exactly once")
    require(len(requested) == 1, "expected one durable Persist request before cancellation")
    require(len(_events(backend, operation_id, "persist.linearized")) == 0, "cancelled Persist became linearized")
    require(len(_events(backend, operation_id, "persist.confirmed")) == 0, "cancelled unlinearized Persist became confirmed")
    require(persist_gateway.persist_calls == 0 and persist_gateway.lookup_calls == 0, "unlinearized cancelled Persist touched external gateway")
    return {
        "scenario_id": "cancel-before-persist-linearization",
        "operation_id": operation_id,
        "feature_event_id": (requested[0].get("payload") or {})["feature_event_id"],
        "external_feature_write_count": 0,
        "final_status": projection["status"],
    }


def scenario_persist_linearized_before_cancel():
    fixture = manifest(current_stage="code-review", stage_status="READY")
    idempotency = "fi-persist-linearized-before-cancel"
    operation_id = operation_id_for(REPOSITORY, FEATURE, idempotency)
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    feature_gateway = PlainFeatureGateway(fixture)
    persist_gateway = CancelAfterLinearizationPersistGateway(clock)
    runtime = make_runtime(backend, clock)
    persist_gateway.runtime = runtime
    persist_gateway.operation_id = operation_id
    executor = _make_executor(runtime, feature_gateway, persist_gateway)
    bundle = make_bundle(runtime, executor)

    result = bundle.backends["operation.start"].invoke(_start_request(idempotency), _trusted_context(bundle))
    require(result["status"] == "CANCELLED", "linearized-before-cancel did not converge to CANCELLED")
    projection = rebuild_projection(backend.read_snapshot(), operation_id)
    vertical = vertical_projection(backend.read_snapshot(), operation_id)
    requested = _events(backend, operation_id, "persist.requested")
    linearized = _events(backend, operation_id, "persist.linearized")
    cancelled = _events(backend, operation_id, "operation.cancelled")
    confirmed = _events(backend, operation_id, "persist.confirmed")
    require(len(requested) == len(linearized) == len(cancelled) == len(confirmed) == 1, "expected one request, linearization, cancel and confirmation")
    require(
        requested[0]["sequence"] < linearized[0]["sequence"] < cancelled[0]["sequence"] < confirmed[0]["sequence"],
        "durable ordering is not request -> linearized -> cancel -> exact confirmed",
    )
    require(projection["status"] == "CANCELLED", "post-confirmation Operation escaped CANCELLED")
    require(vertical["expected_feature_revision"] == 12, "exact confirmed Persist did not advance vertical revision fence")
    require(persist_gateway.persist_calls == 1 and persist_gateway.lookup_calls == 0, "exact linearized Persist did not execute once")
    require(persist_gateway.event_id == (confirmed[0].get("payload") or {}).get("feature_event_id"), "confirmed Event identity differs from external write")
    return {
        "scenario_id": "persist-linearized-before-cancel",
        "operation_id": operation_id,
        "feature_event_id": persist_gateway.event_id,
        "persist_receipt_id": "modeled-exact-event-receipt",
        "result_revision": 12,
        "external_feature_write_count": 1,
        "final_status": projection["status"],
    }


def main():
    before = scenario_cancel_before_persist_linearization()
    after = scenario_persist_linearized_before_cancel()
    print("v0.3 Persist/cancel executor orchestration validation passed")
    print("- cancel-before-linearization: one Persist request, zero linearization, zero external Feature write")
    print("- linearized-before-cancel: one exact Feature write, cancel, then legal exact Persist confirmation")
    print("- final Operation remains CANCELLED; no automatic lifecycle progression is authorized")
    print("- deterministic harness evidence only; no Issue #221 release PASS is claimed")
    return {before["scenario_id"]: before, after["scenario_id"]: after}


if __name__ == "__main__":
    main()
