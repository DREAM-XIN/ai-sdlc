#!/usr/bin/env python3
"""Recovery helpers specific to the v0.3 vertical Operator profile."""
from __future__ import annotations

from typing import Any

from operator_store import StoreCommandError, _append_event, _finalize, plan_takeover
from operator_store_model import StoreSnapshot, digest_json, operation_events, rebuild_projection
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE


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
    """Durably record the normalized trusted callback envelope in the existing callback event.

    Extra payload fields are intentionally ignored by the legacy Store projection while the
    vertical recovery path can reconstruct the exact normalized envelope after process loss.
    """
    projection = rebuild_projection(snapshot, context.operation_id)
    if projection.get("operation_profile") != VERTICAL_PROFILE:
        raise StoreCommandError("CAPABILITY_UNAVAILABLE", "Operation is not a vertical profile")
    if projection["generation"] != context.operation_generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "callback belongs to a superseded generation")
    if context.external_dispatch_key not in projection["authorized_dispatches"]:
        raise StoreCommandError("INVALID_REQUEST", "callback is not correlated to an authorized dispatch")
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
