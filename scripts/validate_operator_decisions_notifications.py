#!/usr/bin/env python3
"""Deterministic adversarial validation for durable Decisions, Notifications and inbox."""
from __future__ import annotations

import copy
from pathlib import Path
import json
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_api import API_VERSION, dispatch
from operator_decision_backends import decision_notification_backends
from operator_decision_policy import DECISION_POLICY_SCHEMA, ProtectedDecisionPolicyVerifier, VerifiedDecisionPolicy
from operator_decisions_notifications import (
    build_operator_inbox,
    list_decisions,
    list_notifications,
    plan_authorization_expiring_notification,
    plan_decision_expiry,
    plan_decision_request,
    plan_decision_response,
    plan_notification_ack,
    plan_notification_for_operation,
    rebuild_decision,
    rebuild_notification,
)
from operator_store import StoreCommandError, _append_event, _finalize, plan_cancel, plan_operation_fact, plan_operation_start
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import StoreMutationPlan, StoreSnapshot, apply_plan_to_snapshot, digest_json, rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE

REPOSITORY = "dream-xin/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
TARGET_REF = "feature/F-TEST"
FEATURE_ID = "F-TEST"
HEAD = "a" * 40


def apply(snapshot, plan, label):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=f"{label}-{digest_json(plan.result)[:8]}")


def feature(revision=10, head=HEAD, ref=TARGET_REF):
    return FeatureSnapshot(
        repository=REPOSITORY,
        feature_id=FEATURE_ID,
        target_ref=ref,
        revision=revision,
        manifest_digest="manifest-" + str(revision),
        current_stage="implementation",
        stages={"implementation": "WORKING"},
        gates={},
        remediation_tasks=tuple(),
        artifacts=tuple(),
        candidate_pr_number=230,
        candidate_head_sha=head,
    )


def verified_policy(*, digest="1" * 64, responders=("user-1",), choices=None, epoch="epoch-1"):
    actions = choices or {"approve": "resume-exact-operation", "deny": "remain-blocked"}
    return VerifiedDecisionPolicy(
        policy_ref="protected://decision-policy",
        policy_epoch=epoch,
        policy_digest=digest,
        base_policy_digest="b" * 64,
        decision_type="NEEDS_AUTHORIZATION",
        allowed_choices=tuple(sorted(actions)),
        choice_actions=dict(actions),
        allowed_responders=frozenset(responders),
        ttl_seconds=600,
        warning_seconds=120,
    )


