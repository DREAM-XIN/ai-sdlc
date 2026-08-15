#!/usr/bin/env python3
"""Fresh-process recovery validation for durable first-attempt gh-aw output leases."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from operator_store import (
    plan_authorize_launch,
    plan_dispatch_claim,
    plan_launch_lookup,
    plan_operation_start,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import (
    StoreSnapshot,
    apply_plan_to_snapshot,
    digest_json,
    operation_events,
)
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import (
    FeatureSnapshot,
    TrustedDispatchContext,
    VERTICAL_PROFILE,
    VerticalInvariantError,
    validate_worker_result,
)
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_gh_aw_collector import _build_receipts
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from operator_vertical_recovery import plan_vertical_callback_record
from operator_vertical_store import plan_vertical_semantic_reservation
from validate_operator_vertical_gh_aw_attempt_binding import (
    AttemptAwareFakeHttp,
    resolve,
    source,
)
from validate_operator_vertical_gh_aw_github_source import (
    DISPATCH_ID,
    FEATURE,
    HEAD,
    NOW,
    RUN_ID,
    TARGET,
    TARGET_REF,
    trusted_context,
)

STATE_REF = "refs/heads/ai-sdlc-operator-state"


def expect_vertical_error(callback, message):
    try:
        callback()
    except VerticalInvariantError:
        return
    raise AssertionError(message)


def receipt_for_context(src, resolved, trusted):
    context = TrustedDispatchContext(
        operation_id=str(trusted["operation_id"]),
        operation_generation=int(trusted["operation_generation"]),
        operation_profile=str(trusted["operation_profile"]),
        semantic_effect_key=str(trusted["semantic_effect_key"]),
        external_dispatch_key=str(trusted["external_dispatch_key"]),
        dispatch_id=str(trusted["dispatch_id"]),
        runtime_receipt_identity=str(RUN_ID),
        target_repository=str(trusted["target_repository"]),
        target_ref=str(trusted["target_ref"]),
        feature_id=str(trusted["feature_id"]),
        expected_revision=int(trusted["expected_revision"]),
        feature_stage=str(trusted["feature_stage"]),
        task_id=resolved.run.task_id,
        role=str(trusted["role"]),
        candidate_pr_number=resolved.run.candidate_pr_number,
        candidate_head_sha=resolved.run.candidate_head_sha,
        worker_identity=resolved.run.worker_identity,
        collector_identity=resolved.run.collector_identity,
    )
    worker_payload = validate_worker_result(context.role, resolved.role_payload)
    declared = {
        str(row["label"]): str(row["kind"])
        for row in worker_payload.get("outputs", [])
    }
    receipts = _build_receipts(
        coordinator=SimpleNamespace(content_loader=src.load_content),
        context=context,
        outputs=resolved.outputs,
        declared_outputs=declared,
        collected_at="2026-08-15T08:45:00Z",
    )
    assert len(receipts) == 1
    return context, worker_payload, receipts[0]


def receipt_for(src, resolved, role):
    return receipt_for_context(src, resolved, trusted_context(role))[2]


def durable_round_trip(receipt):
    # Model the exact callback/Store serialization boundary: fresh recovery sees
    # only durable receipt fields, never source-A process memory.
    return json.loads(json.dumps(receipt, sort_keys=True))


def validate_fresh_source_recovers_durable_receipt():
    for role in ("developer", "reviewer", "qa"):
        fake = AttemptAwareFakeHttp(role=role)
        source_a = source(fake)
        resolved = resolve(source_a, role)
        receipt = durable_round_trip(receipt_for(source_a, resolved, role))

        source_b = source(fake)
        assert source_b is not source_a
        assert source_b._exact_run_snapshots == {}, "fresh source unexpectedly inherited run lease memory"
        data = source_b.load_content(receipt["trusted_uri"])
        assert hashlib.sha256(data).hexdigest() == receipt["sha256"]
        assert source_b._exact_run_snapshots == {}, "fresh load recreated hidden process-local resolve authority"


def validate_fresh_source_rejects_rerun():
    fake = AttemptAwareFakeHttp(role="reviewer")
    source_a = source(fake)
    resolved = resolve(source_a, "reviewer")
    receipt = durable_round_trip(receipt_for(source_a, resolved, "reviewer"))
    fake.mutate_run_from_call = fake.run_reads + 1
    expect_vertical_error(
        lambda: source(fake).load_content(receipt["trusted_uri"]),
        "fresh recovery accepted a GitHub Actions rerun after callback durability",
    )


def validate_fresh_source_rejects_content_or_provenance_drift():
    developer_fake = AttemptAwareFakeHttp(role="developer")
    developer_a = source(developer_fake)
    developer_resolved = resolve(developer_a, "developer")
    developer_receipt = durable_round_trip(
        receipt_for(developer_a, developer_resolved, "developer")
    )
    changed_pr = deepcopy(developer_fake._pr())
    changed_pr["draft"] = False
    developer_fake._pr = lambda: deepcopy(changed_pr)
    expect_vertical_error(
        lambda: source(developer_fake).load_content(developer_receipt["trusted_uri"]),
        "fresh recovery accepted changed Developer PR provenance",
    )

    reviewer_fake = AttemptAwareFakeHttp(role="reviewer")
    reviewer_a = source(reviewer_fake)
    reviewer_resolved = resolve(reviewer_a, "reviewer")
    reviewer_receipt = durable_round_trip(
        receipt_for(reviewer_a, reviewer_resolved, "reviewer")
    )
    changed_comment = deepcopy(reviewer_fake._comment())
    changed_comment["user"] = {"type": "User"}
    reviewer_fake._comment = lambda: deepcopy(changed_comment)
    expect_vertical_error(
        lambda: source(reviewer_fake).load_content(reviewer_receipt["trusted_uri"]),
        "fresh recovery accepted changed Gate provenance",
    )


def validate_durable_uri_is_not_worker_rebindable():
    fake = AttemptAwareFakeHttp(role="reviewer")
    source_a = source(fake)
    resolved = resolve(source_a, "reviewer")
    receipt = durable_round_trip(receipt_for(source_a, resolved, "reviewer"))
    uri = receipt["trusted_uri"]
    assert "--first-attempt--key-" in uri and f"--run-{RUN_ID}--" in uri
    expect_vertical_error(
        lambda: source(fake).load_content(uri.replace(f"--run-{RUN_ID}--", "--run-999--")),
        "tampered durable run identity was accepted",
    )
    expect_vertical_error(
        lambda: source(fake).load_content(uri.replace("--first-attempt--key-dispatch-", "--first-attempt--key-forged-")),
        "tampered durable stable dispatch identity was accepted",
    )


def _apply(snapshot, plan, sha):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=sha)


def _durable_reviewer_launch_state():
    snapshot = StoreSnapshot(ref_sha="s0")
    start = plan_operation_start(
        snapshot,
        target_repository=TARGET,
        feature_id=FEATURE,
        expected_revision=7,
        idempotency_key="fresh-process-attempt-recovery",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = _apply(snapshot, start, "s1")
    operation_id = start.result["operation_id"]
    reservation = plan_vertical_semantic_reservation(
        snapshot,
        operation_id=operation_id,
        generation=0,
        target_repository=TARGET,
        feature_id=FEATURE,
        expected_revision=7,
        current_stage="code-review",
        task_identity="REVIEW-1",
        role="reviewer",
        candidate_head_sha=HEAD,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, reservation, "s2")
    effect_key = reservation.result["semantic_effect_key"]
    claim = plan_dispatch_claim(
        snapshot,
        operation_id=operation_id,
        generation=0,
        effect_key=effect_key,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, claim, "s3")
    external_key = claim.result["external_dispatch_key"]
    launch = plan_authorize_launch(
        snapshot,
        operation_id=operation_id,
        generation=0,
        claim_id=claim.result["claim_id"],
        dispatch_id=DISPATCH_ID,
        occurred_at=NOW,
        trusted_context_digest="trusted",
        verified_expected_revision=7,
        verified_stage="code-review",
        verified_candidate_head_sha=HEAD,
    )
    snapshot = _apply(snapshot, launch, "s4")
    lookup = plan_launch_lookup(
        snapshot,
        operation_id=operation_id,
        generation=0,
        external_dispatch_key_value=external_key,
        lookup_state="LAUNCHED",
        receipt_id=str(RUN_ID),
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    return _apply(snapshot, lookup, "s5"), operation_id, effect_key, external_key


class FixedReviewFeatureGateway:
    def read_feature(self, *, operation_id):
        return (
            FeatureSnapshot(
                repository=TARGET,
                feature_id=FEATURE,
                target_ref=TARGET_REF,
                revision=7,
                manifest_digest="f" * 64,
                current_stage="code-review",
                stages={
                    "implementation": "DONE",
                    "code-review": "WORKING",
                    "verification": "TODO",
                },
                gates={"code-gate": "PENDING"},
                remediation_tasks=tuple(),
                artifacts=(
                    {
                        "id": "implementation-fixture",
                        "type": "implementation",
                        "uri": "docs/implementation.md",
                        "status": "draft",
                    },
                ),
                candidate_pr_number=42,
                candidate_head_sha=HEAD,
            ),
            {},
        )


class CountingPersistGateway:
    def __init__(self):
        self.persist_calls = []
        self.lookup_calls = []

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls.append((dict(event), target_ref))
        return {"event_id": event["id"], "result_revision": 8}

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls.append((event_id, target_ref))
        return None


class NoDispatchGateway:
    def __init__(self):
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        self.launch_calls.append(dict(dispatch))
        raise AssertionError("durable callback recovery must not launch a second Worker")

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(external_dispatch_key)
        raise AssertionError("durable callback recovery must not reconcile a completed Worker launch")


def _events(executor, operation_id, event_type):
    return [
        row
        for row in operation_events(executor.runtime.backend.read_snapshot(), operation_id)
        if row["event_type"] == event_type
    ]


def validate_durable_callback_recovers_with_fresh_source_and_executor():
    snapshot, operation_id, effect_key, external_key = _durable_reviewer_launch_state()
    fake_a = AttemptAwareFakeHttp(
        role="reviewer",
        title=f"AI-SDLC gh-aw {external_key}",
    )
    source_a = source(fake_a)
    trusted = {
        "operation_id": operation_id,
        "operation_generation": 0,
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": effect_key,
        "external_dispatch_key": external_key,
        "dispatch_id": DISPATCH_ID,
        "target_repository": TARGET,
        "target_ref": TARGET_REF,
        "feature_id": FEATURE,
        "expected_revision": 7,
        "feature_stage": "code-review",
        "role": "reviewer",
        "launch_candidate_head_sha": HEAD,
    }
    resolved = source_a.resolve(
        external_dispatch_key=external_key,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted,
    )
    context, worker_payload, receipt = receipt_for_context(source_a, resolved, trusted)
    receipt = durable_round_trip(receipt)
    callback_id = "gh-aw-callback-" + digest_json(
        {
            "operation_id": operation_id,
            "generation": 0,
            "external_dispatch_key": external_key,
            "runtime_receipt_identity": str(RUN_ID),
            "run_id": RUN_ID,
        }
    )[:24]

    # Exact coordinator durability boundary, followed by an injected process crash:
    # callback is committed, but no result validation/translation/Persist runs in A.
    recorded = plan_vertical_callback_record(
        snapshot,
        context=context,
        callback_id=callback_id,
        worker_payload=worker_payload,
        receipts=[receipt],
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    crashed_snapshot = _apply(snapshot, recorded, "s6")
    recorded_rows = [
        row for row in operation_events(crashed_snapshot, operation_id)
        if row["event_type"] == "worker.callback.recorded"
    ]
    assert len(recorded_rows) == 1
    assert not [
        row for row in operation_events(crashed_snapshot, operation_id)
        if row["event_type"] in {"worker.result.validated", "worker.result.rejected", "feature.event.translated"}
    ]

    # Fresh process: new HTTP/source/runtime/executor objects over only the durable snapshot.
    fake_b = AttemptAwareFakeHttp(
        role="reviewer",
        title=f"AI-SDLC gh-aw {external_key}",
    )
    source_b = source(fake_b)
    assert source_b is not source_a and source_b._exact_run_snapshots == {}
    backend_b = MemoryStateRefBackend(
        repository=TARGET,
        state_ref=STATE_REF,
        snapshot=crashed_snapshot,
    )
    runtime_b = OperatorStoreRuntime(
        backend=backend_b,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )
    persist = CountingPersistGateway()
    dispatch = NoDispatchGateway()
    base_b = TrustedVerticalExecutor(
        runtime=runtime_b,
        feature_gateway=FixedReviewFeatureGateway(),
        persist_gateway=persist,
        dispatch_gateway=dispatch,
        config=TrustedVerticalExecutorConfig(
            target_ref=TARGET_REF,
            trusted_context_digest="trusted",
            max_auto_steps=4,
            effect_lineage_required=False,
            legacy_compatibility_mode=True,
        ),
    )
    fresh = TrustedRecoveringVerticalExecutor(
        base_executor=base_b,
        content_loader=source_b.load_content,
        trusted_role_policy="vertical-independent-role-policy/v1",
        collector_namespace_policy="gh-aw-first-attempt-digest-bound/v1",
    )

    assert fresh._reconcile_callback(operation_id) is True
    assert len(_events(fresh, operation_id, "worker.callback.recorded")) == 1
    assert len(_events(fresh, operation_id, "worker.result.validated")) == 1
    assert len(_events(fresh, operation_id, "worker.result.rejected")) == 0
    assert len(_events(fresh, operation_id, "feature.event.translated")) == 1
    assert len(_events(fresh, operation_id, "persist.requested")) == 1
    assert len(_events(fresh, operation_id, "persist.linearized")) == 1
    assert len(_events(fresh, operation_id, "persist.confirmed")) == 1
    assert len(persist.persist_calls) == 1
    assert len(persist.lookup_calls) == 0
    assert not dispatch.launch_calls and not dispatch.lookup_calls

    before_events = len(operation_events(fresh.runtime.backend.read_snapshot(), operation_id))
    before_ref = fresh.runtime.backend.read_snapshot().ref_sha
    assert fresh._reconcile_callback(operation_id) is None
    assert len(operation_events(fresh.runtime.backend.read_snapshot(), operation_id)) == before_events
    assert fresh.runtime.backend.read_snapshot().ref_sha == before_ref
    assert len(persist.persist_calls) == 1
    assert not dispatch.launch_calls and not dispatch.lookup_calls


def main():
    validate_fresh_source_recovers_durable_receipt()
    validate_fresh_source_rejects_rerun()
    validate_fresh_source_rejects_content_or_provenance_drift()
    validate_durable_uri_is_not_worker_rebindable()
    validate_durable_callback_recovers_with_fresh_source_and_executor()
    print("fresh-process first-attempt gh-aw receipt recovery validation passed")
    print("- durable collector URI reconstructs first-attempt run authority without source-A memory")
    print("- callback-recorded crash recovers through source B to one validated result and one exact Persist")
    print("- rerun/content/provenance drift remains fail-closed after process replacement")
    print("- duplicate fresh recovery is zero-mutation and never launches a second Worker")


if __name__ == "__main__":
    main()
