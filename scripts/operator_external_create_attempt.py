#!/usr/bin/env python3
"""Durable one-shot external-create attempt authority for the v0.3 Vertical runtime."""
from __future__ import annotations

import re
from typing import Any

from operator_store import StoreCommandError
from operator_store_model import (
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    digest_json,
    dispatch_claim_path,
    operation_events,
    reservation_path,
)

ATTEMPT_SCHEMA = "ai-sdlc.external-create-attempt/v1"
_ATTEMPT_SUFFIX = "/external-create-attempt.json"
_EFFECT_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")


def external_create_attempt_id(semantic_effect_key: str, external_dispatch_key: str) -> str:
    return "eca-" + digest_json(
        {
            "semantic_effect_key": semantic_effect_key,
            "external_dispatch_key": external_dispatch_key,
        }
    )[:40]


def external_create_attempt_path(semantic_effect_key: str) -> str:
    if not _EFFECT_KEY_RE.fullmatch(str(semantic_effect_key)):
        raise StoreCommandError("INVALID_REQUEST", "external-create attempt requires an exact semantic effect key")
    return (
        "state/operator/v1/reservations/external/"
        + str(semantic_effect_key)
        + _ATTEMPT_SUFFIX
    )


def is_external_create_attempt_path(path: str) -> bool:
    return (
        path.startswith("state/operator/v1/reservations/external/")
        and path.endswith(_ATTEMPT_SUFFIX)
    )


def _normalize_execution_binding(binding: dict[str, Any]) -> dict[str, str]:
    if not isinstance(binding, dict):
        raise StoreCommandError("POLICY_DENIED", "external-create attempt lacks trusted execution binding")
    required = (
        "worker_id",
        "role",
        "profile",
        "workflow_file",
        "selection_policy",
        "default_branch",
    )
    normalized: dict[str, str] = {}
    for field in required:
        value = binding.get(field)
        if not isinstance(value, str) or not value.strip():
            raise StoreCommandError("POLICY_DENIED", f"external-create execution binding lacks {field}")
        normalized[field] = value.strip()
    if not _WORKFLOW_RE.fullmatch(normalized["workflow_file"]):
        raise StoreCommandError("POLICY_DENIED", "external-create execution binding has invalid workflow filename")
    if any(ch.isspace() for ch in normalized["default_branch"]):
        raise StoreCommandError("POLICY_DENIED", "external-create execution binding has invalid default branch")
    credential_name = binding.get("credential_name")
    if credential_name is not None:
        if not isinstance(credential_name, str) or not credential_name.strip():
            raise StoreCommandError("POLICY_DENIED", "external-create execution binding has invalid credential name")
        normalized["credential_name"] = credential_name.strip()
    return normalized


def _authorization_record(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    generation: int,
    claim_id: str,
    dispatch_id: str,
    semantic_effect_key: str,
    external_dispatch_key: str,
) -> dict[str, Any]:
    reservation = snapshot.get(reservation_path(semantic_effect_key))
    if not isinstance(reservation, dict):
        raise StoreCommandError("INVALID_REQUEST", "external-create attempt lacks semantic reservation")
    if reservation.get("semantic_effect_key") != semantic_effect_key:
        raise StoreCommandError("POLICY_DENIED", "semantic reservation key binding mismatch")
    if reservation.get("external_dispatch_key") != external_dispatch_key:
        raise StoreCommandError("POLICY_DENIED", "semantic reservation external key binding mismatch")

    claim = snapshot.get(dispatch_claim_path(claim_id))
    if not isinstance(claim, dict):
        raise StoreCommandError("INVALID_REQUEST", "external-create attempt lacks dispatch claim")
    claim_expected = {
        "claim_id": claim_id,
        "operation_id": operation_id,
        "operation_generation": generation,
        "semantic_effect_key": semantic_effect_key,
        "external_dispatch_key": external_dispatch_key,
    }
    if any(claim.get(key) != value for key, value in claim_expected.items()):
        raise StoreCommandError("POLICY_DENIED", "external-create dispatch claim binding mismatch")

    matches = []
    for event in operation_events(snapshot, operation_id):
        if event.get("event_type") != "dispatch.launch.authorized":
            continue
        if event.get("operation_generation") != generation:
            continue
        payload = event.get("payload") or {}
        expected = {
            "claim_id": claim_id,
            "dispatch_id": dispatch_id,
            "semantic_effect_key": semantic_effect_key,
            "external_dispatch_key": external_dispatch_key,
        }
        if all(payload.get(key) == value for key, value in expected.items()):
            matches.append(event)
    if len(matches) != 1:
        raise StoreCommandError(
            "POLICY_DENIED",
            "external-create attempt is not bound to one exact durable launch authorization",
        )
    event = matches[0]
    payload = event.get("payload") or {}
    reservation_expected = {
        "feature_id": reservation.get("feature_id"),
        "expected_revision": reservation.get("expected_revision"),
        "stage": reservation.get("current_stage"),
        "role": reservation.get("role"),
        "candidate_head_sha": reservation.get("candidate_head_sha"),
    }
    if any(payload.get(key) != value for key, value in reservation_expected.items()):
        raise StoreCommandError(
            "POLICY_DENIED",
            "external-create authorization does not match semantic reservation identity",
        )
    return event


