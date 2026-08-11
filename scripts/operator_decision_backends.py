#!/usr/bin/env python3
"""Trusted canonical backends and coordinator for Decisions, Notifications and operator.inbox."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_decision_policy import ProtectedDecisionPolicyVerifier
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
from operator_store import StoreCommandError
from operator_store_backends import OperatorStoreRuntime
from operator_store_model import normalize_repository
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import FeatureTruthGateway


@dataclass(frozen=True)
class TrustedOperatorScope:
    repositories: frozenset[str]
    feature_ids: frozenset[str] | None
    principal: str
    client_adapter_id: str

    @classmethod
    def from_context(cls, trusted_context: dict[str, Any]) -> "TrustedOperatorScope":
        raw = trusted_context.get("trusted_scope")
        principal = str(trusted_context.get("trusted_principal") or "")
        client = str(trusted_context.get("trusted_client_adapter_id") or "")
        if not isinstance(raw, dict) or not principal or not client:
            raise StoreCommandError("UNAUTHORIZED", "trusted Operator scope/principal/client context is required")
        repositories = raw.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise StoreCommandError("UNAUTHORIZED", "trusted Operator scope has no repositories")
        normalized = frozenset(normalize_repository(str(value)) for value in repositories)
        feature_values = raw.get("feature_ids")
        if feature_values is None:
            feature_ids = None
        elif isinstance(feature_values, list) and feature_values and all(isinstance(value, str) and value for value in feature_values):
            feature_ids = frozenset(feature_values)
        else:
            raise StoreCommandError("UNAUTHORIZED", "trusted Operator Feature scope is invalid")
        return cls(normalized, feature_ids, principal, client)

    def allows(self, repository: str, feature_id: str) -> bool:
        return normalize_repository(repository) in self.repositories and (
            self.feature_ids is None or feature_id in self.feature_ids
        )


def _scope_for_request(request: dict[str, Any], trusted_context: dict[str, Any]) -> TrustedOperatorScope:
    scope = TrustedOperatorScope.from_context(trusted_context)
    declared = (request.get("client_identity") or {}).get("adapter_id")
    if declared != scope.client_adapter_id:
        raise StoreCommandError("UNAUTHORIZED", "client identity does not match trusted adapter context")
    declared_human = (request.get("client_identity") or {}).get("human_principal")
    if declared_human is not None and declared_human != scope.principal:
        raise StoreCommandError("UNAUTHORIZED", "human principal does not match trusted invocation context")
    return scope


def _decision_public(view: dict[str, Any]) -> dict[str, Any]:
    row = {
        "decision_id": view["decision_id"],
        "decision_type": view["decision_type"],
        "feature_id": view["feature_id"],
        "operation_id": view["operation_id"],
        "operation_generation": view["operation_generation"],
        "expected_revision": view["expected_revision"],
        "target_ref": view["target_ref"],
        "candidate_head_sha": view.get("candidate_head_sha"),
        "allowed_choices": list(view["allowed_choices"]),
        "requested_by": view["requested_by"],
        "requested_at": view["requested_at"],
        "expires_at": view["expires_at"],
        "status": view["status"],
    }
    if view.get("response"):
        row.update({key: value for key, value in view["response"].items() if key != "authorized_action"})
    return row


def _notification_public(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "notification_id": view["notification_id"],
        "notification_type": view["notification_type"],
        "feature_id": view["feature_id"],
        "operation_id": view["operation_id"],
        "operation_generation": view["operation_generation"],
        "decision_id": view.get("decision_id"),
        "summary": view.get("summary", ""),
        "created_at": view["created_at"],
        "status": view["status"],
    }


class DecisionNotificationCoordinator:
    """Trusted internal write surface. It never accepts policy/scope authority from canonical payloads."""

    def __init__(
        self,
        *,
        runtime: OperatorStoreRuntime,
        policy_verifier: ProtectedDecisionPolicyVerifier,
        feature_gateway: FeatureTruthGateway,
        trusted_context_digest: str,
    ):
        if not isinstance(runtime, OperatorStoreRuntime):
            raise ValueError("Decision coordinator requires trusted Operator Store runtime")
        if not isinstance(policy_verifier, ProtectedDecisionPolicyVerifier):
            raise ValueError("Decision coordinator requires current protected policy verifier")
        if not trusted_context_digest:
            raise ValueError("Decision coordinator requires trusted context digest")
        self.runtime = runtime
        self.policy_verifier = policy_verifier
        self.feature_gateway = feature_gateway
        self.trusted_context_digest = trusted_context_digest

    def request_decision(
        self,
        *,
        operation_id: str,
        decision_type: str,
        request_key: str,
        requested_by: str,
        summary: str = "",
    ) -> dict[str, Any]:
        def planner(snapshot):
            projection = __import__("operator_store_model").rebuild_projection(snapshot, operation_id)
            feature, _ = self.feature_gateway.read_feature(operation_id=operation_id)
            policy = self.policy_verifier.verify_current(
                target_repository=feature.repository,
                feature_id=feature.feature_id,
                target_ref=feature.target_ref,
                decision_type=decision_type,
            )
            return plan_decision_request(
                snapshot,
                feature=feature,
                operation_id=operation_id,
                generation=int(projection["generation"]),
                decision_type=decision_type,
                request_key=request_key,
                policy=policy,
                requested_by=requested_by,
                occurred_at=self.runtime.clock(),
                trusted_context_digest=self.trusted_context_digest,
                summary=summary,
            )
        return dict(self.runtime.commit_replanned(planner).result)

    def reconcile_decision_time(self, *, decision_id: str) -> dict[str, Any]:
        def planner(snapshot):
            view = rebuild_decision(snapshot, decision_id)
            if view["status"] != "PENDING":
                return plan_decision_expiry(
                    snapshot,
                    decision_id=decision_id,
                    occurred_at=self.runtime.clock(),
                    trusted_context_digest=self.trusted_context_digest,
                )
            feature, _ = self.feature_gateway.read_feature(operation_id=str(view["operation_id"]))
            policy = self.policy_verifier.verify_current(
                target_repository=feature.repository,
                feature_id=feature.feature_id,
                target_ref=feature.target_ref,
                decision_type=str(view["decision_type"]),
            )
            now = self.runtime.clock()
            from operator_decisions_notifications import _parse_time
            if _parse_time(now) >= _parse_time(str(view["expires_at"])):
                return plan_decision_expiry(
                    snapshot,
                    decision_id=decision_id,
                    occurred_at=now,
                    trusted_context_digest=self.trusted_context_digest,
                )
            return plan_authorization_expiring_notification(
                snapshot,
                decision_id=decision_id,
                occurred_at=now,
                warning_seconds=policy.warning_seconds,
                trusted_context_digest=self.trusted_context_digest,
            )
        return dict(self.runtime.commit_replanned(planner).result)

    def notify_operation(self, *, operation_id: str, notification_type: str, trigger_identity: str, summary: str = "") -> dict[str, Any]:
        return dict(
            self.runtime.commit_replanned(
                lambda snapshot: plan_notification_for_operation(
                    snapshot,
                    operation_id=operation_id,
                    notification_type=notification_type,
                    trigger_identity=trigger_identity,
                    occurred_at=self.runtime.clock(),
                    trusted_context_digest=self.trusted_context_digest,
                    summary=summary,
                )
            ).result
        )


class _ProtectedBackend:
    def __init__(self, runtime: OperatorStoreRuntime):
        self.runtime = runtime

    def availability(self, capability, trusted_context):
        try:
            receipt = self.runtime.protected_receipt()
            receipt.validate_for(self.runtime.backend.repository, self.runtime.backend.state_ref)
            TrustedOperatorScope.from_context(trusted_context)
        except Exception:
            return False, "POLICY_RESTRICTED"
        return True, "AVAILABLE"


class DecisionListBackend(_ProtectedBackend):
    def invoke(self, request, trusted_context):
        scope = _scope_for_request(request, trusted_context)
        rows = list_decisions(
            self.runtime.backend.read_snapshot(),
            repositories=set(scope.repositories),
            feature_ids=set(scope.feature_ids) if scope.feature_ids is not None else None,
        )
        return {"decisions": [_decision_public(row) for row in rows]}


class NotificationListBackend(_ProtectedBackend):
    def invoke(self, request, trusted_context):
        scope = _scope_for_request(request, trusted_context)
        rows = list_notifications(
            self.runtime.backend.read_snapshot(),
            repositories=set(scope.repositories),
            feature_ids=set(scope.feature_ids) if scope.feature_ids is not None else None,
        )
        return {"notifications": [_notification_public(row) for row in rows]}


class OperatorInboxBackend(_ProtectedBackend):
    def invoke(self, request, trusted_context):
        scope = _scope_for_request(request, trusted_context)
        result = build_operator_inbox(
            self.runtime.backend.read_snapshot(),
            repositories=set(scope.repositories),
            feature_ids=set(scope.feature_ids) if scope.feature_ids is not None else None,
        )
        return {
            "operations": result["operations"],
            "decisions": [_decision_public(row) for row in result["decisions"]],
            "notifications": [_notification_public(row) for row in result["notifications"]],
        }


class DecisionRespondBackend(_ProtectedBackend):
    def __init__(self, runtime, *, policy_verifier: ProtectedDecisionPolicyVerifier, feature_gateway: FeatureTruthGateway, trusted_context_digest: str):
        super().__init__(runtime)
        self.policy_verifier = policy_verifier
        self.feature_gateway = feature_gateway
        self.trusted_context_digest = trusted_context_digest

    def invoke(self, request, trusted_context):
        scope = _scope_for_request(request, trusted_context)
        decision_id = str((request.get("payload") or {})["decision_id"])
        selected = str((request.get("payload") or {})["response"])

        def planner(snapshot):
            view = rebuild_decision(snapshot, decision_id)
            if not scope.allows(str(view["target_repository"]), str(view["feature_id"])):
                raise StoreCommandError("UNAUTHORIZED", "Decision is outside trusted invocation scope")
            feature, _ = self.feature_gateway.read_feature(operation_id=str(view["operation_id"]))
            policy = self.policy_verifier.verify_current(
                target_repository=feature.repository,
                feature_id=feature.feature_id,
                target_ref=feature.target_ref,
                decision_type=str(view["decision_type"]),
            )
            return plan_decision_response(
                snapshot,
                decision_id=decision_id,
                selected_choice=selected,
                responder_identity=scope.principal,
                responder_client=scope.client_adapter_id,
                occurred_at=self.runtime.clock(),
                trusted_feature=feature,
                current_policy=policy,
                trusted_context_digest=self.trusted_context_digest,
            )
        result = self.runtime.commit_replanned(planner)
        return {"decision_id": result.result["decision_id"], "status": result.result["status"]}


class NotificationAckBackend(_ProtectedBackend):
    def __init__(self, runtime, *, trusted_context_digest: str):
        super().__init__(runtime)
        self.trusted_context_digest = trusted_context_digest

    def invoke(self, request, trusted_context):
        scope = _scope_for_request(request, trusted_context)
        notification_id = str((request.get("payload") or {})["notification_id"])

        def planner(snapshot):
            view = rebuild_notification(snapshot, notification_id)
            if not scope.allows(str(view["target_repository"]), str(view["feature_id"])):
                raise StoreCommandError("UNAUTHORIZED", "Notification is outside trusted invocation scope")
            return plan_notification_ack(
                snapshot,
                notification_id=notification_id,
                acknowledged_by=scope.principal,
                acknowledged_via_client=scope.client_adapter_id,
                occurred_at=self.runtime.clock(),
                trusted_context_digest=self.trusted_context_digest,
            )
        result = self.runtime.commit_replanned(planner)
        return {"notification_id": result.result["notification_id"], "status": result.result["status"]}


def decision_notification_backends(
    runtime: OperatorStoreRuntime,
    *,
    policy_verifier: ProtectedDecisionPolicyVerifier,
    feature_gateway: FeatureTruthGateway,
    trusted_context_digest: str,
) -> tuple[dict[str, Any], DecisionNotificationCoordinator]:
    coordinator = DecisionNotificationCoordinator(
        runtime=runtime,
        policy_verifier=policy_verifier,
        feature_gateway=feature_gateway,
        trusted_context_digest=trusted_context_digest,
    )
    return (
        {
            "operator.inbox": OperatorInboxBackend(runtime),
            "decision.list": DecisionListBackend(runtime),
            "decision.respond": DecisionRespondBackend(
                runtime,
                policy_verifier=policy_verifier,
                feature_gateway=feature_gateway,
                trusted_context_digest=trusted_context_digest,
            ),
            "notification.list": NotificationListBackend(runtime),
            "notification.ack": NotificationAckBackend(runtime, trusted_context_digest=trusted_context_digest),
        },
        coordinator,
    )
