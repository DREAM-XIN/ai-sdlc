#!/usr/bin/env python3
"""Focused Decision remediation validation: takeover/cancel plus cross-repo policy scope."""
from __future__ import annotations

import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_decision_policy import DECISION_POLICY_SCHEMA, ProtectedDecisionPolicyVerifier, VerifiedDecisionPolicy
from operator_decisions_notifications import (
    build_operator_inbox,
    list_decisions,
    plan_consume_decision_authorization,
    plan_decision_request,
    plan_decision_response,
    rebuild_decision,
)
from operator_store import StoreCommandError, plan_cancel, plan_operation_start, plan_takeover
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, digest_json
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE

REPO = "dream-xin/ai-sdlc"
FEATURE = "F-TAKEOVER-DECISION"
REF = "feature/F-TAKEOVER-DECISION"
HEAD = "a" * 40


def apply(snapshot, plan, sha):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=sha)


def feature(repository=REPO):
    return FeatureSnapshot(
        repository=repository,
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


def assert_code(code, fn):
    try:
        fn()
    except Exception as exc:
        assert getattr(exc, "code", None) == code, (code, type(exc).__name__, str(exc))
    else:
        raise AssertionError(f"expected {code}")


def test_pending_takeover_and_cancel():
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


def test_resolved_authorization_takeover_fence():
    snapshot, operation_id, decision_id = pending_snapshot("resolved-takeover")
    response = plan_decision_response(
        snapshot,
        decision_id=decision_id,
        selected_choice="approve",
        responder_identity="user-1",
        responder_client="chatgpt",
        occurred_at="2026-08-11T00:02:00Z",
        trusted_feature=feature(),
        current_policy=policy(),
        trusted_context_digest="trusted",
    )
    snapshot = apply(snapshot, response, "resolved")
    snapshot = apply(
        snapshot,
        plan_takeover(snapshot, operation_id=operation_id, occurred_at="2026-08-11T00:03:00Z", trusted_context_digest="trusted"),
        "takeover-resolved",
    )
    before = copy.deepcopy(snapshot.files)
    assert_code(
        "SUPERSEDED_GENERATION",
        lambda: plan_consume_decision_authorization(
            snapshot,
            decision_id=decision_id,
            expected_action="resume-exact-operation",
            consumer_identity="trusted-orchestrator",
            occurred_at="2026-08-11T00:04:00Z",
            trusted_feature=feature(),
            current_policy=policy(),
            trusted_context_digest="trusted",
        ),
    )
    assert snapshot.files == before


def test_cross_repository_policy_scope():
    control = "dream-xin/control-plane"
    target = "dream-xin/target-project"
    unauthorized = "dream-xin/untrusted-project"
    base = {
        "schema_version": DECISION_POLICY_SCHEMA,
        "repository": control,
        "state_ref": "refs/heads/ai-sdlc-operator-state",
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": "protected://decision-policy",
        "policy_epoch": "epoch-cross-repo",
        "allowed_target_repositories": [target],
        "decision_types": {
            "NEEDS_AUTHORIZATION": {
                "choices": {"approve": "resume-exact-operation", "deny": "remain-blocked"},
                "allowed_responders": ["user-1", "user-2"],
                "ttl_seconds": 600,
                "warning_seconds": 120,
            }
        },
    }
    base["policy_digest"] = digest_json(base)
    restriction_calls = []

    def restriction_loader(repository, feature_id, target_ref):
        restriction_calls.append((repository, feature_id, target_ref))
        return {"allowed_choices": ["approve"], "allowed_responders": ["user-1"]}

    verifier = ProtectedDecisionPolicyVerifier(
        repository=control,
        state_ref="refs/heads/ai-sdlc-operator-state",
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda repository, state_ref, profile: copy.deepcopy(base),
        feature_restriction_loader=restriction_loader,
    )
    current = verifier.verify_current(
        target_repository=target,
        feature_id=FEATURE,
        target_ref=REF,
        decision_type="NEEDS_AUTHORIZATION",
    )
    assert current.allowed_choices == ("approve",)
    assert current.allowed_responders == frozenset({"user-1"})
    assert restriction_calls == [(target, FEATURE, REF)]

    assert_code(
        "POLICY_DENIED",
        lambda: verifier.verify_current(
            target_repository=unauthorized,
            feature_id=FEATURE,
            target_ref=REF,
            decision_type="NEEDS_AUTHORIZATION",
        ),
    )

    # Legacy policy without an explicit target allowlist remains fail-closed:
    # it authorizes only the trusted control repository itself, never a new target.
    legacy = copy.deepcopy(base)
    legacy.pop("allowed_target_repositories")
    legacy["policy_digest"] = digest_json({key: value for key, value in legacy.items() if key != "policy_digest"})
    legacy_verifier = ProtectedDecisionPolicyVerifier(
        repository=control,
        state_ref="refs/heads/ai-sdlc-operator-state",
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda repository, state_ref, profile: copy.deepcopy(legacy),
    )
    assert_code(
        "POLICY_DENIED",
        lambda: legacy_verifier.verify_current(
            target_repository=target,
            feature_id=FEATURE,
            target_ref=REF,
            decision_type="NEEDS_AUTHORIZATION",
        ),
    )


def main():
    test_pending_takeover_and_cancel()
    test_resolved_authorization_takeover_fence()
    test_cross_repository_policy_scope()
    print("Operator Decision takeover/cancel/cross-repo remediation validation passed")


if __name__ == "__main__":
    main()
