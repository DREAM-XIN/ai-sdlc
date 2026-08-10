#!/usr/bin/env python3
"""Recovery/fence validation for the v0.3 vertical Operator loop."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from operator_store import StoreCommandError, plan_needs_user, plan_operation_start
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_controller import VerticalLoopResumeBackend, select_vertical_action
from operator_vertical_store import (
    plan_vertical_persist_confirmed,
    plan_vertical_persist_linearized,
    plan_vertical_persist_requested,
    plan_vertical_semantic_reservation,
    vertical_projection,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-10T06:10:00Z"
REPO = "dream-xin/ai-sdlc"
FEATURE = "F-VERTICAL-RECOVERY-0001"
HEAD = "c" * 40


def _apply(snapshot, plan, sha):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=sha)


def _start(profile=VERTICAL_PROFILE):
    snapshot = StoreSnapshot(ref_sha="s0")
    plan = plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        idempotency_key="idem",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=profile,
    )
    return _apply(snapshot, plan, "s1"), plan.result["operation_id"]


def validate_cross_revision_fence():
    snapshot, operation_id = _start()
    common = dict(
        operation_id=operation_id,
        generation=0,
        feature_event_id="EVT-ADVANCE-1",
        expected_revision=10,
        target_ref="feature/test",
        candidate_head_sha=HEAD,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, plan_vertical_persist_requested(snapshot, **common), "s2")
    snapshot = _apply(snapshot, plan_vertical_persist_linearized(snapshot, **common), "s3")
    confirm = dict(common)
    confirm["result_revision"] = 11
    snapshot = _apply(snapshot, plan_vertical_persist_confirmed(snapshot, **confirm), "s4")
    assert vertical_projection(snapshot, operation_id)["expected_feature_revision"] == 11
    plan_vertical_semantic_reservation(
        snapshot,
        operation_id=operation_id,
        generation=0,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=11,
        current_stage="code-review",
        task_identity="vertical:code-review:" + HEAD,
        role="reviewer",
        candidate_head_sha=HEAD,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    try:
        plan_vertical_semantic_reservation(
            snapshot,
            operation_id=operation_id,
            generation=0,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=10,
            current_stage="code-review",
            task_identity="stale",
            role="reviewer",
            candidate_head_sha=HEAD,
            occurred_at=NOW,
            trusted_context_digest="trusted",
        )
        raise AssertionError("stale pre-Persist Feature fence unexpectedly accepted")
    except StoreCommandError as exc:
        assert exc.code == "STALE_REVISION"


def validate_needs_user_stops_effects():
    snapshot, operation_id = _start()
    plan = plan_needs_user(
        snapshot,
        operation_id=operation_id,
        generation=0,
        reason_code="USER_INPUT",
        summary="approval required",
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, plan, "s2")
    assert vertical_projection(snapshot, operation_id)["status"] == "NEEDS_USER"
    try:
        plan_vertical_semantic_reservation(
            snapshot,
            operation_id=operation_id,
            generation=0,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=10,
            current_stage="implementation",
            task_identity="must-not-run",
            role="developer",
            candidate_head_sha=HEAD,
            occurred_at=NOW,
            trusted_context_digest="trusted",
        )
        raise AssertionError("semantic effect unexpectedly planned after NEEDS_USER")
    except StoreCommandError as exc:
        assert exc.code == "NEEDS_USER"


def validate_rereview_identity():
    manifest = {
        "protocol_version": "0.1.0",
        "revision": 22,
        "feature": {"id": FEATURE, "title": "recovery", "risk": "high"},
        "workflow": {
            "profile": "standard-feature",
            "status": "ACTIVE",
            "current_stage": "code-review",
            "stages": [
                {"id": "implementation", "status": "DONE"},
                {"id": "code-review", "status": "WORKING", "gate": "code-gate"},
                {"id": "verification", "status": "TODO", "gate": "verification-gate"},
                {"id": "acceptance", "status": "TODO", "gate": "release-gate"},
            ],
        },
        "tasks": [
            {
                "id": FEATURE + "-CODE-REMEDIATION-ABC",
                "kind": "remediation",
                "stage": "implementation",
                "role": "developer",
                "source_stage": "code-review",
                "feedback": "fix",
                "status": "DONE",
            }
        ],
        "artifacts": [{"id": "implementation-v1", "type": "implementation", "uri": "docs/i.md", "status": "draft"}],
        "gates": [
            {"id": "code-gate", "status": "PENDING"},
            {"id": "verification-gate", "status": "PENDING"},
            {"id": "release-gate", "status": "PENDING"},
        ],
        "evidence": [],
        "applied_events": [],
        "updated_at": NOW,
    }
    feature = FeatureSnapshot.from_manifest(
        repository=REPO,
        target_ref="feature/test",
        manifest=manifest,
        candidate_pr_number=1,
        candidate_head_sha=HEAD,
    )
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    assert action.step == "CODE_REREVIEW"
    assert action.task_identity == f"vertical:code-rereview:{FEATURE}-CODE-REMEDIATION-ABC:{HEAD}"


def validate_canonical_profile_injection_rejected():
    schema = json.loads(
        (ROOT / "spec" / "operator" / "capabilities" / "operation-start.request.schema.json").read_text(encoding="utf-8")
    )
    errors = list(Draft202012Validator(schema).iter_errors({"operation_profile": VERTICAL_PROFILE}))
    assert errors, "canonical operation.start payload unexpectedly accepts operation_profile"


def validate_legacy_resume_fails_closed():
    snapshot, operation_id = _start(profile=None)

    class Backend:
        repository = REPO
        state_ref = "refs/heads/ai-sdlc-operator-state"
        def read_snapshot(self): return snapshot

    class Runtime:
        backend = Backend()
        def clock(self): return NOW

    class FeatureGateway:
        def read_feature(self, *, operation_id): raise AssertionError("legacy profile should fail before Feature read")

    class Executor:
        def advance_until_stop(self, *, operation_id): raise AssertionError("legacy profile should never execute")

    backend = VerticalLoopResumeBackend(runtime=Runtime(), feature_gateway=FeatureGateway(), executor=Executor())
    try:
        backend.invoke({"context": {"operation_id": operation_id, "expected_feature_revision": 10}}, {})
        raise AssertionError("legacy unprofiled Operation unexpectedly resumed vertically")
    except VerticalInvariantError as exc:
        assert exc.code == "CAPABILITY_UNAVAILABLE"


def main():
    validate_cross_revision_fence()
    validate_needs_user_stops_effects()
    validate_rereview_identity()
    validate_canonical_profile_injection_rejected()
    validate_legacy_resume_fails_closed()
    print("Operator vertical recovery validation passed")


if __name__ == "__main__":
    main()
