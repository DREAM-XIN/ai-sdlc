#!/usr/bin/env python3
"""Deterministic validation for the v0.3 vertical Operator loop."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from apply_feature_event import apply_event
from operator_store import StoreCommandError, plan_operation_start
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, rebuild_projection
from operator_vertical import (
    FeatureSnapshot,
    RoleIndependencePolicy,
    TrustedDispatchContext,
    VERTICAL_PROFILE,
    VerticalInvariantError,
    translate_result,
    validate_worker_result,
)
from operator_vertical_controller import select_vertical_action

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "spec" / "operator" / "vertical"
NOW = "2026-08-10T06:00:00Z"
REPO = "dream-xin/ai-sdlc"
FEATURE = "F-VERTICAL-TEST-0001"
HEAD = "a" * 40


def _apply_store(snapshot, plan):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha="next")


def _base_manifest():
    return {
        "protocol_version": "0.1.0",
        "revision": 10,
        "feature": {"id": FEATURE, "title": "vertical test", "risk": "high", "issue": "#1"},
        "workflow": {
            "profile": "standard-feature",
            "status": "ACTIVE",
            "current_stage": "implementation",
            "stages": [
                {"id": "requirement", "status": "DONE"},
                {"id": "requirement-review", "status": "DONE", "gate": "requirement-gate"},
                {"id": "design", "status": "DONE"},
                {"id": "design-review", "status": "DONE", "gate": "design-gate"},
                {"id": "plan", "status": "DONE"},
                {"id": "implementation", "status": "WORKING"},
                {"id": "code-review", "status": "TODO", "gate": "code-gate"},
                {"id": "verification", "status": "TODO", "gate": "verification-gate"},
                {"id": "acceptance", "status": "TODO", "gate": "release-gate"},
            ],
        },
        "tasks": [],
        "artifacts": [],
        "gates": [
            {"id": "requirement-gate", "status": "PASS", "evidence": ["req-review"]},
            {"id": "design-gate", "status": "PASS", "evidence": ["design-review"]},
            {"id": "code-gate", "status": "PENDING"},
            {"id": "verification-gate", "status": "PENDING"},
            {"id": "release-gate", "status": "PENDING"},
        ],
        "evidence": [
            {"id": "req-review", "type": "review", "status": "pass", "uri": "docs/req-review.md"},
            {"id": "design-review", "type": "review", "status": "pass", "uri": "docs/design-review.md"},
        ],
        "applied_events": [],
        "updated_at": NOW,
    }


def _feature(manifest, head=HEAD):
    return FeatureSnapshot.from_manifest(
        repository=REPO,
        target_ref="feature/test",
        manifest=manifest,
        candidate_pr_number=1,
        candidate_head_sha=head,
    )


def _context(feature, *, role, task_id, dispatch_id, worker_identity, semantic="b" * 64):
    return TrustedDispatchContext(
        operation_id="op-test",
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=semantic,
        external_dispatch_key="dispatch-test",
        dispatch_id=dispatch_id,
        runtime_receipt_identity="runtime-receipt",
        target_repository=REPO,
        target_ref=feature.target_ref,
        feature_id=FEATURE,
        expected_revision=feature.revision,
        feature_stage=feature.current_stage,
        task_id=task_id,
        role=role,
        candidate_pr_number=feature.candidate_pr_number,
        candidate_head_sha=feature.candidate_head_sha,
        worker_identity=worker_identity,
        collector_identity="collector-1",
    )


def _receipt(context, *, label, kind, data):
    return {
        "output_id": f"out-{label}",
        "label": label,
        "kind": kind,
        "media_type": "text/markdown",
        "trusted_uri": f"docs/features/{FEATURE}/worker-runs/{context.dispatch_id}/{label}.md",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "operation_id": context.operation_id,
        "operation_generation": context.operation_generation,
        "operation_profile": context.operation_profile,
        "semantic_effect_key": context.semantic_effect_key,
        "external_dispatch_key": context.external_dispatch_key,
        "dispatch_id": context.dispatch_id,
        "worker_role": context.role,
        "worker_identity": context.worker_identity,
        "target_repository": REPO,
        "feature_id": FEATURE,
        "expected_revision": context.expected_revision,
        "candidate_head_sha": context.candidate_head_sha,
        "collector_identity": context.collector_identity,
        "collected_at": NOW,
    }


def _loader(blobs):
    return lambda uri: blobs[uri]


def validate_schemas():
    for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def validate_profile_binding():
    empty = StoreSnapshot(ref_sha="root")
    vertical_plan = plan_operation_start(
        empty,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        idempotency_key="idem-1",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = _apply_store(empty, vertical_plan)
    operation_id = vertical_plan.result["operation_id"]
    projection = rebuild_projection(snapshot, operation_id)
    assert projection["operation_profile"] == VERTICAL_PROFILE
    try:
        plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=10,
            idempotency_key="idem-2",
            occurred_at=NOW,
            trusted_context_digest="trusted",
            operation_profile=None,
        )
        raise AssertionError("profile-conflicting active start unexpectedly converged")
    except StoreCommandError as exc:
        assert exc.code == "ALREADY_CLAIMED"

    legacy = StoreSnapshot(ref_sha="legacy")
    legacy_plan = plan_operation_start(
        legacy,
        target_repository=REPO,
        feature_id="F-LEGACY",
        expected_revision=1,
        idempotency_key="legacy",
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    legacy = _apply_store(legacy, legacy_plan)
    assert rebuild_projection(legacy, legacy_plan.result["operation_id"])["operation_profile"] is None


def validate_worker_authority_rejection():
    payload = {"status": "COMPLETED", "summary": "done", "outputs": [], "uri": "state/features/x.yaml"}
    try:
        validate_worker_result("developer", payload)
        raise AssertionError("Worker authority-bearing URI unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "INVALID_REQUEST"

    proposed = {"verdict": "PASS", "summary": "ok", "findings": [], "outputs": [], "proposed_events": []}
    try:
        validate_worker_result("reviewer", proposed)
        raise AssertionError("Worker proposed_events unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "INVALID_REQUEST"


def validate_happy_path():
    manifest = _base_manifest()
    feature = _feature(manifest)
    developer = _context(feature, role="developer", task_id=f"vertical:implementation:{feature.revision}", dispatch_id="dev-1", worker_identity="dev-worker")
    dev_payload = {
        "status": "COMPLETED",
        "summary": "implementation complete",
        "candidate_head_sha": HEAD,
        "outputs": [
            {"label": "implementation", "kind": "artifact"},
            {"label": "implementation-verification", "kind": "evidence"},
        ],
    }
    blobs = {}
    dev_receipts = []
    for label, kind in (("implementation", "artifact"), ("implementation-verification", "evidence")):
        data = label.encode()
        receipt = _receipt(developer, label=label, kind=kind, data=data)
        blobs[receipt["trusted_uri"]] = data
        dev_receipts.append(receipt)
    event = translate_result(
        context=developer,
        feature=feature,
        worker_payload=dev_payload,
        receipts=dev_receipts,
        independence_policy=RoleIndependencePolicy(),
        occurred_at=NOW,
        content_loader=_loader(blobs),
    )
    applied = apply_event(manifest, event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]
    assert manifest["workflow"]["current_stage"] == "code-review"
    assert next(row for row in manifest["workflow"]["stages"] if row["id"] == "code-review")["status"] == "READY"

    feature = _feature(manifest)
    start_review = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert start_review.kind == "persist"
    applied = apply_event(manifest, start_review.feature_event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]

    feature = _feature(manifest)
    review_action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert review_action.step == "CODE_REVIEW" and review_action.role == "reviewer"
    reviewer = _context(feature, role="reviewer", task_id=review_action.task_id, dispatch_id="review-1", worker_identity="review-worker")
    review_payload = {
        "verdict": "PASS",
        "summary": "review passed",
        "findings": [],
        "outputs": [{"label": "code-review", "kind": "evidence"}],
    }
    review_data = b"review"
    review_receipt = _receipt(reviewer, label="code-review", kind="evidence", data=review_data)
    event = translate_result(
        context=reviewer,
        feature=feature,
        worker_payload=review_payload,
        receipts=[review_receipt],
        independence_policy=RoleIndependencePolicy(developer_identity="dev-worker"),
        occurred_at=NOW,
        content_loader=_loader({review_receipt["trusted_uri"]: review_data}),
    )
    applied = apply_event(manifest, event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]
    assert next(row for row in manifest["gates"] if row["id"] == "code-gate")["status"] == "PASS"

    feature = _feature(manifest)
    start_qa = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert start_qa.kind == "persist" and start_qa.step == "VERIFICATION_QA"
    applied = apply_event(manifest, start_qa.feature_event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]

    feature = _feature(manifest)
    qa_action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    qa = _context(feature, role="qa", task_id=qa_action.task_id, dispatch_id="qa-1", worker_identity="qa-worker")
    qa_payload = {
        "verdict": "PASS",
        "summary": "verification passed",
        "checks": [{"code": "unit", "status": "PASS", "summary": "ok"}],
        "outputs": [{"label": "verification", "kind": "evidence"}],
    }
    qa_data = b"verification"
    qa_receipt = _receipt(qa, label="verification", kind="evidence", data=qa_data)
    event = translate_result(
        context=qa,
        feature=feature,
        worker_payload=qa_payload,
        receipts=[qa_receipt],
        independence_policy=RoleIndependencePolicy(developer_identity="dev-worker", reviewer_identity="review-worker"),
        occurred_at=NOW,
        content_loader=_loader({qa_receipt["trusted_uri"]: qa_data}),
    )
    applied = apply_event(manifest, event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]
    feature = _feature(manifest)
    done = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert done.kind == "done"
    assert feature.stages["acceptance"] == "READY"
    assert feature.gates["release-gate"] == "PENDING"
    assert manifest["workflow"]["status"] == "ACTIVE"


def validate_rework_and_independence():
    manifest = _base_manifest()
    manifest["workflow"]["stages"][5]["status"] = "DONE"
    manifest["workflow"]["stages"][6]["status"] = "WORKING"
    manifest["workflow"]["current_stage"] = "code-review"
    manifest["artifacts"] = [{"id": "implementation-v1", "type": "implementation", "uri": "docs/implementation.md", "status": "draft"}]
    feature = _feature(manifest)
    reviewer = _context(feature, role="reviewer", task_id="vertical:code-review:" + HEAD, dispatch_id="review-rework", worker_identity="review-worker")
    payload = {
        "verdict": "REWORK",
        "summary": "needs changes",
        "findings": [{"severity": "MAJOR", "code": "M1", "summary": "fix durable binding"}],
        "outputs": [{"label": "review-rework", "kind": "evidence"}],
    }
    data = b"rework"
    receipt = _receipt(reviewer, label="review-rework", kind="evidence", data=data)
    event = translate_result(
        context=reviewer,
        feature=feature,
        worker_payload=payload,
        receipts=[receipt],
        independence_policy=RoleIndependencePolicy(developer_identity="dev-worker"),
        occurred_at=NOW,
        content_loader=_loader({receipt["trusted_uri"]: data}),
    )
    applied = apply_event(manifest, event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]
    feature = _feature(manifest)
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert action.kind == "persist" and action.step == "CODE_REMEDIATION"
    applied = apply_event(manifest, action.feature_event)
    assert applied["outcome"] == "APPLIED", applied
    manifest = applied["manifest"]
    feature = _feature(manifest)
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert action.role == "developer" and action.step == "CODE_REMEDIATION"

    try:
        bad_reviewer = _context(feature, role="reviewer", task_id="x", dispatch_id="bad", worker_identity="dev-worker")
        RoleIndependencePolicy(developer_identity="dev-worker").verify(bad_reviewer)
        raise AssertionError("same Developer/Reviewer identity unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "POLICY_DENIED"


def main():
    validate_schemas()
    validate_profile_binding()
    validate_worker_authority_rejection()
    validate_happy_path()
    validate_rework_and_independence()
    print("Operator vertical loop validation passed")


if __name__ == "__main__":
    main()
