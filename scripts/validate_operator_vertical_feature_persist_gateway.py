#!/usr/bin/env python3
"""Adversarial restart validation for durable Vertical Feature Persist adapter."""
from __future__ import annotations

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_release_feature_event_gateway import build_release_decision_event_gateway
from operator_store import plan_operation_fact, plan_operation_start
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import digest_json
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
from operator_vertical_store import plan_vertical_persist_linearized, plan_vertical_persist_requested
from validate_operator_github_feature_event_gateway import EVENT_ID, FEATURE, REF, REPO, REV
from validate_operator_release_feature_event_gateway import HistoryFakeGitHub

STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-11T06:00:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def exact_event(*, stage_status="WORKING"):
    return {
        "version": "0.1.0",
        "id": EVENT_ID,
        "feature_id": FEATURE,
        "expected_revision": REV,
        "occurred_at": NOW,
        "changes": [
            {"kind": "stage", "id": "acceptance", "status": stage_status},
        ],
    }


def canonical_event_text(event):
    event_id, text = CanonicalExactRevisionGitHubFeatureEventGateway._validate_event(
        event,
        feature_id=FEATURE,
        expected_revision=REV,
    )
    require(event_id == EVENT_ID, event_id)
    return text


def runtime():
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository="dream-xin/control-fixture", state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )


def release_gateway(fake):
    return build_release_decision_event_gateway(
        token="trusted-event-writer",
        repository=REPO,
        default_branch="main",
        feature_refs={FEATURE: REF},
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )


def start_and_translate(store, event):
    started = store.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=REV,
            idempotency_key="vertical-persist-gateway-fixture",
            occurred_at=NOW,
            trusted_context_digest="vertical-persist-fixture",
            operation_profile=VERTICAL_PROFILE,
        )
    )
    operation_id = str(started.result["operation_id"])
    payload = {
        "feature_event_id": event["id"],
        "feature_event_digest": digest_json(event),
        "feature_event": dict(event),
        "feature_revision": REV,
        "feature_stage": "acceptance",
        "feature_manifest_digest": "manifest-fixture",
        "candidate_head_sha": None,
        "target_ref": REF,
    }
    store.commit_replanned(
        lambda snapshot: plan_operation_fact(
            snapshot,
            operation_id=operation_id,
            generation=0,
            event_type="feature.event.translated",
            payload=payload,
            occurred_at=NOW,
            trusted_context_digest="vertical-persist-fixture",
        )
    )
    return operation_id, payload


def linearize(store, operation_id):
    common = {
        "operation_id": operation_id,
        "generation": 0,
        "feature_event_id": EVENT_ID,
        "expected_revision": REV,
        "target_ref": REF,
        "candidate_head_sha": None,
        "occurred_at": NOW,
        "trusted_context_digest": "vertical-persist-fixture",
    }
    store.commit_replanned(lambda snapshot: plan_vertical_persist_requested(snapshot, **common))
    store.commit_replanned(lambda snapshot: plan_vertical_persist_linearized(snapshot, **common))


def assert_code(code, fn):
    try:
        fn()
    except Exception as exc:
        require(getattr(exc, "code", None) == code, (code, type(exc).__name__, str(exc)))
    else:
        raise AssertionError(f"expected {code}")


def main():
    store = runtime()
    fake = HistoryFakeGitHub()
    event = exact_event()
    operation_id, _ = start_and_translate(store, event)

    unlinearized = DurableVerticalFeaturePersistGateway(
        runtime=store,
        event_gateway=release_gateway(fake),
    )
    assert_code(
        "POLICY_DENIED",
        lambda: unlinearized.persist_feature_event(event=event, target_ref=REF),
    )
    require(fake.put_count == 0, "translated-but-unlinearized Event reached GitHub write transport")

    linearize(store, operation_id)

    conflicting = exact_event(stage_status="DONE")
    linearized_adapter = DurableVerticalFeaturePersistGateway(
        runtime=store,
        event_gateway=release_gateway(fake),
    )
    assert_code(
        "CONFLICT",
        lambda: linearized_adapter.persist_feature_event(event=conflicting, target_ref=REF),
    )
    require(fake.put_count == 0, "conflicting Event body reached GitHub write transport")

    # Simulate trusted Persist having applied the exact Event, the inbox file
    # being cleaned up, then two later Events advancing the Feature further.
    # Current #247 semantics require the cleanup-safe receipt to prove the exact
    # historical canonical bytes/digest rather than trust event-id membership.
    fake.history_texts = [canonical_event_text(event)]
    fake.event_text = None
    fake.event_sha = None
    fake.manifest["applied_events"] = [EVENT_ID, "EVT-LATER-1", "EVT-LATER-2"]
    fake.manifest["revision"] = REV + 3

    # New process: new adapter and new release gateway instances. No in-memory
    # event->revision map survives; recovery must come from durable Store facts
    # plus trusted immutable Git history for the exact Event bytes.
    restarted = DurableVerticalFeaturePersistGateway(
        runtime=store,
        event_gateway=release_gateway(fake),
    )
    receipt = restarted.lookup_feature_event(event_id=EVENT_ID, target_ref=REF)
    require(receipt == {"event_id": EVENT_ID, "result_revision": REV + 1}, receipt)
    require(fake.history_lookup_count == 1, "restart receipt did not prove historical exact Event digest")
    require(fake.put_count == 0, "restart receipt lookup recreated an already-applied Event")

    # A conflicting second durable translation for the same Event id must block
    # before any external lookup/write rather than choose one arbitrarily.
    bad_event = exact_event(stage_status="DONE")
    store.commit_replanned(
        lambda snapshot: plan_operation_fact(
            snapshot,
            operation_id=operation_id,
            generation=0,
            event_type="feature.event.translated",
            payload={
                "feature_event_id": EVENT_ID,
                "feature_event_digest": digest_json(bad_event),
                "feature_event": bad_event,
                "feature_revision": REV,
                "feature_stage": "acceptance",
                "feature_manifest_digest": "manifest-fixture",
                "candidate_head_sha": None,
                "target_ref": REF,
            },
            occurred_at="2026-08-11T06:00:01Z",
            trusted_context_digest="vertical-persist-fixture",
        )
    )
    before_lookups = fake.event_lookup_count
    conflicted_restart = DurableVerticalFeaturePersistGateway(
        runtime=store,
        event_gateway=release_gateway(fake),
    )
    assert_code(
        "INTERNAL_FAILURE",
        lambda: conflicted_restart.lookup_feature_event(event_id=EVENT_ID, target_ref=REF),
    )
    require(fake.event_lookup_count == before_lookups, "conflicting durable facts reached external Event lookup")

    print("Vertical Feature Persist gateway validation passed")
    print("- translated Event alone cannot bypass Persist linearization")
    print("- external Event body must match the unique durable translated fact")
    print("- fresh-process lookup reconstructs expected revision from protected Store")
    print("- inbox cleanup + later Feature advances prove exact historical digest and result_revision")
    print("- conflicting durable translated facts fail before external lookup/write")


if __name__ == "__main__":
    main()
