#!/usr/bin/env python3
"""Focused stale-Decision validation across takeover/cancel."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_decision_policy import VerifiedDecisionPolicy
from operator_decisions_notifications import build_operator_inbox, list_decisions, plan_decision_request, rebuild_decision
from operator_store import plan_cancel, plan_operation_start, plan_takeover
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE

REPO = "dream-xin/ai-sdlc"
FEATURE = "F-TAKEOVER-DECISION"
REF = "feature/F-TAKEOVER-DECISION"
HEAD = "a" * 40


def apply(snapshot, plan, sha):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=sha)


def feature():
    return FeatureSnapshot(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        revision=10,
        manifest_digest="manifest",
        current_stage="implementation",
        stages={"implementation": "WORKING"},
        gates={},
        remediation_tasks=tuple(),
        artifacts=tuple(),
        candidate_pr_number=230,
        candidate_head_sha=HEAD,
    )


def policy():
    return VerifiedDecisionPolicy(
        policy_ref="protected://decision-policy",
        policy_epoch="epoch-1",
        policy_digest="1" * 64,
        base_policy_digest="b" * 64,
        decision_type="NEEDS_AUTHORIZATION",
        allowed_choices=("approve",),
        choice_actions={"approve": "resume-exact-operation"},
        allowed_responders=frozenset({"user-1"}),
        ttl_seconds=600,
        warning_seconds=120,
    )


def pending_snapshot(idempotency_key):
    snapshot = StoreSnapshot(ref_sha=None, files={})
    start = plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=10,
        idempotency_key=idempotency_key,
        occurred_at="2026-08-11T00:00:00Z",
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = apply(snapshot, start, "start")
    operation_id = start.result["operation_id"]
    request = plan_decision_request(
        snapshot,
        feature=feature(),
        operation_id=operation_id,
        generation=0,
        decision_type="NEEDS_AUTHORIZATION",
        request_key="authorization",
        policy=policy(),
        requested_by="trusted-orchestrator",
        occurred_at="2026-08-11T00:01:00Z",
        trusted_context_digest="trusted",
    )
    snapshot = apply(snapshot, request, "decision")
    return snapshot, operation_id, request.result["decision_id"]


def assert_hidden(snapshot, decision_id):
    assert rebuild_decision(snapshot, decision_id)["status"] == "SUPERSEDED"
    assert list_decisions(snapshot, repositories={REPO}, pending_only=True) == []
    assert build_operator_inbox(snapshot, repositories={REPO})["decisions"] == []


def main():
    snapshot, operation_id, decision_id = pending_snapshot("takeover")
    snapshot = apply(
        snapshot,
        plan_takeover(snapshot, operation_id=operation_id, occurred_at="2026-08-11T00:02:00Z", trusted_context_digest="trusted"),
        "takeover",
    )
    assert_hidden(snapshot, decision_id)

    snapshot, operation_id, decision_id = pending_snapshot("cancel")
    snapshot = apply(
        snapshot,
        plan_cancel(snapshot, operation_id=operation_id, reason="cancelled", occurred_at="2026-08-11T00:02:00Z", trusted_context_digest="trusted"),
        "cancel",
    )
    assert_hidden(snapshot, decision_id)
    print("Operator stale Decision takeover/cancel validation passed")


if __name__ == "__main__":
    main()
