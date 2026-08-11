#!/usr/bin/env python3
"""Validate semantic failure classification for already-linearized Persist recovery."""
from __future__ import annotations

from operator_github_feature_event_gateway import FeatureEventGatewayError
from operator_store import plan_cancel, plan_operation_fact, plan_operation_start
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import digest_json, operation_events, rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor
from operator_vertical_store import plan_vertical_persist_linearized, plan_vertical_persist_requested

REPOSITORY = "dream-xin/fixture"
FEATURE = "F-PERSIST-CLASSIFY-0001"
REF = "feature/F-PERSIST-CLASSIFY-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
EVENT_ID = "EVT-F-PERSIST-CLASSIFY-0001"
REV = 7
NOW = "2026-08-11T06:15:00Z"
DETERMINISTIC_CODES = (
    "STALE_REVISION",
    "CONFLICT",
    "UNAUTHORIZED",
    "POLICY_DENIED",
    "INVALID_REQUEST",
    "INTERNAL_FAILURE",
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def feature_event():
    return {
        "version": "0.1.0",
        "id": EVENT_ID,
        "feature_id": FEATURE,
        "expected_revision": REV,
        "occurred_at": NOW,
        "changes": [{"kind": "stage", "id": "implementation", "status": "DONE"}],
    }


class UnusedFeatureGateway:
    def read_feature(self, *, operation_id):
        raise AssertionError("already-linearized Persist classification must not need fresh Feature truth")


class UnusedDispatchGateway:
    def launch(self, *, dispatch):
        raise AssertionError("Persist classification must not launch a Worker")

    def lookup(self, *, external_dispatch_key):
        raise AssertionError("Persist classification must not inspect Worker dispatch")


class PersistGateway:
    def __init__(self, *, lookup_result=None, lookup_error=None, persist_result=None, persist_error=None):
        self.lookup_result = lookup_result
        self.lookup_error = lookup_error
        self.persist_result = persist_result
        self.persist_error = persist_error
        self.lookup_calls = 0
        self.persist_calls = 0

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        require(event_id == EVENT_ID and target_ref == REF, (event_id, target_ref))
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.lookup_result

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls += 1
        require(event.get("id") == EVENT_ID and target_ref == REF, (event, target_ref))
        if self.persist_error is not None:
            raise self.persist_error
        if self.persist_result is not None:
            return self.persist_result
        raise AssertionError("unexpected Persist submission")


def make_runtime():
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository="dream-xin/control", state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )


def seed_linearized(runtime):
    started = runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPOSITORY,
            feature_id=FEATURE,
            expected_revision=REV,
            idempotency_key="persist-classification",
            occurred_at=NOW,
            trusted_context_digest="persist-classification",
            operation_profile=VERTICAL_PROFILE,
        )
    )
    operation_id = str(started.result["operation_id"])
    event = feature_event()
    runtime.commit_replanned(
        lambda snapshot: plan_operation_fact(
            snapshot,
            operation_id=operation_id,
            generation=0,
            event_type="feature.event.translated",
            payload={
                "feature_event_id": EVENT_ID,
                "feature_event_digest": digest_json(event),
                "feature_event": event,
                "feature_revision": REV,
                "feature_stage": "implementation",
                "feature_manifest_digest": "manifest-fixture",
                "candidate_head_sha": None,
                "target_ref": REF,
            },
            occurred_at=NOW,
            trusted_context_digest="persist-classification",
        )
    )
    common = dict(
        operation_id=operation_id,
        generation=0,
        feature_event_id=EVENT_ID,
        expected_revision=REV,
        target_ref=REF,
        candidate_head_sha=None,
        occurred_at=NOW,
        trusted_context_digest="persist-classification",
    )
    runtime.commit_replanned(lambda snapshot: plan_vertical_persist_requested(snapshot, **common))
    runtime.commit_replanned(lambda snapshot: plan_vertical_persist_linearized(snapshot, **common))
    return operation_id


def executor(runtime, persist_gateway):
    base = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=UnusedFeatureGateway(),
        persist_gateway=persist_gateway,
        dispatch_gateway=UnusedDispatchGateway(),
        config=TrustedVerticalExecutorConfig(
            target_ref=REF,
            trusted_context_digest="persist-classification",
            legacy_compatibility_mode=True,
        ),
    )
    return FailureClassifyingTrustedRecoveringVerticalExecutor(
        base_executor=base,
        content_loader=lambda uri: b"",
        trusted_role_policy="fixture-role-policy",
        collector_namespace_policy="fixture-collector-policy",
    )


def event_count(runtime, operation_id):
    return len(operation_events(runtime.backend.read_snapshot(), operation_id))


def cancel(runtime, operation_id):
    runtime.commit_replanned(
        lambda snapshot: plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="fixture cancellation",
            occurred_at=NOW,
            trusted_context_digest="persist-classification",
        )
    )


