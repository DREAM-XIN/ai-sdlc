#!/usr/bin/env python3
"""Recovery helpers specific to the v0.3 vertical Operator profile."""
from __future__ import annotations

from typing import Any

from operator_store import StoreCommandError, _append_event, _finalize, plan_takeover
from operator_store_model import (
    StoreSnapshot,
    digest_json,
    normalize_repository,
    operation_events,
    rebuild_projection,
    reservation_path,
)
from operator_vertical import RoleIndependencePolicy, TrustedDispatchContext, VERTICAL_PROFILE
from operator_vertical_store import vertical_projection


def _context_payload(context: TrustedDispatchContext) -> dict[str, Any]:
    return {
        "operation_id": context.operation_id,
        "operation_generation": context.operation_generation,
        "operation_profile": context.operation_profile,
        "semantic_effect_key": context.semantic_effect_key,
        "external_dispatch_key": context.external_dispatch_key,
        "dispatch_id": context.dispatch_id,
        "runtime_receipt_identity": context.runtime_receipt_identity,
        "target_repository": context.target_repository,
        "target_ref": context.target_ref,
        "feature_id": context.feature_id,
        "expected_revision": context.expected_revision,
        "feature_stage": context.feature_stage,
        "task_id": context.task_id,
        "role": context.role,
        "candidate_pr_number": context.candidate_pr_number,
        "candidate_head_sha": context.candidate_head_sha,
        "worker_identity": context.worker_identity,
        "collector_identity": context.collector_identity,
    }


def _task_binding_matches(task_identity: str, task_id: str) -> bool:
    if task_identity.startswith(("vertical:code-remediation:", "vertical:code-rereview:")):
        return f":{task_id}:" in task_identity
    return task_identity == task_id


def _validate_durable_dispatch_binding(snapshot: StoreSnapshot, context: TrustedDispatchContext) -> None:
    projection = vertical_projection(snapshot, context.operation_id)
    if projection.get("operation_profile") != VERTICAL_PROFILE:
        raise StoreCommandError("CAPABILITY_UNAVAILABLE", "Operation is not a vertical profile")
    if projection["generation"] != context.operation_generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "callback belongs to a superseded generation")
    if normalize_repository(str(projection["target_repository"])) != normalize_repository(context.target_repository):
        raise StoreCommandError("STALE_REVISION", "callback repository binding mismatch")
    if projection["feature_id"] != context.feature_id:
        raise StoreCommandError("STALE_REVISION", "callback Feature binding mismatch")
    if projection["expected_feature_revision"] != context.expected_revision:
        raise StoreCommandError("STALE_REVISION", "callback Feature revision binding mismatch")

    reservation = snapshot.get(reservation_path(context.semantic_effect_key))
    if not isinstance(reservation, dict):
        raise StoreCommandError("INVALID_REQUEST", "callback semantic reservation does not exist")
    expected_reservation = {
        "external_dispatch_key": context.external_dispatch_key,
        "target_repository": normalize_repository(context.target_repository),
        "feature_id": context.feature_id,
        "expected_revision": context.expected_revision,
        "current_stage": context.feature_stage,
        "role": context.role,
        "candidate_head_sha": context.candidate_head_sha,
    }
    for field, expected in expected_reservation.items():
        actual = reservation.get(field)
        if field == "target_repository":
            actual = normalize_repository(str(actual))
        if actual != expected:
            raise StoreCommandError("STALE_REVISION", f"callback reservation binding mismatch: {field}")
    task_identity = str(reservation.get("task_identity") or "")
    if not task_identity or not _task_binding_matches(task_identity, context.task_id):
        raise StoreCommandError("STALE_REVISION", "callback task binding mismatch")

    authorizations = []
    for event in operation_events(snapshot, context.operation_id):
        if event["event_type"] != "dispatch.launch.authorized":
            continue
        if int(event["operation_generation"]) != context.operation_generation:
            continue
        payload = event.get("payload") or {}
        if payload.get("external_dispatch_key") == context.external_dispatch_key:
            authorizations.append(payload)
    if len(authorizations) != 1:
        raise StoreCommandError("INVALID_REQUEST", "callback requires exactly one durable launch authorization")
    launch = authorizations[0]
    expected_launch = {
        "dispatch_id": context.dispatch_id,
        "semantic_effect_key": context.semantic_effect_key,
        "external_dispatch_key": context.external_dispatch_key,
        "feature_id": context.feature_id,
        "expected_revision": context.expected_revision,
        "stage": context.feature_stage,
        "role": context.role,
        "candidate_head_sha": context.candidate_head_sha,
    }
    for field, expected in expected_launch.items():
        if launch.get(field) != expected:
            raise StoreCommandError("STALE_REVISION", f"callback launch binding mismatch: {field}")


