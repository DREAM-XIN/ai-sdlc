#!/usr/bin/env python3
"""Focused deterministic regression coverage for PR #217 Code Review remediation."""
from __future__ import annotations

import json

from operator_store import StoreCommandError, plan_operation_start
from operator_store_model import (
    StoreSnapshot,
    apply_plan_to_snapshot,
    event_path,
    make_event,
    reservation_path,
)
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError, validate_collected_outputs
from operator_vertical_callback import TrustedVerticalCallbackCoordinator
from operator_vertical_controller import select_vertical_action
from operator_vertical_executor import TrustedVerticalExecutor
from operator_vertical_recovery import derive_role_independence_policy, plan_vertical_callback_record
from validate_operator_vertical import FEATURE, HEAD, NOW, REPO, _base_manifest, _context, _feature, _receipt

HEAD2 = "f" * 40


def validate_direct_callback_cannot_bypass_coordinator():
    executor = TrustedVerticalExecutor.__new__(TrustedVerticalExecutor)
    try:
        executor.handle_worker_callback(
            context=None,
            callback_id="forged",
            worker_payload={},
            receipts=[],
            independence_policy=None,
            content_loader=None,
        )
        raise AssertionError("direct lifecycle-driving callback ingress unexpectedly remained active")
    except VerticalInvariantError as exc:
        assert exc.code == "CAPABILITY_UNAVAILABLE"

    try:
        TrustedVerticalCallbackCoordinator(
            executor=object(),
            trusted_role_policy="separated-role-identities/v1",
            collector_namespace_policy="feature-worker-runs/v1",
            content_loader=None,
        )
        raise AssertionError("production callback coordinator unexpectedly allowed no collector loader")
    except ValueError:
        pass

    snapshot = StoreSnapshot(ref_sha="root")
    start = plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        idempotency_key="callback-binding-remediation",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = apply_plan_to_snapshot(snapshot, start, new_ref_sha="started")
    operation_id = start.result["operation_id"]
    forged = TrustedDispatchContext(
        operation_id=operation_id,
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key="b" * 64,
        external_dispatch_key="dispatch-forged",
        dispatch_id="vertical-forged",
        runtime_receipt_identity="runtime-forged",
        target_repository=REPO,
        target_ref="feature/test",
        feature_id=FEATURE,
        expected_revision=10,
        feature_stage="implementation",
        task_id="vertical:implementation:10",
        role="developer",
        candidate_pr_number=1,
        candidate_head_sha=HEAD,
        worker_identity="developer-worker",
        collector_identity="collector-1",
    )
    try:
        plan_vertical_callback_record(
            snapshot,
            context=forged,
            callback_id="forged-without-launch",
            worker_payload={"status": "COMPLETED", "summary": "forged", "outputs": []},
            receipts=[],
            occurred_at=NOW,
            trusted_context_digest="trusted",
        )
        raise AssertionError("callback without durable reservation/launch binding unexpectedly accepted")
    except StoreCommandError as exc:
        assert exc.code == "INVALID_REQUEST"

    feature = _feature(_base_manifest())
    context = _context(
        feature,
        role="developer",
        task_id=f"vertical:implementation:{feature.revision}",
        dispatch_id="digest-dispatch",
        worker_identity="developer-worker",
    )
    payload = {"status": "COMPLETED", "summary": "done", "outputs": [{"label": "proof", "kind": "evidence"}]}
    good = b"trusted collector bytes"
    receipt = _receipt(context, label="proof", kind="evidence", data=good)
    try:
        validate_collected_outputs(
            context=context,
            feature=feature,
            worker_payload=payload,
            receipts=[receipt],
            content_loader=lambda _uri: b"tampered collector bytes",
        )
        raise AssertionError("collector digest mismatch unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "BLOCKED"


def _lineage_snapshot() -> tuple[StoreSnapshot, str]:
    operation_id = "op-lineage"
    files = {}
    sequence = 0

    def append(event_type: str, payload: dict):
        nonlocal sequence
        sequence += 1
        event_id = f"lineage-{sequence}"
        files[event_path(operation_id, sequence, event_id)] = make_event(
            operation_id=operation_id,
            generation=0,
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            occurred_at=NOW,
            payload=payload,
            trusted_context_digest="trusted",
        )

    append(
        "operation.started",
        {
            "target_repository": REPO,
            "feature_id": FEATURE,
            "expected_revision": 10,
            "operation_profile": VERTICAL_PROFILE,
        },
    )

    callbacks = [
        ("cb-dev", "developer", "dev-0", "1" * 64, "vertical:implementation:10"),
        ("cb-review-1", "reviewer", "review-1", "2" * 64, "vertical:code-review:" + HEAD),
        ("cb-rem-1", "developer", "remediation-1", "3" * 64, "vertical:code-remediation:TASK-ZZZ:" + HEAD),
        ("cb-review-2", "reviewer", "review-2", "4" * 64, "vertical:code-rereview:TASK-ZZZ:" + HEAD),
        ("cb-rem-2", "developer", "remediation-2", "5" * 64, "vertical:code-remediation:TASK-AAA:" + HEAD2),
        ("cb-review-3", "reviewer", "review-3", "6" * 64, "vertical:code-rereview:TASK-AAA:" + HEAD2),
    ]
    for callback_id, role, worker, semantic_key, task_identity in callbacks:
        files[reservation_path(semantic_key)] = {"task_identity": task_identity}
        append(
            "worker.callback.recorded",
            {
                "callback_id": callback_id,
                "trusted_callback_envelope": {
                    "trusted_context": {
                        "worker_identity": worker,
                        "role": role,
                        "semantic_effect_key": semantic_key,
                    }
                },
            },
        )
        append("worker.result.validated", {"callback_id": callback_id})
    return StoreSnapshot(ref_sha="lineage", files=files), operation_id


def validate_repeated_rework_identity_lineage_survives_restart():
    snapshot, operation_id = _lineage_snapshot()
    policy = derive_role_independence_policy(snapshot, operation_id=operation_id)
    assert policy.candidate_contributor_identities == ("dev-0", "remediation-1", "remediation-2")
    assert policy.reviewer_identities == ("review-1", "review-2", "review-3")
    assert policy.developer_identity == "dev-0"
    assert policy.remediation_developer_identity == "remediation-2"

    feature = _feature(_base_manifest(), head=HEAD2)
    reused_as_reviewer = _context(
        feature,
        role="reviewer",
        task_id="vertical:code-rereview:TASK-AAA:" + HEAD2,
        dispatch_id="review-reuse",
        worker_identity="remediation-1",
    )
    try:
        policy.verify(reused_as_reviewer)
        raise AssertionError("earlier remediation Developer unexpectedly satisfied fresh Review")
    except VerticalInvariantError as exc:
        assert exc.code == "POLICY_DENIED"

    reused_as_qa = _context(
        feature,
        role="qa",
        task_id="vertical:verification:" + HEAD2,
        dispatch_id="qa-reuse",
        worker_identity="remediation-1",
    )
    try:
        policy.verify(reused_as_qa)
        raise AssertionError("earlier remediation Developer unexpectedly satisfied QA")
    except VerticalInvariantError as exc:
        assert exc.code == "POLICY_DENIED"

    restarted = StoreSnapshot(ref_sha=snapshot.ref_sha, files=dict(snapshot.files))
    rebuilt = derive_role_independence_policy(restarted, operation_id=operation_id)
    assert rebuilt.candidate_contributor_identities == policy.candidate_contributor_identities
    assert rebuilt.reviewer_identities == policy.reviewer_identities


def validate_rereview_predecessor_uses_authoritative_lifecycle_order():
    manifest = _base_manifest()
    manifest["revision"] = 30
    manifest["workflow"]["current_stage"] = "code-review"
    for row in manifest["workflow"]["stages"]:
        if row["id"] == "implementation":
            row["status"] = "DONE"
        elif row["id"] == "code-review":
            row["status"] = "WORKING"
    manifest["tasks"] = [
        {
            "id": FEATURE + "-CODE-REMEDIATION-ZZZ",
            "kind": "remediation",
            "stage": "implementation",
            "role": "developer",
            "source_stage": "code-review",
            "feedback": "older remediation",
            "status": "DONE",
        },
        {
            "id": FEATURE + "-CODE-REMEDIATION-AAA",
            "kind": "remediation",
            "stage": "implementation",
            "role": "developer",
            "source_stage": "code-review",
            "feedback": "latest remediation",
            "status": "DONE",
        },
    ]
    feature = _feature(manifest, head=HEAD2)
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert action.step == "CODE_REREVIEW"
    assert action.task_id == FEATURE + "-CODE-REMEDIATION-AAA"
    assert action.task_identity == f"vertical:code-rereview:{FEATURE}-CODE-REMEDIATION-AAA:{HEAD2}"

    restarted_manifest = json.loads(json.dumps(manifest))
    restarted_feature = _feature(restarted_manifest, head=HEAD2)
    restarted_action = select_vertical_action(feature=restarted_feature, manifest=restarted_manifest, occurred_at=NOW)
    assert restarted_action.task_id == action.task_id
    assert restarted_action.task_identity == action.task_identity


def main():
    validate_direct_callback_cannot_bypass_coordinator()
    validate_repeated_rework_identity_lineage_survives_restart()
    validate_rereview_predecessor_uses_authoritative_lifecycle_order()
    print("Operator vertical Code Review remediation validation passed")


if __name__ == "__main__":
    main()