def validate_external_create_attempt(snapshot: StoreSnapshot, attempt: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(attempt, dict) or attempt.get("schema_version") != ATTEMPT_SCHEMA:
        raise StoreCommandError("POLICY_DENIED", "invalid external-create attempt artifact")
    semantic_effect_key = str(attempt.get("semantic_effect_key") or "")
    external_dispatch_key = str(attempt.get("external_dispatch_key") or "")
    attempt_id = external_create_attempt_id(semantic_effect_key, external_dispatch_key)
    if attempt.get("attempt_id") != attempt_id:
        raise StoreCommandError("POLICY_DENIED", "external-create attempt id binding mismatch")
    operation_id = str(attempt.get("created_operation_id") or "")
    generation = attempt.get("created_generation")
    claim_id = str(attempt.get("creator_claim_id") or "")
    dispatch_id = str(attempt.get("creator_dispatch_id") or "")
    if not operation_id or type(generation) is not int or generation < 0 or not claim_id or not dispatch_id:
        raise StoreCommandError("POLICY_DENIED", "external-create attempt creator provenance is incomplete")
    event = _authorization_record(
        snapshot,
        operation_id=operation_id,
        generation=generation,
        claim_id=claim_id,
        dispatch_id=dispatch_id,
        semantic_effect_key=semantic_effect_key,
        external_dispatch_key=external_dispatch_key,
    )
    if attempt.get("authorization_event_id") != event.get("event_id"):
        raise StoreCommandError("POLICY_DENIED", "external-create attempt authorization event binding mismatch")
    execution_binding = _normalize_execution_binding(attempt.get("execution_binding"))
    reservation = snapshot.get(reservation_path(semantic_effect_key))
    if execution_binding["role"] != reservation.get("role"):
        raise StoreCommandError("POLICY_DENIED", "external-create worker role does not match semantic reservation")
    normalized = dict(attempt)
    normalized["execution_binding"] = execution_binding
    return normalized


def find_external_create_attempt(
    snapshot: StoreSnapshot,
    *,
    external_dispatch_key: str,
) -> dict[str, Any] | None:
    rows = []
    for path, value in snapshot.files.items():
        if not is_external_create_attempt_path(path) or not isinstance(value, dict):
            continue
        if value.get("external_dispatch_key") == external_dispatch_key:
            rows.append(value)
    if not rows:
        return None
    if len(rows) != 1:
        raise StoreCommandError("POLICY_DENIED", "multiple external-create attempts share one external dispatch key")
    return validate_external_create_attempt(snapshot, rows[0])


def plan_external_create_attempt(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    generation: int,
    claim_id: str,
    dispatch_id: str,
    semantic_effect_key: str,
    external_dispatch_key_value: str,
    execution_binding: dict[str, Any],
    occurred_at: str,
    trusted_context_digest: str,
) -> StoreMutationPlan:
    """Acquire one global create permission or reuse the already-durable winner."""
    execution = _normalize_execution_binding(execution_binding)
    event = _authorization_record(
        snapshot,
        operation_id=operation_id,
        generation=generation,
        claim_id=claim_id,
        dispatch_id=dispatch_id,
        semantic_effect_key=semantic_effect_key,
        external_dispatch_key=external_dispatch_key_value,
    )
    reservation = snapshot.get(reservation_path(semantic_effect_key))
    if execution["role"] != reservation.get("role"):
        raise StoreCommandError("POLICY_DENIED", "external-create execution role does not match reservation")

    path = external_create_attempt_path(semantic_effect_key)
    existing = snapshot.get(path)
    if existing is not None:
        normalized = validate_external_create_attempt(snapshot, existing)
        if normalized.get("semantic_effect_key") != semantic_effect_key:
            raise StoreCommandError("POLICY_DENIED", "external-create attempt semantic identity conflict")
        if normalized.get("external_dispatch_key") != external_dispatch_key_value:
            raise StoreCommandError("POLICY_DENIED", "external-create attempt external identity conflict")
        return StoreMutationPlan(
            snapshot.ref_sha,
            tuple(),
            {
                "attempt_id": normalized["attempt_id"],
                "acquired": False,
                "semantic_effect_key": semantic_effect_key,
                "external_dispatch_key": external_dispatch_key_value,
                "execution_binding": dict(normalized["execution_binding"]),
                "created_operation_id": normalized["created_operation_id"],
                "created_generation": normalized["created_generation"],
            },
        )

    value = {
        "schema_version": ATTEMPT_SCHEMA,
        "attempt_id": external_create_attempt_id(semantic_effect_key, external_dispatch_key_value),
        "semantic_effect_key": semantic_effect_key,
        "external_dispatch_key": external_dispatch_key_value,
        "created_operation_id": operation_id,
        "created_generation": generation,
        "creator_claim_id": claim_id,
        "creator_dispatch_id": dispatch_id,
        "authorization_event_id": event["event_id"],
        "execution_binding": execution,
        "created_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    return StoreMutationPlan(
        snapshot.ref_sha,
        (StoreMutation("create_immutable", path, value),),
        {
            "attempt_id": value["attempt_id"],
            "acquired": True,
            "semantic_effect_key": semantic_effect_key,
            "external_dispatch_key": external_dispatch_key_value,
            "execution_binding": dict(execution),
            "created_operation_id": operation_id,
            "created_generation": generation,
        },
    )