def plan_vertical_callback_record(
    snapshot: StoreSnapshot,
    *,
    context: TrustedDispatchContext,
    callback_id: str,
    worker_payload: dict[str, Any],
    receipts: list[dict[str, Any]],
    occurred_at: str,
    trusted_context_digest: str,
):
    """Durably record one collector-normalized callback after exact Store binding checks."""
    _validate_durable_dispatch_binding(snapshot, context)
    envelope = {
        "trusted_context": _context_payload(context),
        "worker_payload": worker_payload,
        "collected_outputs": receipts,
    }
    payload = {
        "callback_id": callback_id,
        "callback_digest": digest_json({"worker_payload": worker_payload, "receipts": receipts}),
        "external_dispatch_key": context.external_dispatch_key,
        "trusted_callback_envelope": envelope,
        "trusted_callback_envelope_digest": digest_json(envelope),
    }
    working, event = _append_event(
        snapshot,
        operation_id=context.operation_id,
        generation=context.operation_generation,
        event_type="worker.callback.recorded",
        occurred_at=occurred_at,
        payload=payload,
        trusted_context_digest=trusted_context_digest,
        identity_material={"callback_id": callback_id},
    )
    return _finalize(snapshot, working, [event], context.operation_id)


def recover_vertical_callback(snapshot: StoreSnapshot, *, operation_id: str, callback_id: str) -> dict[str, Any]:
    matches = []
    for event in operation_events(snapshot, operation_id):
        if event["event_type"] != "worker.callback.recorded":
            continue
        payload = event.get("payload") or {}
        if payload.get("callback_id") == callback_id:
            matches.append(payload)
    if len(matches) != 1:
        raise StoreCommandError("INVALID_REQUEST", "callback recovery requires exactly one durable callback record")
    payload = matches[0]
    envelope = payload.get("trusted_callback_envelope")
    if not isinstance(envelope, dict):
        raise StoreCommandError("BLOCKED", "callback record predates recoverable vertical envelope")
    if digest_json(envelope) != payload.get("trusted_callback_envelope_digest"):
        raise StoreCommandError("INTERNAL_FAILURE", "durable callback envelope digest mismatch")
    context = envelope.get("trusted_context") or {}
    if context.get("operation_id") != operation_id or context.get("operation_profile") != VERTICAL_PROFILE:
        raise StoreCommandError("INTERNAL_FAILURE", "durable callback envelope binding mismatch")
    return envelope


def derive_role_independence_policy(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    exclude_callback_id: str | None = None,
) -> RoleIndependencePolicy:
    """Rebuild trusted role-separation identities only from accepted durable callbacks."""
    callback_by_id: dict[str, dict[str, Any]] = {}
    validated: list[str] = []
    for event in operation_events(snapshot, operation_id):
        payload = event.get("payload") or {}
        if event["event_type"] == "worker.callback.recorded":
            callback_id = str(payload.get("callback_id") or "")
            envelope = payload.get("trusted_callback_envelope")
            if callback_id and isinstance(envelope, dict):
                callback_by_id[callback_id] = envelope
        elif event["event_type"] == "worker.result.validated":
            callback_id = str(payload.get("callback_id") or "")
            if callback_id:
                validated.append(callback_id)

    developer_identity = None
    reviewer_identity = None
    remediation_developer_identity = None
    for callback_id in validated:
        if callback_id == exclude_callback_id:
            continue
        envelope = callback_by_id.get(callback_id)
        if not envelope:
            continue
        context = envelope.get("trusted_context") or {}
        worker_identity = str(context.get("worker_identity") or "")
        role = context.get("role")
        semantic_key = context.get("semantic_effect_key")
        reservation = snapshot.get(reservation_path(str(semantic_key))) if semantic_key else None
        task_identity = str((reservation or {}).get("task_identity") or "")
        if not worker_identity:
            continue
        if role == "developer":
            if task_identity.startswith("vertical:code-remediation:"):
                remediation_developer_identity = worker_identity
            elif task_identity.startswith("vertical:implementation:"):
                developer_identity = worker_identity
        elif role == "reviewer":
            reviewer_identity = worker_identity

    return RoleIndependencePolicy(
        developer_identity=developer_identity,
        reviewer_identity=reviewer_identity,
        remediation_developer_identity=remediation_developer_identity,
    )


def plan_vertical_takeover(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    occurred_at: str,
    trusted_context_digest: str,
):
    """Take over only resumable vertical operations; NEEDS_USER remains a stable stop."""
    projection = rebuild_projection(snapshot, operation_id)
    if projection.get("operation_profile") != VERTICAL_PROFILE:
        raise StoreCommandError("CAPABILITY_UNAVAILABLE", "Operation is not a vertical profile")
    if projection["status"] == "NEEDS_USER":
        raise StoreCommandError("NEEDS_USER", "vertical takeover cannot bypass required user input")
    return plan_takeover(
        snapshot,
        operation_id=operation_id,
        occurred_at=occurred_at,
        trusted_context_digest=trusted_context_digest,
    )
