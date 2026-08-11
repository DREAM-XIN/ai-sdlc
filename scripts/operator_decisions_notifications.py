#!/usr/bin/env python3
"""Durable Decision, Notification Outbox and operator.inbox semantics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import json

from jsonschema import Draft202012Validator, FormatChecker

from operator_decision_policy import VerifiedDecisionPolicy
from operator_store import StoreCommandError, _append_event, _finalize
from operator_store_model import (
    STORE_ROOT,
    StoreInvariantError,
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    decision_path,
    digest_json,
    notification_path,
    operation_events,
    projection_public,
    rebuild_projection,
    normalize_repository,
    unfinished_operations,
)
from operator_vertical import FeatureSnapshot

ROOT = Path(__file__).resolve().parents[1]
DECISION_SCHEMA = ROOT / "spec" / "operator" / "store" / "decision.schema.json"
NOTIFICATION_SCHEMA = ROOT / "spec" / "operator" / "store" / "notification.schema.json"
DECISION_SCHEMA_VERSION = "ai-sdlc.decision/v1"
NOTIFICATION_SCHEMA_VERSION = "ai-sdlc.notification/v1"
NOTIFICATION_TYPES = frozenset({"decision.requested", "operation.blocked", "operation.completed", "authorization.expiring"})


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise StoreCommandError("INVALID_REQUEST", "trusted Decision time is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StoreCommandError("INVALID_REQUEST", "invalid trusted Decision time") from exc
    if parsed.tzinfo is None:
        raise StoreCommandError("INVALID_REQUEST", "trusted Decision time must include timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate(record: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise StoreInvariantError(f"{label}:{location}: {error.message}")


def _immutable(snapshot: StoreSnapshot, path: str, label: str) -> dict[str, Any]:
    value = snapshot.get(path)
    if not isinstance(value, dict):
        raise StoreCommandError("INVALID_REQUEST", f"{label} not found")
    return dict(value)


def decision_id_for(material: dict[str, Any]) -> str:
    return "dec-" + digest_json(material)[:48]


def notification_id_for(material: dict[str, Any]) -> str:
    return "ntf-" + digest_json(material)[:48]


def _record_rows(snapshot: StoreSnapshot, kind: str) -> list[dict[str, Any]]:
    if kind == "decision":
        prefix, schema, path_fn, id_key, label = f"{STORE_ROOT}/decisions/", DECISION_SCHEMA, decision_path, "decision_id", "Decision"
    else:
        prefix, schema, path_fn, id_key, label = f"{STORE_ROOT}/notifications/", NOTIFICATION_SCHEMA, notification_path, "notification_id", "Notification"
    rows = []
    for path, value in snapshot.files.items():
        if not path.startswith(prefix) or not path.endswith(".json"):
            continue
        if not isinstance(value, dict):
            raise StoreInvariantError(f"{label} record must be an object")
        record = dict(value)
        _validate(record, schema, label)
        if path_fn(str(record[id_key])) != path:
            raise StoreInvariantError(f"{label} path/id binding mismatch")
        rows.append(record)
    return rows


def rebuild_decision(snapshot: StoreSnapshot, decision_id: str) -> dict[str, Any]:
    record = _immutable(snapshot, decision_path(decision_id), "Decision")
    _validate(record, DECISION_SCHEMA, "Decision")
    if record.get("decision_id") != decision_id:
        raise StoreInvariantError("Decision id binding mismatch")
    state = "PENDING"
    response = None
    consumed = None
    requested_seen = False
    for event in operation_events(snapshot, str(record["operation_id"])):
        payload = event.get("payload") or {}
        if payload.get("decision_id") != decision_id:
            continue
        event_type = event["event_type"]
        if event_type == "decision.requested":
            if requested_seen:
                raise StoreInvariantError("duplicate Decision request fact")
            requested_seen = True
        elif event_type == "decision.responded":
            if state != "PENDING":
                raise StoreInvariantError("Decision response after terminal state")
            state = "RESOLVED"
            response = {
                "responded_by_user": payload.get("responded_by_user"),
                "responded_via_client": payload.get("responded_via_client"),
                "responded_at": payload.get("responded_at"),
                "selected_choice": payload.get("selected_choice"),
                "authorized_action": payload.get("authorized_action"),
            }
        elif event_type == "decision.expired":
            if state != "PENDING":
                raise StoreInvariantError("Decision expiry after terminal state")
            state = "EXPIRED"
        elif event_type == "decision.superseded":
            if state != "PENDING":
                raise StoreInvariantError("Decision supersession after terminal state")
            state = "SUPERSEDED"
        elif event_type == "decision.authorization-consumed":
            if state != "RESOLVED" or consumed is not None:
                raise StoreInvariantError("invalid Decision authorization consumption")
            consumed = {
                "consumed_at": payload.get("consumed_at"),
                "action": payload.get("action"),
                "consumer": payload.get("consumer"),
            }
    if not requested_seen:
        raise StoreInvariantError("Decision record lacks durable request fact")
    projection = rebuild_projection(snapshot, str(record["operation_id"]))
    if state == "PENDING" and (
        projection["generation"] != int(record["operation_generation"]) or projection["status"] == "CANCELLED"
    ):
        state = "SUPERSEDED"
    return {**record, "status": state, "response": response, "authorization_consumed": consumed}


def rebuild_notification(snapshot: StoreSnapshot, notification_id: str) -> dict[str, Any]:
    record = _immutable(snapshot, notification_path(notification_id), "Notification")
    _validate(record, NOTIFICATION_SCHEMA, "Notification")
    if record.get("notification_id") != notification_id:
        raise StoreInvariantError("Notification id binding mismatch")
    created_seen = False
    ack = None
    for event in operation_events(snapshot, str(record["operation_id"])):
        payload = event.get("payload") or {}
        if payload.get("notification_id") != notification_id:
            continue
        if event["event_type"] == "notification.created":
            if created_seen:
                raise StoreInvariantError("duplicate Notification created fact")
            created_seen = True
        elif event["event_type"] == "notification.acknowledged":
            current = {
                "acknowledged_by": payload.get("acknowledged_by"),
                "acknowledged_via_client": payload.get("acknowledged_via_client"),
                "acknowledged_at": payload.get("acknowledged_at"),
            }
            if ack is not None and ack != current:
                raise StoreInvariantError("conflicting Notification acknowledgement history")
            ack = current
    if not created_seen:
        raise StoreInvariantError("Notification record lacks durable created fact")
    return {**record, "status": "ACKNOWLEDGED" if ack else "UNREAD", "acknowledgement": ack}


def _verify_feature(record: dict[str, Any], feature: FeatureSnapshot) -> None:
    if not isinstance(feature, FeatureSnapshot):
        raise StoreCommandError("STALE_REVISION", "fresh trusted Feature truth is required")
    if normalize_repository(feature.repository) != normalize_repository(str(record["target_repository"])):
        raise StoreCommandError("STALE_REVISION", "Decision Feature repository binding changed")
    if feature.feature_id != record["feature_id"] or feature.target_ref != record["target_ref"]:
        raise StoreCommandError("STALE_REVISION", "Decision Feature identity/ref binding changed")
    if feature.revision != int(record["expected_revision"]):
        raise StoreCommandError("STALE_REVISION", "Decision Feature revision changed")
    if feature.candidate_head_sha != record.get("candidate_head_sha"):
        raise StoreCommandError("STALE_REVISION", "Decision candidate head changed")


def _verify_policy(record: dict[str, Any], policy: VerifiedDecisionPolicy) -> None:
    if (
        record.get("decision_type") != policy.decision_type
        or record.get("trusted_policy_ref") != policy.policy_ref
        or record.get("trusted_policy_epoch") != policy.policy_epoch
        or record.get("trusted_policy_digest") != policy.policy_digest
        or tuple(sorted(record.get("allowed_choices", []))) != tuple(sorted(policy.allowed_choices))
        or dict(record.get("choice_actions") or {}) != dict(policy.choice_actions)
    ):
        raise StoreCommandError("POLICY_DENIED", "current protected Decision policy no longer matches Decision authority")


def _same_notification(existing: dict[str, Any], proposed: dict[str, Any]) -> bool:
    replay = dict(proposed)
    replay["created_at"] = existing.get("created_at")
    return existing == replay


def _notification_record(
    projection: dict[str, Any], *, notification_type: str, semantic_key: str, created_at: str,
    trusted_context_digest: str, decision_id: str | None = None, summary: str = "",
) -> dict[str, Any]:
    if notification_type not in NOTIFICATION_TYPES:
        raise StoreCommandError("INVALID_REQUEST", "unsupported Notification type")
    notification_id = notification_id_for({
        "notification_type": notification_type,
        "operation_id": projection["operation_id"],
        "operation_generation": projection["generation"],
        "semantic_key": semantic_key,
    })
    record = {
        "schema_version": NOTIFICATION_SCHEMA_VERSION,
        "notification_id": notification_id,
        "notification_type": notification_type,
        "target_repository": normalize_repository(str(projection["target_repository"])),
        "feature_id": str(projection["feature_id"]),
        "operation_id": str(projection["operation_id"]),
        "operation_generation": int(projection["generation"]),
        "decision_id": decision_id,
        "semantic_key": semantic_key,
        "summary": summary[:512],
        "created_at": created_at,
        "trusted_context_digest": trusted_context_digest,
    }
    _validate(record, NOTIFICATION_SCHEMA, "Notification")
    return record


def _add_notification(
    original: StoreSnapshot, working: StoreSnapshot, mutations: list[StoreMutation], *, projection: dict[str, Any],
    notification_type: str, semantic_key: str, created_at: str, trusted_context_digest: str,
    decision_id: str | None = None, summary: str = "",
) -> tuple[StoreSnapshot, dict[str, Any]]:
    record = _notification_record(
        projection, notification_type=notification_type, semantic_key=semantic_key, created_at=created_at,
        trusted_context_digest=trusted_context_digest, decision_id=decision_id, summary=summary,
    )
    path = notification_path(record["notification_id"])
    existing = working.get(path)
    if existing is not None:
        if not isinstance(existing, dict) or not _same_notification(existing, record):
            raise StoreCommandError("ALREADY_APPLIED", "Notification semantic identity conflicts with existing record")
        return working, rebuild_notification(working, record["notification_id"])
    mutation = StoreMutation("create_immutable", path, record)
    mutations.append(mutation)
    working = apply_plan_to_snapshot(working, StoreMutationPlan(original.ref_sha, (mutation,), {}))
    working, event = _append_event(
        working, operation_id=projection["operation_id"], generation=projection["generation"],
        event_type="notification.created", occurred_at=created_at,
        payload={"notification_id": record["notification_id"], "notification_type": notification_type, "semantic_key": semantic_key},
        trusted_context_digest=trusted_context_digest, identity_material={"notification_id": record["notification_id"]},
    )
    mutations.append(event)
    return working, {**record, "status": "UNREAD", "acknowledgement": None}


def plan_decision_request(
    snapshot: StoreSnapshot, *, feature: FeatureSnapshot, operation_id: str, generation: int, decision_type: str,
    request_key: str, policy: VerifiedDecisionPolicy, requested_by: str, occurred_at: str,
    trusted_context_digest: str, summary: str = "",
) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if projection["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "Decision request generation is stale")
    if projection["status"] in {"DONE", "CANCELLED"}:
        raise StoreCommandError("CANCELLED_OPERATION", "terminal Operation cannot request a Decision")
    if (
        normalize_repository(str(projection["target_repository"])) != normalize_repository(feature.repository)
        or projection["feature_id"] != feature.feature_id
        or projection["expected_feature_revision"] != feature.revision
    ):
        raise StoreCommandError("STALE_REVISION", "Decision request Feature/Operation binding mismatch")
    if policy.decision_type != decision_type:
        raise StoreCommandError("POLICY_DENIED", "Decision policy type does not match requested Decision")
    seed = {
        "target_repository": normalize_repository(feature.repository), "feature_id": feature.feature_id,
        "operation_id": operation_id, "operation_generation": generation, "expected_revision": feature.revision,
        "target_ref": feature.target_ref, "candidate_head_sha": feature.candidate_head_sha,
        "decision_type": decision_type, "request_key": request_key, "policy_digest": policy.policy_digest,
    }
    decision_id = decision_id_for(seed)
    requested_at = _parse_time(occurred_at)
    record = {
        "schema_version": DECISION_SCHEMA_VERSION, "decision_id": decision_id, "decision_type": decision_type,
        "target_repository": normalize_repository(feature.repository), "feature_id": feature.feature_id,
        "operation_id": operation_id, "operation_generation": generation, "expected_revision": feature.revision,
        "target_ref": feature.target_ref, "candidate_head_sha": feature.candidate_head_sha,
        "allowed_choices": list(policy.allowed_choices), "choice_actions": dict(policy.choice_actions),
        "trusted_policy_ref": policy.policy_ref, "trusted_policy_epoch": policy.policy_epoch,
        "trusted_policy_digest": policy.policy_digest, "requested_by": requested_by,
        "requested_at": _format_time(requested_at),
        "expires_at": _format_time(requested_at + timedelta(seconds=policy.ttl_seconds)),
        "trusted_context_digest": trusted_context_digest,
    }
    _validate(record, DECISION_SCHEMA, "Decision")
    existing = snapshot.get(decision_path(decision_id))
    if existing is not None:
        if existing != record:
            raise StoreCommandError("ALREADY_APPLIED", "Decision semantic identity conflicts with existing record")
        return StoreMutationPlan(snapshot.ref_sha, tuple(), rebuild_decision(snapshot, decision_id))
    mutations = [StoreMutation("create_immutable", decision_path(decision_id), record)]
    working = apply_plan_to_snapshot(snapshot, StoreMutationPlan(snapshot.ref_sha, tuple(mutations), {}))
    working, event = _append_event(
        working, operation_id=operation_id, generation=generation, event_type="decision.requested", occurred_at=occurred_at,
        payload={"decision_id": decision_id, "decision_type": decision_type}, trusted_context_digest=trusted_context_digest,
        identity_material={"decision_id": decision_id},
    )
    mutations.append(event)
    working, notification = _add_notification(
        snapshot, working, mutations, projection=rebuild_projection(working, operation_id),
        notification_type="decision.requested", semantic_key=f"decision.requested:{decision_id}", created_at=occurred_at,
        trusted_context_digest=trusted_context_digest, decision_id=decision_id, summary=summary,
    )
    return _finalize(snapshot, working, mutations, operation_id, {**record, "status": "PENDING", "notification_id": notification["notification_id"]})


def plan_decision_response(
    snapshot: StoreSnapshot, *, decision_id: str, selected_choice: str, responder_identity: str,
    responder_client: str, occurred_at: str, trusted_feature: FeatureSnapshot,
    current_policy: VerifiedDecisionPolicy, trusted_context_digest: str,
) -> StoreMutationPlan:
    view = rebuild_decision(snapshot, decision_id)
    if view["status"] == "RESOLVED":
        response = view.get("response") or {}
        if response.get("selected_choice") == selected_choice and response.get("responded_by_user") == responder_identity:
            return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": "RESOLVED"})
        raise StoreCommandError("ALREADY_APPLIED", "Decision is already resolved with different semantics")
    projection = rebuild_projection(snapshot, str(view["operation_id"]))
    if projection["status"] == "CANCELLED":
        raise StoreCommandError("CANCELLED_OPERATION", "cancelled Operation rejects late Decision response")
    if view["status"] in {"EXPIRED", "SUPERSEDED"}:
        raise StoreCommandError("POLICY_DENIED", "Decision is no longer current authority")
    _verify_feature(view, trusted_feature)
    _verify_policy(view, current_policy)
    if projection["generation"] != int(view["operation_generation"]):
        raise StoreCommandError("SUPERSEDED_GENERATION", "Decision Operation generation is stale")
    if responder_identity not in current_policy.allowed_responders:
        raise StoreCommandError("UNAUTHORIZED", "trusted responder is not authorized for this Decision")
    if _parse_time(occurred_at) >= _parse_time(str(view["expires_at"])):
        raise StoreCommandError("POLICY_DENIED", "Decision has expired")
    action = current_policy.action_for(selected_choice)
    working, event = _append_event(
        snapshot, operation_id=str(view["operation_id"]), generation=int(view["operation_generation"]),
        event_type="decision.responded", occurred_at=occurred_at,
        payload={"decision_id": decision_id, "responded_by_user": responder_identity,
                 "responded_via_client": responder_client, "responded_at": occurred_at,
                 "selected_choice": selected_choice, "authorized_action": action},
        trusted_context_digest=trusted_context_digest,
        identity_material={"decision_id": decision_id, "selected_choice": selected_choice},
    )
    return _finalize(snapshot, working, [event], str(view["operation_id"]), {"decision_id": decision_id, "status": "RESOLVED"})


def plan_decision_expiry(snapshot: StoreSnapshot, *, decision_id: str, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    view = rebuild_decision(snapshot, decision_id)
    if view["status"] != "PENDING":
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": view["status"]})
    if _parse_time(occurred_at) < _parse_time(str(view["expires_at"])):
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": "PENDING"})
    working, event = _append_event(
        snapshot, operation_id=str(view["operation_id"]), generation=int(view["operation_generation"]),
        event_type="decision.expired", occurred_at=occurred_at,
        payload={"decision_id": decision_id, "expired_at": occurred_at}, trusted_context_digest=trusted_context_digest,
        identity_material={"decision_id": decision_id},
    )
    return _finalize(snapshot, working, [event], str(view["operation_id"]), {"decision_id": decision_id, "status": "EXPIRED"})


def plan_decision_supersede(snapshot: StoreSnapshot, *, decision_id: str, reason: str, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    view = rebuild_decision(snapshot, decision_id)
    if view["status"] == "SUPERSEDED":
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": "SUPERSEDED"})
    if view["status"] != "PENDING":
        raise StoreCommandError("ALREADY_APPLIED", "only a pending Decision may be superseded")
    working, event = _append_event(
        snapshot, operation_id=str(view["operation_id"]), generation=int(view["operation_generation"]),
        event_type="decision.superseded", occurred_at=occurred_at,
        payload={"decision_id": decision_id, "reason": reason[:512]}, trusted_context_digest=trusted_context_digest,
        identity_material={"decision_id": decision_id},
    )
    return _finalize(snapshot, working, [event], str(view["operation_id"]), {"decision_id": decision_id, "status": "SUPERSEDED"})


def plan_consume_decision_authorization(
    snapshot: StoreSnapshot, *, decision_id: str, expected_action: str, consumer_identity: str, occurred_at: str,
    trusted_feature: FeatureSnapshot, current_policy: VerifiedDecisionPolicy, trusted_context_digest: str,
) -> StoreMutationPlan:
    view = rebuild_decision(snapshot, decision_id)
    if view["status"] != "RESOLVED" or not view.get("response"):
        raise StoreCommandError("POLICY_DENIED", "Decision has no resolved authorization to consume")
    if view.get("authorization_consumed") is not None:
        consumed = view["authorization_consumed"]
        if consumed.get("action") == expected_action:
            return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": "CONSUMED"})
        raise StoreCommandError("ALREADY_APPLIED", "Decision authorization was consumed for another action")
    _verify_feature(view, trusted_feature)
    _verify_policy(view, current_policy)
    if _parse_time(occurred_at) >= _parse_time(str(view["expires_at"])):
        raise StoreCommandError("POLICY_DENIED", "expired Decision cannot authorize later work")
    action = current_policy.action_for(str(view["response"]["selected_choice"]))
    if action != expected_action:
        raise StoreCommandError("POLICY_DENIED", "Decision does not authorize the requested bounded action")
    projection = rebuild_projection(snapshot, str(view["operation_id"]))
    if projection["status"] == "CANCELLED":
        raise StoreCommandError("CANCELLED_OPERATION", "cancelled Operation rejects Decision authorization consumption")
    working, event = _append_event(
        snapshot, operation_id=str(view["operation_id"]), generation=int(view["operation_generation"]),
        event_type="decision.authorization-consumed", occurred_at=occurred_at,
        payload={"decision_id": decision_id, "action": action, "consumer": consumer_identity, "consumed_at": occurred_at},
        trusted_context_digest=trusted_context_digest, identity_material={"decision_id": decision_id, "action": action},
    )
    return _finalize(snapshot, working, [event], str(view["operation_id"]), {"decision_id": decision_id, "status": "CONSUMED"})


def plan_notification_for_operation(
    snapshot: StoreSnapshot, *, operation_id: str, notification_type: str, trigger_identity: str,
    occurred_at: str, trusted_context_digest: str, summary: str = "",
) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    required_status = {"operation.blocked": "BLOCKED", "operation.completed": "DONE"}.get(notification_type)
    if required_status is None or projection["status"] != required_status:
        raise StoreCommandError("INVALID_REQUEST", "Operation state does not match requested Notification trigger")
    mutations: list[StoreMutation] = []
    working, notification = _add_notification(
        snapshot, snapshot, mutations, projection=projection, notification_type=notification_type,
        semantic_key=f"{notification_type}:{trigger_identity}", created_at=occurred_at,
        trusted_context_digest=trusted_context_digest, summary=summary,
    )
    return StoreMutationPlan(snapshot.ref_sha, tuple(), notification) if not mutations else _finalize(snapshot, working, mutations, operation_id, notification)


def plan_authorization_expiring_notification(
    snapshot: StoreSnapshot, *, decision_id: str, occurred_at: str, warning_seconds: int, trusted_context_digest: str,
) -> StoreMutationPlan:
    view = rebuild_decision(snapshot, decision_id)
    if view["status"] != "PENDING":
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": view["status"]})
    now, expires = _parse_time(occurred_at), _parse_time(str(view["expires_at"]))
    if now >= expires:
        raise StoreCommandError("POLICY_DENIED", "expired Decision must be materialized before warning")
    if now < expires - timedelta(seconds=warning_seconds):
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"decision_id": decision_id, "status": "NOT_DUE"})
    projection = rebuild_projection(snapshot, str(view["operation_id"]))
    mutations: list[StoreMutation] = []
    working, notification = _add_notification(
        snapshot, snapshot, mutations, projection=projection, notification_type="authorization.expiring",
        semantic_key=f"authorization.expiring:{decision_id}:{view['expires_at']}", created_at=occurred_at,
        trusted_context_digest=trusted_context_digest, decision_id=decision_id,
        summary="Decision authorization is approaching expiry",
    )
    return StoreMutationPlan(snapshot.ref_sha, tuple(), notification) if not mutations else _finalize(snapshot, working, mutations, str(view["operation_id"]), notification)


def plan_notification_ack(
    snapshot: StoreSnapshot, *, notification_id: str, acknowledged_by: str, acknowledged_via_client: str,
    occurred_at: str, trusted_context_digest: str,
) -> StoreMutationPlan:
    view = rebuild_notification(snapshot, notification_id)
    if view["status"] == "ACKNOWLEDGED":
        ack = view.get("acknowledgement") or {}
        if ack.get("acknowledged_by") == acknowledged_by:
            return StoreMutationPlan(snapshot.ref_sha, tuple(), {"notification_id": notification_id, "status": "ACKNOWLEDGED"})
        raise StoreCommandError("ALREADY_APPLIED", "Notification was acknowledged by another trusted identity")
    working, event = _append_event(
        snapshot, operation_id=str(view["operation_id"]), generation=int(view["operation_generation"]),
        event_type="notification.acknowledged", occurred_at=occurred_at,
        payload={"notification_id": notification_id, "acknowledged_by": acknowledged_by,
                 "acknowledged_via_client": acknowledged_via_client, "acknowledged_at": occurred_at},
        trusted_context_digest=trusted_context_digest,
        identity_material={"notification_id": notification_id, "acknowledged_by": acknowledged_by},
    )
    return _finalize(snapshot, working, [event], str(view["operation_id"]), {"notification_id": notification_id, "status": "ACKNOWLEDGED"})


def list_decisions(
    snapshot: StoreSnapshot, *, repositories: set[str], feature_ids: set[str] | None = None, pending_only: bool = False,
) -> list[dict[str, Any]]:
    normalized = {normalize_repository(value) for value in repositories}
    rows = []
    for record in _record_rows(snapshot, "decision"):
        if normalize_repository(str(record["target_repository"])) not in normalized:
            continue
        if feature_ids is not None and record["feature_id"] not in feature_ids:
            continue
        view = rebuild_decision(snapshot, str(record["decision_id"]))
        if pending_only and view["status"] != "PENDING":
            continue
        rows.append(view)
    rows.sort(key=lambda row: (row["requested_at"], row["decision_id"]))
    return rows


def list_notifications(
    snapshot: StoreSnapshot, *, repositories: set[str], feature_ids: set[str] | None = None, unread_only: bool = False,
) -> list[dict[str, Any]]:
    normalized = {normalize_repository(value) for value in repositories}
    rows = []
    for record in _record_rows(snapshot, "notification"):
        if normalize_repository(str(record["target_repository"])) not in normalized:
            continue
        if feature_ids is not None and record["feature_id"] not in feature_ids:
            continue
        view = rebuild_notification(snapshot, str(record["notification_id"]))
        if unread_only and view["status"] != "UNREAD":
            continue
        rows.append(view)
    rows.sort(key=lambda row: (row["created_at"], row["notification_id"]))
    return rows


def build_operator_inbox(snapshot: StoreSnapshot, *, repositories: set[str], feature_ids: set[str] | None = None) -> dict[str, Any]:
    normalized = {normalize_repository(value) for value in repositories}
    operations = []
    for repository in sorted(normalized):
        for row in unfinished_operations(snapshot, target_repository=repository):
            if feature_ids is not None and row["feature_id"] not in feature_ids:
                continue
            operations.append(projection_public(row))
    operations.sort(key=lambda row: row["operation_id"])
    return {
        "operations": operations,
        "decisions": list_decisions(snapshot, repositories=normalized, feature_ids=feature_ids, pending_only=True),
        "notifications": list_notifications(snapshot, repositories=normalized, feature_ids=feature_ids, unread_only=True),
    }