def validate_all_live_deterministic_codes_block():
    for code in DETERMINISTIC_CODES:
        runtime = make_runtime()
        operation_id = seed_linearized(runtime)
        persist = PersistGateway(lookup_error=FeatureEventGatewayError(code, f"fixture {code}"))
        result = executor(runtime, persist)._reconcile_persist(operation_id)
        require(isinstance(result, dict) and result["status"] == "BLOCKED", (code, result))
        require(
            rebuild_projection(runtime.backend.read_snapshot(), operation_id)["status"] == "BLOCKED",
            (code, result),
        )
        require(persist.lookup_calls == 1, (code, persist.lookup_calls))
        require(persist.persist_calls == 0, f"{code} lookup failure incorrectly retried Persist submission")


def validate_live_transient_and_unclassified_wait():
    for error in (
        FeatureEventGatewayError("TRANSIENT_FAILURE", "GitHub unavailable"),
        RuntimeError("unclassified transport failure"),
    ):
        runtime = make_runtime()
        operation_id = seed_linearized(runtime)
        persist = PersistGateway(lookup_error=error)
        result = executor(runtime, persist)._reconcile_persist(operation_id)
        require(isinstance(result, dict) and result["status"] == "WAITING_EXTERNAL", result)
        require(rebuild_projection(runtime.backend.read_snapshot(), operation_id)["status"] == "WAITING_EXTERNAL", result)
        require(persist.lookup_calls == 1 and persist.persist_calls == 0, (persist.lookup_calls, persist.persist_calls))


def validate_lookup_absent_then_deterministic_submit_failure_blocks():
    runtime = make_runtime()
    operation_id = seed_linearized(runtime)
    persist = PersistGateway(
        lookup_result=None,
        persist_error=FeatureEventGatewayError("CONFLICT", "exact Event changed before submit"),
    )
    result = executor(runtime, persist)._reconcile_persist(operation_id)
    require(isinstance(result, dict) and result["status"] == "BLOCKED", result)
    require(persist.lookup_calls == 1 and persist.persist_calls == 1, (persist.lookup_calls, persist.persist_calls))


def validate_invalid_receipts_block():
    for receipt in (
        {"event_id": "EVT-WRONG", "result_revision": REV + 1},
        {"event_id": EVENT_ID, "result_revision": REV + 2},
        "not-a-receipt",
    ):
        runtime = make_runtime()
        operation_id = seed_linearized(runtime)
        persist = PersistGateway(lookup_result=receipt)
        result = executor(runtime, persist)._reconcile_persist(operation_id)
        require(isinstance(result, dict) and result["status"] == "BLOCKED", (receipt, result))
        require(persist.persist_calls == 0, receipt)


def assert_cancelled_no_mutation(*, error_code, expected_code):
    runtime = make_runtime()
    operation_id = seed_linearized(runtime)
    cancel(runtime, operation_id)
    before = event_count(runtime, operation_id)
    persist = PersistGateway(lookup_error=FeatureEventGatewayError(error_code, "fixture failure"))
    try:
        executor(runtime, persist)._reconcile_persist(operation_id)
        raise AssertionError(f"cancelled {error_code} unexpectedly returned normally")
    except VerticalInvariantError as exc:
        require(exc.code == expected_code, (error_code, exc.code, str(exc)))
    after = event_count(runtime, operation_id)
    require(after == before, f"cancelled {error_code} appended a forbidden journal fact")
    projection = rebuild_projection(runtime.backend.read_snapshot(), operation_id)
    require(projection["status"] == "CANCELLED", projection)
    require(EVENT_ID not in projection["confirmed_persists"], projection)
    require(persist.persist_calls == 0, persist.persist_calls)


def validate_cancelled_exact_confirmation_remains_legal():
    runtime = make_runtime()
    operation_id = seed_linearized(runtime)
    cancel(runtime, operation_id)
    before = event_count(runtime, operation_id)
    persist = PersistGateway(lookup_result={"event_id": EVENT_ID, "result_revision": REV + 1})
    result = executor(runtime, persist)._reconcile_persist(operation_id)
    require(result is True, result)
    after = event_count(runtime, operation_id)
    require(after == before + 1, (before, after))
    projection = rebuild_projection(runtime.backend.read_snapshot(), operation_id)
    require(projection["status"] == "CANCELLED", projection)
    require(EVENT_ID in projection["confirmed_persists"], projection)
    require(persist.lookup_calls == 1 and persist.persist_calls == 0, (persist.lookup_calls, persist.persist_calls))


def main():
    validate_all_live_deterministic_codes_block()
    validate_live_transient_and_unclassified_wait()
    validate_lookup_absent_then_deterministic_submit_failure_blocks()
    validate_invalid_receipts_block()
    assert_cancelled_no_mutation(error_code="STALE_REVISION", expected_code="BLOCKED")
    assert_cancelled_no_mutation(error_code="TRANSIENT_FAILURE", expected_code="EXTERNAL_WAIT")
    validate_cancelled_exact_confirmation_remains_legal()
    print("Persist reconciliation failure classification validation passed")
    print("- all deterministic exact-Persist error codes => durable BLOCKED")
    print("- deterministic lookup failure => zero Persist resubmission")
    print("- transient/unclassified external failure => WAITING_EXTERNAL")
    print("- lookup ABSENT may submit once; deterministic submit failure => BLOCKED")
    print("- invalid exact receipt identity/revision => BLOCKED")
    print("- cancelled deterministic/transient failure => zero journal mutation")
    print("- cancelled exact applied receipt => persist.confirmed remains legal")


if __name__ == "__main__":
    main()
