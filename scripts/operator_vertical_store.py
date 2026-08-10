#!/usr/bin/env python3
"""Vertical-loop Store overlay for Feature fences that advance after trusted Persist."""
from __future__ import annotations

from typing import Any

from operator_store import StoreCommandError, _append_event, _finalize, _projection
from operator_store_model import (
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    canonical_json,
    external_dispatch_key,
    operation_events,
    rebuild_projection,
    reservation_path,
    semantic_effect_key,
    semantic_effect_material,
)


def vertical_projection(snapshot: StoreSnapshot, operation_id: str) -> dict[str, Any]:
    projection = dict(rebuild_projection(snapshot, operation_id))
    current_revision = projection["expected_feature_revision"]
    for event in operation_events(snapshot, operation_id):
        if event["event_type"] == "persist.confirmed":
            payload = event.get("payload") or {}
            result_revision = payload.get("result_revision")
            if result_revision is not None:
                if not isinstance(result_revision, int) or result_revision <= current_revision:
                    raise StoreCommandError("INTERNAL_FAILURE", "invalid confirmed Feature result revision")
                current_revision = result_revision
    projection["expected_feature_revision"] = current_revision
    return projection


def plan_vertical_semantic_reservation(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    generation: int,
    target_repository: str,
    feature_id: str,
    expected_revision: int,
    current_stage: str,
    task_identity: str,
    role: str,
    candidate_head_sha: str | None,
    occurred_at: str,
    trusted_context_digest: str,
) -> StoreMutationPlan:
    base = _projection(snapshot, operation_id, generation)
    current = vertical_projection(snapshot, operation_id)
    if base.get("operation_profile") != current.get("operation_profile"):
        raise StoreCommandError("INTERNAL_FAILURE", "vertical projection profile mismatch")
    if current["expected_feature_revision"] != expected_revision:
        raise StoreCommandError("STALE_REVISION", "expected Feature revision does not match vertical Operation fence")
    material = semantic_effect_material(
        target_repository=target_repository,
        feature_id=feature_id,
        expected_revision=expected_revision,
        current_stage=current_stage,
        task_identity=task_identity,
        role=role,
        candidate_head_sha=candidate_head_sha,
    )
    key = semantic_effect_key(**material)
    path = reservation_path(key)
    value = {
        "semantic_effect_key": key,
        "external_dispatch_key": external_dispatch_key(key),
        **material,
        "created_operation_id": operation_id,
        "created_generation": generation,
        "created_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    existing = snapshot.get(path)
    if existing is not None:
        left = dict(existing)
        right = dict(value)
        for row in (left, right):
            for field in ("created_at", "created_operation_id", "created_generation", "trusted_context_digest"):
                row.pop(field, None)
        if canonical_json(left) != canonical_json(right):
            raise StoreCommandError("ALREADY_CLAIMED", "semantic effect reservation conflicts with existing identity")
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"semantic_effect_key": key, "external_dispatch_key": existing["external_dispatch_key"]})
    return StoreMutationPlan(
        snapshot.ref_sha,
        (StoreMutation("create_immutable", path, value),),
        {"semantic_effect_key": key, "external_dispatch_key": value["external_dispatch_key"]},
    )


def _vertical_persist_event(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    generation: int,
    event_type: str,
    feature_event_id: str,
    expected_revision: int,
    target_ref: str,
    candidate_head_sha: str | None,
    occurred_at: str,
    trusted_context_digest: str,
    result_revision: int | None = None,
) -> StoreMutationPlan:
    _projection(
        snapshot,
        operation_id,
        generation,
        allow_blocked=event_type == "persist.confirmed",
        allow_needs_user=event_type == "persist.confirmed",
        allow_cancelled=event_type == "persist.confirmed",
    )
    current = vertical_projection(snapshot, operation_id)
    if current["expected_feature_revision"] != expected_revision:
        raise StoreCommandError("STALE_REVISION", "Persist expected revision does not match vertical Operation fence")
    if event_type == "persist.linearized" and feature_event_id not in current["requested_persists"]:
        raise StoreCommandError("INVALID_REQUEST", "Persist linearization lacks request")
    if event_type == "persist.confirmed" and feature_event_id not in current["linearized_persists"]:
        raise StoreCommandError("INVALID_REQUEST", "Persist confirmation lacks linearization")
    if event_type == "persist.confirmed":
        if result_revision is None or result_revision <= expected_revision:
            raise StoreCommandError("INVALID_REQUEST", "Persist confirmation requires advancing result_revision")
    payload = {
        "feature_event_id": feature_event_id,
        "expected_revision": expected_revision,
        "target_ref": target_ref,
        "candidate_head_sha": candidate_head_sha,
    }
    if result_revision is not None:
        payload["result_revision"] = result_revision
    working, event = _append_event(
        snapshot,
        operation_id=operation_id,
        generation=generation,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        trusted_context_digest=trusted_context_digest,
        identity_material={"feature_event_id": feature_event_id, "event_type": event_type},
    )
    return _finalize(snapshot, working, [event], operation_id)


def plan_vertical_persist_requested(snapshot: StoreSnapshot, **kwargs) -> StoreMutationPlan:
    return _vertical_persist_event(snapshot, event_type="persist.requested", **kwargs)


def plan_vertical_persist_linearized(snapshot: StoreSnapshot, **kwargs) -> StoreMutationPlan:
    return _vertical_persist_event(snapshot, event_type="persist.linearized", **kwargs)


def plan_vertical_persist_confirmed(snapshot: StoreSnapshot, **kwargs) -> StoreMutationPlan:
    return _vertical_persist_event(snapshot, event_type="persist.confirmed", **kwargs)


def plan_vertical_done(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    generation: int,
    feature_revision: int,
    occurred_at: str,
    trusted_context_digest: str,
) -> StoreMutationPlan:
    current = vertical_projection(snapshot, operation_id)
    if current["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "operation generation is no longer current")
    if current["expected_feature_revision"] != feature_revision:
        raise StoreCommandError("STALE_REVISION", "Feature revision changed before vertical completion")
    working, event = _append_event(
        snapshot,
        operation_id=operation_id,
        generation=generation,
        event_type="operation.done",
        occurred_at=occurred_at,
        payload={"feature_revision": feature_revision, "profile": current.get("operation_profile")},
        trusted_context_digest=trusted_context_digest,
        identity_material={"feature_revision": feature_revision},
    )
    return _finalize(snapshot, working, [event], operation_id)