def started_snapshot(*, idem="idem-1"):
    snapshot = StoreSnapshot(ref_sha=None, files={})
    plan = plan_operation_start(
        snapshot,
        target_repository=REPOSITORY,
        feature_id=FEATURE_ID,
        expected_revision=10,
        idempotency_key=idem,
        occurred_at="2026-08-11T00:00:00Z",
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = apply(snapshot, plan, "start")
    return snapshot, plan.result["operation_id"]


def request(snapshot, operation_id, *, key="need-auth", policy=None, at="2026-08-11T00:01:00Z"):
    policy = policy or verified_policy()
    plan = plan_decision_request(
        snapshot,
        feature=feature(),
        operation_id=operation_id,
        generation=0,
        decision_type="NEEDS_AUTHORIZATION",
        request_key=key,
        policy=policy,
        requested_by="trusted-orchestrator",
        occurred_at=at,
        trusted_context_digest="trusted",
        summary="Approve exact bounded resume",
    )
    return apply(snapshot, plan, "decision"), plan.result["decision_id"], policy


def assert_raises(code, fn):
    try:
        fn()
    except Exception as exc:
        assert getattr(exc, "code", None) == code, (code, type(exc).__name__, str(exc))
    else:
        raise AssertionError(f"expected {code}")


def test_policy_verifier():
    base = {
        "schema_version": DECISION_POLICY_SCHEMA,
        "repository": REPOSITORY,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": "protected://decision-policy",
        "policy_epoch": "epoch-1",
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
    restriction = {"allowed_choices": ["approve"], "allowed_responders": ["user-1"], "max_ttl_seconds": 300, "warning_seconds": 60}
    verifier = ProtectedDecisionPolicyVerifier(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda repository, state_ref, profile: copy.deepcopy(base),
        feature_restriction_loader=lambda repository, feature_id, target_ref: copy.deepcopy(restriction),
    )
    current = verifier.verify_current(
        target_repository=REPOSITORY,
        feature_id=FEATURE_ID,
        target_ref=TARGET_REF,
        decision_type="NEEDS_AUTHORIZATION",
    )
    assert current.allowed_choices == ("approve",)
    assert current.allowed_responders == frozenset({"user-1"})
    assert current.ttl_seconds == 300 and current.warning_seconds == 60

    bad = dict(restriction)
    bad["allowed_choices"] = ["approve", "force"]
    expanding = ProtectedDecisionPolicyVerifier(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda repository, state_ref, profile: copy.deepcopy(base),
        feature_restriction_loader=lambda repository, feature_id, target_ref: copy.deepcopy(bad),
    )
    assert_raises(
        "POLICY_DENIED",
        lambda: expanding.verify_current(
            target_repository=REPOSITORY,
            feature_id=FEATURE_ID,
            target_ref=TARGET_REF,
            decision_type="NEEDS_AUTHORIZATION",
        ),
    )


def test_decision_lifecycle_and_adversaries():
    snapshot, operation_id = started_snapshot()
    snapshot, decision_id, policy = request(snapshot, operation_id)
    view = rebuild_decision(snapshot, decision_id)
    assert view["status"] == "PENDING"
    projection = rebuild_projection(snapshot, operation_id)
    assert projection["status"] == "NEEDS_USER"
    assert projection["pending_decisions"] == [decision_id]
    notifications = list_notifications(snapshot, repositories={REPOSITORY}, unread_only=True)
    assert len(notifications) == 1 and notifications[0]["notification_type"] == "decision.requested"

    # Generic natural-language approval is not an exact bounded choice.
    assert_raises(
        "POLICY_DENIED",
        lambda: plan_decision_response(
            snapshot,
            decision_id=decision_id,
            selected_choice="yes",
            responder_identity="user-1",
            responder_client="chatgpt",
            occurred_at="2026-08-11T00:02:00Z",
            trusted_feature=feature(),
            current_policy=policy,
            trusted_context_digest="trusted",
        ),
    )
    # Identity, revision, ref, candidate and policy drift all fail closed.
    assert_raises(
        "UNAUTHORIZED",
        lambda: plan_decision_response(
            snapshot,
            decision_id=decision_id,
            selected_choice="approve",
            responder_identity="other-user",
            responder_client="chatgpt",
            occurred_at="2026-08-11T00:02:00Z",
            trusted_feature=feature(),
            current_policy=policy,
            trusted_context_digest="trusted",
        ),
    )
    for stale in (feature(revision=11), feature(ref="feature/F-OTHER"), feature(head="c" * 40)):
        assert_raises(
            "STALE_REVISION",
            lambda stale=stale: plan_decision_response(
                snapshot,
                decision_id=decision_id,
                selected_choice="approve",
                responder_identity="user-1",
                responder_client="chatgpt",
                occurred_at="2026-08-11T00:02:00Z",
                trusted_feature=stale,
                current_policy=policy,
                trusted_context_digest="trusted",
            ),
        )
    assert_raises(
        "POLICY_DENIED",
        lambda: plan_decision_response(
            snapshot,
            decision_id=decision_id,
            selected_choice="approve",
            responder_identity="user-1",
            responder_client="chatgpt",
            occurred_at="2026-08-11T00:02:00Z",
            trusted_feature=feature(),
            current_policy=verified_policy(digest="2" * 64),
            trusted_context_digest="trusted",
        ),
    )

    plan = plan_decision_response(
        snapshot,
        decision_id=decision_id,
        selected_choice="approve",
        responder_identity="user-1",
        responder_client="chatgpt",
        occurred_at="2026-08-11T00:02:00Z",
        trusted_feature=feature(),
        current_policy=policy,
        trusted_context_digest="trusted",
    )
    snapshot = apply(snapshot, plan, "respond")
    resolved = rebuild_decision(snapshot, decision_id)
    assert resolved["status"] == "RESOLVED"
    assert resolved["response"]["responded_by_user"] == "user-1"
    assert resolved["response"]["responded_via_client"] == "chatgpt"
    assert resolved["response"]["responded_at"] == "2026-08-11T00:02:00Z"
    assert resolved["response"]["selected_choice"] == "approve"
    after = rebuild_projection(snapshot, operation_id)
    assert after["authorized_dispatches"] == [] and after["linearized_persists"] == []
    duplicate = plan_decision_response(
        snapshot,
        decision_id=decision_id,
        selected_choice="approve",
        responder_identity="user-1",
        responder_client="chatgpt",
        occurred_at="2026-08-11T00:03:00Z",
        trusted_feature=feature(),
        current_policy=policy,
        trusted_context_digest="trusted",
    )
    assert duplicate.mutations == tuple()
    assert_raises(
        "ALREADY_APPLIED",
        lambda: plan_decision_response(
            snapshot,
            decision_id=decision_id,
            selected_choice="deny",
            responder_identity="user-1",
            responder_client="chatgpt",
            occurred_at="2026-08-11T00:03:00Z",
            trusted_feature=feature(),
            current_policy=policy,
            trusted_context_digest="trusted",
        ),
    )

    # Cancel before response wins and cannot be undone by a late Decision.
    cancel_snapshot, cancel_op = started_snapshot(idem="cancel-race")
    cancel_snapshot, cancel_decision, cancel_policy = request(cancel_snapshot, cancel_op, key="cancel-auth")
    cancel_plan = plan_cancel(
        cancel_snapshot,
        operation_id=cancel_op,
        reason="user cancel",
        occurred_at="2026-08-11T00:02:00Z",
        trusted_context_digest="trusted",
    )
    cancel_snapshot = apply(cancel_snapshot, cancel_plan, "cancel")
    assert_raises(
        "CANCELLED_OPERATION",
        lambda: plan_decision_response(
            cancel_snapshot,
            decision_id=cancel_decision,
            selected_choice="approve",
            responder_identity="user-1",
            responder_client="chatgpt",
            occurred_at="2026-08-11T00:03:00Z",
            trusted_feature=feature(),
            current_policy=cancel_policy,
            trusted_context_digest="trusted",
        ),
    )


def test_expiry_reconcile_and_notifications():
    snapshot, operation_id = started_snapshot(idem="expiry")
    snapshot, decision_id, policy = request(snapshot, operation_id, key="expiry-auth")
    before = copy.deepcopy(snapshot.files)
    not_due = plan_decision_expiry(
        snapshot,
        decision_id=decision_id,
        occurred_at="2026-08-11T00:05:00Z",
        trusted_context_digest="trusted",
    )
    assert not_due.mutations == tuple() and snapshot.files == before
    # Same durable history remains PENDING regardless of wall clock; only reconcile appends expiry.
    assert rebuild_decision(snapshot, decision_id)["status"] == "PENDING"
    assert_raises(
        "POLICY_DENIED",
        lambda: plan_decision_response(
            snapshot,
            decision_id=decision_id,
            selected_choice="approve",
            responder_identity="user-1",
            responder_client="chatgpt",
            occurred_at="2026-08-11T00:11:00Z",
            trusted_feature=feature(),
            current_policy=policy,
            trusted_context_digest="trusted",
        ),
    )
    expired = plan_decision_expiry(
        snapshot,
        decision_id=decision_id,
        occurred_at="2026-08-11T00:11:00Z",
        trusted_context_digest="trusted",
    )
    snapshot = apply(snapshot, expired, "expired")
    assert rebuild_decision(snapshot, decision_id)["status"] == "EXPIRED"
    assert rebuild_projection(snapshot, operation_id)["status"] == "BLOCKED"

    warning_snapshot, warning_op = started_snapshot(idem="warning")
    warning_snapshot, warning_decision, _ = request(warning_snapshot, warning_op, key="warning-auth")
    warning = plan_authorization_expiring_notification(
        warning_snapshot,
        decision_id=warning_decision,
        occurred_at="2026-08-11T00:09:30Z",
        warning_seconds=120,
        trusted_context_digest="trusted",
    )
    warning_snapshot = apply(warning_snapshot, warning, "warning")
    warning_rows = [row for row in list_notifications(warning_snapshot, repositories={REPOSITORY}) if row["notification_type"] == "authorization.expiring"]
    assert len(warning_rows) == 1
    duplicate = plan_authorization_expiring_notification(
        warning_snapshot,
        decision_id=warning_decision,
        occurred_at="2026-08-11T00:09:40Z",
        warning_seconds=120,
        trusted_context_digest="trusted",
    )
    assert duplicate.mutations == tuple()

    # Exact idempotent acknowledgement changes only Notification delivery/read state.
    notification_id = warning_rows[0]["notification_id"]
    before_projection = rebuild_projection(warning_snapshot, warning_op)
    ack = plan_notification_ack(
        warning_snapshot,
        notification_id=notification_id,
        acknowledged_by="user-1",
        acknowledged_via_client="chatgpt",
        occurred_at="2026-08-11T00:09:45Z",
        trusted_context_digest="trusted",
    )
    warning_snapshot = apply(warning_snapshot, ack, "ack")
    assert rebuild_notification(warning_snapshot, notification_id)["status"] == "ACKNOWLEDGED"
    after_projection = rebuild_projection(warning_snapshot, warning_op)
    assert before_projection["pending_decisions"] == after_projection["pending_decisions"]
    assert before_projection["authorized_dispatches"] == after_projection["authorized_dispatches"]
    assert before_projection["linearized_persists"] == after_projection["linearized_persists"]
    repeat = plan_notification_ack(
        warning_snapshot,
        notification_id=notification_id,
        acknowledged_by="user-1",
        acknowledged_via_client="chatgpt",
        occurred_at="2026-08-11T00:09:50Z",
        trusted_context_digest="trusted",
    )
    assert repeat.mutations == tuple()


def test_operation_notifications_and_inbox_rebuild():
    blocked_snapshot, blocked_op = started_snapshot(idem="blocked")
    blocked_plan = plan_operation_fact(
        blocked_snapshot,
        operation_id=blocked_op,
        generation=0,
        event_type="loop.stable-stop",
        payload={"status": "BLOCKED", "reason": "test block"},
        occurred_at="2026-08-11T00:01:00Z",
        trusted_context_digest="trusted",
    )
    blocked_snapshot = apply(blocked_snapshot, blocked_plan, "blocked")
    notification = plan_notification_for_operation(
        blocked_snapshot,
        operation_id=blocked_op,
        notification_type="operation.blocked",
        trigger_identity="block-1",
        occurred_at="2026-08-11T00:01:01Z",
        trusted_context_digest="trusted",
        summary="Operation blocked",
    )
    blocked_snapshot = apply(blocked_snapshot, notification, "blocked-note")
    assert len([row for row in list_notifications(blocked_snapshot, repositories={REPOSITORY}) if row["notification_type"] == "operation.blocked"]) == 1

    done_snapshot, done_op = started_snapshot(idem="done")
    working, event = _append_event(
        done_snapshot,
        operation_id=done_op,
        generation=0,
        event_type="operation.done",
        occurred_at="2026-08-11T00:01:00Z",
        payload={"feature_revision": 10},
        trusted_context_digest="trusted",
        identity_material={"operation_id": done_op},
    )
    done_plan = _finalize(done_snapshot, working, [event], done_op)
    done_snapshot = apply(done_snapshot, done_plan, "done")
    completed = plan_notification_for_operation(
        done_snapshot,
        operation_id=done_op,
        notification_type="operation.completed",
        trigger_identity="done-1",
        occurred_at="2026-08-11T00:01:01Z",
        trusted_context_digest="trusted",
        summary="Operation completed",
    )
    done_snapshot = apply(done_snapshot, completed, "done-note")
    assert len([row for row in list_notifications(done_snapshot, repositories={REPOSITORY}) if row["notification_type"] == "operation.completed"]) == 1

    inbox_snapshot, inbox_op = started_snapshot(idem="inbox")
    inbox_snapshot, inbox_decision, _ = request(inbox_snapshot, inbox_op, key="inbox-auth")
    rebuilt = StoreSnapshot(ref_sha=inbox_snapshot.ref_sha, files=copy.deepcopy(inbox_snapshot.files))
    inbox = build_operator_inbox(rebuilt, repositories={REPOSITORY})
    assert inbox["operations"] == [{"operation_id": inbox_op, "generation": 0, "status": "NEEDS_USER"}]
    assert [row["decision_id"] for row in inbox["decisions"]] == [inbox_decision]
    assert len(inbox["notifications"]) == 1 and inbox["notifications"][0]["notification_type"] == "decision.requested"
    assert build_operator_inbox(rebuilt, repositories={"other/repo"}) == {"operations": [], "decisions": [], "notifications": []}


def test_canonical_backends_and_cas_replan():
    base_policy = {
        "schema_version": DECISION_POLICY_SCHEMA,
        "repository": REPOSITORY,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": "protected://decision-policy",
        "policy_epoch": "epoch-1",
        "decision_types": {
            "NEEDS_AUTHORIZATION": {
                "choices": {"approve": "resume-exact-operation", "deny": "remain-blocked"},
                "allowed_responders": ["user-1"],
                "ttl_seconds": 600,
                "warning_seconds": 120,
            }
        },
    }
    base_policy["policy_digest"] = digest_json(base_policy)
    verifier = ProtectedDecisionPolicyVerifier(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda repository, state_ref, profile: copy.deepcopy(base_policy),
    )
    snapshot, operation_id = started_snapshot(idem="api")
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF, snapshot=snapshot)
    runtime = OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T00:01:00Z",
    )

    class Gateway:
        def read_feature(self, *, operation_id):
            return feature(), {}

    backends, coordinator = decision_notification_backends(
        runtime,
        policy_verifier=verifier,
        feature_gateway=Gateway(),
        trusted_context_digest="trusted",
    )
    backend.inject_conflict_once()
    created = coordinator.request_decision(
        operation_id=operation_id,
        decision_type="NEEDS_AUTHORIZATION",
        request_key="api-auth",
        requested_by="trusted-orchestrator",
        summary="Approve exact resume",
    )
    assert created["status"] == "PENDING"
    assert len(list_decisions(backend.read_snapshot(), repositories={REPOSITORY}, pending_only=True)) == 1

    trusted = {
        "trusted_principal": "user-1",
        "trusted_client_adapter_id": "chatgpt",
        "trusted_scope": {"repositories": [REPOSITORY]},
    }
    client = {"adapter_id": "chatgpt", "human_principal": "user-1"}
    list_request = {"api_version": API_VERSION, "request_id": "list-1", "capability": "decision.list", "client_identity": client, "payload": {}}
    response = dispatch(list_request, trusted_context=trusted, backends=backends)
    assert response["ok"] is True and len(response["result"]["decisions"]) == 1
    inbox_request = {"api_version": API_VERSION, "request_id": "inbox-1", "capability": "operator.inbox", "client_identity": client, "payload": {}}
    response = dispatch(inbox_request, trusted_context=trusted, backends=backends)
    assert response["ok"] is True and len(response["result"]["decisions"]) == 1

    decision_id = created["decision_id"]
    respond_request = {
        "api_version": API_VERSION,
        "request_id": "respond-1",
        "capability": "decision.respond",
        "client_identity": client,
        "idempotency_key": "respond-idem",
        "payload": {"decision_id": decision_id, "response": "approve"},
    }
    response = dispatch(respond_request, trusted_context=trusted, backends=backends)
    assert response["ok"] is True and response["result"]["status"] == "RESOLVED"
    forged = copy.deepcopy(respond_request)
    forged["request_id"] = "respond-forged"
    forged["client_identity"]["human_principal"] = "other-user"
    response = dispatch(forged, trusted_context=trusted, backends=backends)
    assert response["ok"] is False and response["error"]["code"] == "UNAUTHORIZED"

    notes_request = {"api_version": API_VERSION, "request_id": "notes-1", "capability": "notification.list", "client_identity": client, "payload": {}}
    response = dispatch(notes_request, trusted_context=trusted, backends=backends)
    assert response["ok"] is True and response["result"]["notifications"]
    note_id = response["result"]["notifications"][0]["notification_id"]
    ack_request = {
        "api_version": API_VERSION,
        "request_id": "ack-1",
        "capability": "notification.ack",
        "client_identity": client,
        "idempotency_key": "ack-idem",
        "payload": {"notification_id": note_id},
    }
    response = dispatch(ack_request, trusted_context=trusted, backends=backends)
    assert response["ok"] is True and response["result"]["status"] == "ACKNOWLEDGED"


def main():
    for path in (ROOT / "spec" / "operator" / "store" / "decision.schema.json", ROOT / "spec" / "operator" / "store" / "notification.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
    test_policy_verifier()
    test_decision_lifecycle_and_adversaries()
    test_expiry_reconcile_and_notifications()
    test_operation_notifications_and_inbox_rebuild()
    test_canonical_backends_and_cas_replan()
    print("Operator Decisions/Notifications validation passed")
    print("- exact bounded choices; trusted principal/client/scope")
    print("- fresh Feature/policy/candidate/generation/expiry checks")
    print("- deterministic expiry reconciliation and rebuild")
    print("- durable four-type Notification Outbox + idempotent ack")
    print("- protected CAS re-plan + new-session inbox")


if __name__ == "__main__":
    main()
