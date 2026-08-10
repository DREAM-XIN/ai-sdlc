#!/usr/bin/env python3
"""Completion-path validation for review remediation, fresh re-review, and QA."""
from __future__ import annotations

from apply_feature_event import apply_event
from operator_vertical import RoleIndependencePolicy, translate_result
from operator_vertical_controller import select_vertical_action
from validate_operator_vertical import (
    HEAD,
    NOW,
    _base_manifest,
    _context,
    _feature,
    _loader,
    _receipt,
)

HEAD2 = "e" * 40


def _apply(manifest, event):
    result = apply_event(manifest, event)
    assert result["outcome"] == "APPLIED", result
    return result["manifest"]


def _review_event(feature, *, verdict, dispatch_id, worker_identity, policy):
    context = _context(
        feature,
        role="reviewer",
        task_id=f"vertical:code-review:{feature.candidate_head_sha}",
        dispatch_id=dispatch_id,
        worker_identity=worker_identity,
    )
    payload = {
        "verdict": verdict,
        "summary": "review result",
        "findings": (
            [{"severity": "MAJOR", "code": "M1", "summary": "fix exact recovery binding"}]
            if verdict == "REWORK"
            else []
        ),
        "outputs": [{"label": "code-review", "kind": "evidence"}],
    }
    data = (dispatch_id + verdict).encode()
    receipt = _receipt(context, label="code-review", kind="evidence", data=data)
    return translate_result(
        context=context,
        feature=feature,
        worker_payload=payload,
        receipts=[receipt],
        independence_policy=policy,
        occurred_at=NOW,
        content_loader=_loader({receipt["trusted_uri"]: data}),
    )


def validate_full_remediation_rereview_qa():
    manifest = _base_manifest()
    manifest["workflow"]["stages"][5]["status"] = "DONE"
    manifest["workflow"]["stages"][6]["status"] = "WORKING"
    manifest["workflow"]["current_stage"] = "code-review"
    manifest["artifacts"] = [
        {
            "id": "implementation-v1",
            "type": "implementation",
            "uri": "docs/implementation.md",
            "status": "draft",
        }
    ]

    feature = _feature(manifest, head=HEAD)
    event = _review_event(
        feature,
        verdict="REWORK",
        dispatch_id="review-rework",
        worker_identity="review-worker-1",
        policy=RoleIndependencePolicy(developer_identity="dev-worker-1"),
    )
    manifest = _apply(manifest, event)
    remediation = next(row for row in manifest["tasks"] if row["kind"] == "remediation")

    feature = _feature(manifest, head=HEAD)
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert action.kind == "persist" and action.step == "CODE_REMEDIATION"
    manifest = _apply(manifest, action.feature_event)

    dispatch_feature = _feature(manifest, head=HEAD)
    action = select_vertical_action(feature=dispatch_feature, manifest=manifest, occurred_at=NOW)
    assert action.kind == "dispatch" and action.step == "CODE_REMEDIATION"
    assert action.candidate_head_sha == HEAD

    feature = _feature(manifest, head=HEAD2)
    developer = _context(
        feature,
        role="developer",
        task_id=remediation["id"],
        dispatch_id="remediation-1",
        worker_identity="remediation-worker",
    )
    event = translate_result(
        context=developer,
        feature=feature,
        worker_payload={
            "status": "COMPLETED",
            "summary": "remediation completed",
            "candidate_head_sha": HEAD2,
            "outputs": [],
        },
        receipts=[],
        independence_policy=RoleIndependencePolicy(
            developer_identity="dev-worker-1",
            reviewer_identity="review-worker-1",
        ),
        occurred_at=NOW,
        content_loader=_loader({}),
    )
    manifest = _apply(manifest, event)
    assert next(row for row in manifest["tasks"] if row["id"] == remediation["id"])["status"] == "DONE"

    feature = _feature(manifest, head=HEAD2)
    rereview = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert rereview.step == "CODE_REREVIEW" and rereview.candidate_head_sha == HEAD2
    reviewer = _context(
        feature,
        role="reviewer",
        task_id=remediation["id"],
        dispatch_id="review-fresh",
        worker_identity="review-worker-2",
    )
    data = b"fresh rereview"
    receipt = _receipt(reviewer, label="code-review", kind="evidence", data=data)
    event = translate_result(
        context=reviewer,
        feature=feature,
        worker_payload={
            "verdict": "PASS",
            "summary": "fresh re-review passed",
            "findings": [],
            "outputs": [{"label": "code-review", "kind": "evidence"}],
        },
        receipts=[receipt],
        independence_policy=RoleIndependencePolicy(
            developer_identity="dev-worker-1",
            reviewer_identity="review-worker-1",
            remediation_developer_identity="remediation-worker",
        ),
        occurred_at=NOW,
        content_loader=_loader({receipt["trusted_uri"]: data}),
    )
    manifest = _apply(manifest, event)
    assert next(row for row in manifest["gates"] if row["id"] == "code-gate")["status"] == "PASS"

    feature = _feature(manifest, head=HEAD2)
    start_qa = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert start_qa.kind == "persist" and start_qa.step == "VERIFICATION_QA"
    manifest = _apply(manifest, start_qa.feature_event)

    feature = _feature(manifest, head=HEAD2)
    qa_action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert qa_action.role == "qa" and qa_action.candidate_head_sha == HEAD2
    qa = _context(
        feature,
        role="qa",
        task_id=qa_action.task_id,
        dispatch_id="qa-fresh",
        worker_identity="qa-worker",
    )
    qa_data = b"verification"
    qa_receipt = _receipt(qa, label="verification", kind="evidence", data=qa_data)
    event = translate_result(
        context=qa,
        feature=feature,
        worker_payload={
            "verdict": "PASS",
            "summary": "verification passed",
            "checks": [{"code": "deterministic", "status": "PASS", "summary": "ok"}],
            "outputs": [{"label": "verification", "kind": "evidence"}],
        },
        receipts=[qa_receipt],
        independence_policy=RoleIndependencePolicy(
            developer_identity="dev-worker-1",
            reviewer_identity="review-worker-2",
            remediation_developer_identity="remediation-worker",
        ),
        occurred_at=NOW,
        content_loader=_loader({qa_receipt["trusted_uri"]: qa_data}),
    )
    manifest = _apply(manifest, event)

    feature = _feature(manifest, head=HEAD2)
    done = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert done.kind == "done"
    assert feature.stages["acceptance"] == "READY"
    assert feature.gates["release-gate"] == "PENDING"
    assert manifest["workflow"]["status"] == "ACTIVE"


def main():
    validate_full_remediation_rereview_qa()
    print("Operator vertical completion-path validation passed")


if __name__ == "__main__":
    main()
