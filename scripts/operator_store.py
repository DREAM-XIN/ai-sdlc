#!/usr/bin/env python3
"""Pure semantic commands for the durable AI-SDLC Operator Store."""
from __future__ import annotations

from typing import Any

from operator_store_model import (
    StoreInvariantError,
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    canonical_json,
    digest_json,
    dispatch_claim_id,
    dispatch_claim_path,
    event_path,
    external_dispatch_key,
    feature_claim_id,
    feature_claim_path,
    make_event,
    next_sequence,
    operation_events,
    operation_id_for,
    operation_ids,
    projection_path,
    projection_public,
    rebuild_projection,
    reservation_path,
    semantic_effect_key,
    semantic_effect_material,
    unfinished_operations,
)


class StoreCommandError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _event_id(event_type: str, material: dict[str, Any]) -> str:
    return event_type.replace(".", "-") + "-" + digest_json(material)[:32]


def _append_event(
    snapshot: StoreSnapshot,
    *,
    operation_id: str,
    generation: int,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    trusted_context_digest: str,
    identity_material: dict[str, Any] | None = None,
) -> tuple[StoreSnapshot, StoreMutation]:
    material = dict(identity_material or payload)
    material.update({"operation_id": operation_id, "generation": generation, "event_type": event_type})
    event_id = _event_id(event_type, material)
    for event in operation_events(snapshot, operation_id):
        if event["event_id"] == event_id:
            expected = make_event(
                operation_id=operation_id,
                generation=generation,
                sequence=event["sequence"],
                event_id=event_id,
                event_type=event_type,
                occurred_at=event["occurred_at"],
                payload=payload,
                trusted_context_digest=trusted_context_digest,
            )
            comparable_existing = dict(event)
            comparable_expected = dict(expected)
            comparable_existing["occurred_at"] = "<ignored>"
            comparable_expected["occurred_at"] = "<ignored>"
            if canonical_json(comparable_existing) != canonical_json(comparable_expected):
                raise StoreCommandError("ALREADY_APPLIED", "event identity already exists with different semantics")
            return snapshot, StoreMutation("create_immutable", event_path(operation_id, event["sequence"], event_id), event)
    sequence = next_sequence(snapshot, operation_id)
    event = make_event(
        operation_id=operation_id,
        generation=generation,
        sequence=sequence,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        trusted_context_digest=trusted_context_digest,
    )
    mutation = StoreMutation("create_immutable", event_path(operation_id, sequence, event_id), event)
    temp_plan = StoreMutationPlan(snapshot.ref_sha, (mutation,), {})
    return apply_plan_to_snapshot(snapshot, temp_plan), mutation


def _finalize(snapshot: StoreSnapshot, working: StoreSnapshot, mutations: list[StoreMutation], operation_id: str, result: dict[str, Any] | None = None) -> StoreMutationPlan:
    projection = rebuild_projection(working, operation_id)
    projection_mutation = StoreMutation("replace_projection", projection_path(operation_id), projection)
    return StoreMutationPlan(snapshot.ref_sha, tuple(mutations + [projection_mutation]), result or projection_public(projection))


def _active_feature_operation(snapshot: StoreSnapshot, target_repository: str, feature_id: str) -> dict[str, Any] | None:
    candidates = []
    for path, claim in snapshot.files.items():
        if "/claims/feature/" not in path or not isinstance(claim, dict):
            continue
        if str(claim.get("target_repository", "")).lower() != target_repository.lower() or claim.get("feature_id") != feature_id:
            continue
        op_id = claim.get("operation_id")
        if op_id not in operation_ids(snapshot):
            raise StoreInvariantError("feature claim references missing operation")
        projection = rebuild_projection(snapshot, op_id)
        if projection["status"] not in {"DONE", "CANCELLED"}:
            candidates.append(projection)
    unique = {row["operation_id"]: row for row in candidates}
    if len(unique) > 1:
        raise StoreInvariantError("multiple nonterminal operation owners for feature")
    return next(iter(unique.values()), None)


def plan_operation_start(
    snapshot: StoreSnapshot,
    *,
    target_repository: str,
    feature_id: str,
    expected_revision: int,
    idempotency_key: str,
    occurred_at: str,
    trusted_context_digest: str,
) -> StoreMutationPlan:
    active = _active_feature_operation(snapshot, target_repository, feature_id)
    if active is not None:
        if active.get("expected_feature_revision") != expected_revision:
            raise StoreCommandError("ALREADY_CLAIMED", "feature already has an active operation at another revision")
        return StoreMutationPlan(snapshot.ref_sha, tuple(), projection_public(active))

    operation_id = operation_id_for(target_repository, feature_id, idempotency_key)
    if operation_id in operation_ids(snapshot):
        projection = rebuild_projection(snapshot, operation_id)
        return StoreMutationPlan(snapshot.ref_sha, tuple(), projection_public(projection))

    generation = 0
    claim_id = feature_claim_id(operation_id, generation)
    claim = {
        "claim_id": claim_id,
        "target_repository": target_repository.lower(),
        "feature_id": feature_id,
        "operation_id": operation_id,
        "operation_generation": generation,
        "expected_revision": expected_revision,
        "idempotency_key": idempotency_key,
        "created_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    mutations = [StoreMutation("create_immutable", feature_claim_path(target_repository, feature_id, claim_id), claim)]
    working = apply_plan_to_snapshot(snapshot, StoreMutationPlan(snapshot.ref_sha, tuple(mutations), {}))
    working, event_mutation = _append_event(
        working,
        operation_id=operation_id,
        generation=generation,
        event_type="operation.started",
        occurred_at=occurred_at,
        payload={"target_repository": target_repository.lower(), "feature_id": feature_id, "expected_revision": expected_revision},
        trusted_context_digest=trusted_context_digest,
        identity_material={"idempotency_key": idempotency_key},
    )
    mutations.append(event_mutation)
    return _finalize(snapshot, working, mutations, operation_id)


def plan_semantic_reservation(
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
    projection = rebuild_projection(snapshot, operation_id)
    if projection["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "operation generation is no longer current")
    if projection["status"] == "CANCELLED":
        raise StoreCommandError("CANCELLED_OPERATION", "operation is cancelled")
    if projection["expected_feature_revision"] != expected_revision:
        raise StoreCommandError("STALE_REVISION", "expected feature revision does not match operation")
    material = semantic_effect_material(
        target_repository=target_repository,
        feature_id=feature_id,
        expected_revision=expected_revision,
        current_stage=current_stage,
        task_identity=task_identity,
        role=role,
        candidate_head_sha=candidate_head_sha,
    )
    effect_key = semantic_effect_key(**material)
    reservation = {
        "semantic_effect_key": effect_key,
        "external_dispatch_key": external_dispatch_key(effect_key),
        **material,
        "created_operation_id": operation_id,
        "created_generation": generation,
        "created_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    path = reservation_path(effect_key)
    existing = snapshot.get(path)
    if existing is not None:
        comparable_existing = dict(existing)
        comparable_new = dict(reservation)
        for value in (comparable_existing, comparable_new):
            value.pop("created_at", None)
            value.pop("created_operation_id", None)
            value.pop("created_generation", None)
            value.pop("trusted_context_digest", None)
        if canonical_json(comparable_existing) != canonical_json(comparable_new):
            raise StoreCommandError("ALREADY_CLAIMED", "semantic effect reservation conflicts with existing identity")
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"semantic_effect_key": effect_key, "external_dispatch_key": existing["external_dispatch_key"]})
    return StoreMutationPlan(snapshot.ref_sha, (StoreMutation("create_immutable", path, reservation),), {"semantic_effect_key": effect_key, "external_dispatch_key": reservation["external_dispatch_key"]})


def plan_dispatch_claim(snapshot: StoreSnapshot, *, operation_id: str, generation: int, effect_key: str, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if projection["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "dispatch claim generation is not current")
    if projection["status"] == "CANCELLED":
        raise StoreCommandError("CANCELLED_OPERATION", "operation is cancelled")
    reservation = snapshot.get(reservation_path(effect_key))
    if not isinstance(reservation, dict):
        raise StoreCommandError("INVALID_REQUEST", "semantic reservation does not exist")
    claim_id = dispatch_claim_id(operation_id, generation, effect_key)
    claim = {
        "claim_id": claim_id,
        "operation_id": operation_id,
        "operation_generation": generation,
        "semantic_effect_key": effect_key,
        "external_dispatch_key": reservation["external_dispatch_key"],
        "created_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    path = dispatch_claim_path(claim_id)
    if snapshot.get(path) is not None:
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"claim_id": claim_id, "external_dispatch_key": claim["external_dispatch_key"]})
    mutations = [StoreMutation("create_immutable", path, claim)]
    working = apply_plan_to_snapshot(snapshot, StoreMutationPlan(snapshot.ref_sha, tuple(mutations), {}))
    working, event_mutation = _append_event(
        working,
        operation_id=operation_id,
        generation=generation,
        event_type="dispatch.claimed",
        occurred_at=occurred_at,
        payload={"claim_id": claim_id, "semantic_effect_key": effect_key, "external_dispatch_key": claim["external_dispatch_key"]},
        trusted_context_digest=trusted_context_digest,
        identity_material={"claim_id": claim_id},
    )
    mutations.append(event_mutation)
    return _finalize(snapshot, working, mutations, operation_id, {"claim_id": claim_id, "external_dispatch_key": claim["external_dispatch_key"]})


def plan_authorize_launch(snapshot: StoreSnapshot, *, operation_id: str, generation: int, claim_id: str, dispatch_id: str, occurred_at: str, trusted_context_digest: str, verified_expected_revision: int, verified_stage: str, verified_candidate_head_sha: str | None = None) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if projection["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "launch generation is not current")
    if projection["status"] == "CANCELLED":
        raise StoreCommandError("CANCELLED_OPERATION", "launch authorization fenced by cancellation")
    claim = snapshot.get(dispatch_claim_path(claim_id))
    if not isinstance(claim, dict) or claim.get("operation_id") != operation_id or claim.get("operation_generation") != generation:
        raise StoreCommandError("ALREADY_CLAIMED", "dispatch claim is not owned by current generation")
    reservation = snapshot.get(reservation_path(claim["semantic_effect_key"]))
    if not isinstance(reservation, dict) or reservation.get("external_dispatch_key") != claim.get("external_dispatch_key"):
        raise StoreInvariantError("dispatch claim/reservation binding mismatch")
    if reservation.get("expected_revision") != verified_expected_revision:
        raise StoreCommandError("STALE_REVISION", "verified feature revision does not match reservation")
    if reservation.get("current_stage") != verified_stage or reservation.get("candidate_head_sha") != verified_candidate_head_sha:
        raise StoreCommandError("STALE_REVISION", "verified stage/candidate binding does not match reservation")
    payload = {
        "claim_id": claim_id,
        "dispatch_id": dispatch_id,
        "semantic_effect_key": claim["semantic_effect_key"],
        "external_dispatch_key": claim["external_dispatch_key"],
        "feature_id": reservation["feature_id"],
        "expected_revision": verified_expected_revision,
        "stage": verified_stage,
        "role": reservation["role"],
        "candidate_head_sha": verified_candidate_head_sha,
    }
    working, mutation = _append_event(snapshot, operation_id=operation_id, generation=generation, event_type="dispatch.launch.authorized", occurred_at=occurred_at, payload=payload, trusted_context_digest=trusted_context_digest, identity_material={"claim_id": claim_id, "dispatch_id": dispatch_id})
    return _finalize(snapshot, working, [mutation], operation_id)


def plan_launch_lookup(snapshot: StoreSnapshot, *, operation_id: str, generation: int, external_dispatch_key_value: str, lookup_state: str, receipt_id: str | None, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    if lookup_state not in {"NOT_LAUNCHED", "LAUNCHED", "UNKNOWN"}:
        raise StoreCommandError("INVALID_REQUEST", "invalid launch receipt state")
    projection = rebuild_projection(snapshot, operation_id)
    if generation != projection["generation"]:
        raise StoreCommandError("SUPERSEDED_GENERATION", "lookup generation is not current")
    if external_dispatch_key_value not in projection["authorized_dispatches"] and lookup_state != "UNKNOWN":
        raise StoreCommandError("INVALID_REQUEST", "lookup is not correlated to an authorized dispatch")
    payload = {"external_dispatch_key": external_dispatch_key_value, "lookup_state": lookup_state, "receipt_id": receipt_id}
    working, mutation = _append_event(snapshot, operation_id=operation_id, generation=generation, event_type="dispatch.launch.lookup-recorded", occurred_at=occurred_at, payload=payload, trusted_context_digest=trusted_context_digest, identity_material=payload)
    return _finalize(snapshot, working, [mutation], operation_id)


def plan_callback(snapshot: StoreSnapshot, *, operation_id: str, generation: int, callback_id: str, callback_payload: dict[str, Any], external_dispatch_key_value: str, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if generation != projection["generation"]:
        raise StoreCommandError("SUPERSEDED_GENERATION", "callback generation is not current")
    payload = {"callback_id": callback_id, "callback_digest": digest_json(callback_payload), "external_dispatch_key": external_dispatch_key_value}
    working, mutation = _append_event(snapshot, operation_id=operation_id, generation=generation, event_type="worker.callback.recorded", occurred_at=occurred_at, payload=payload, trusted_context_digest=trusted_context_digest, identity_material={"callback_id": callback_id})
    return _finalize(snapshot, working, [mutation], operation_id)


def plan_cancel(snapshot: StoreSnapshot, *, operation_id: str, reason: str, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if projection["status"] == "CANCELLED":
        return StoreMutationPlan(snapshot.ref_sha, tuple(), projection_public(projection))
    if projection["status"] == "DONE":
        raise StoreCommandError("ALREADY_APPLIED", "operation is already done")
    generation = projection["generation"]
    working, mutation = _append_event(snapshot, operation_id=operation_id, generation=generation, event_type="operation.cancelled", occurred_at=occurred_at, payload={"reason": reason[:512]}, trusted_context_digest=trusted_context_digest, identity_material={"operation_id": operation_id})
    return _finalize(snapshot, working, [mutation], operation_id)


def plan_takeover(snapshot: StoreSnapshot, *, operation_id: str, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if projection["status"] in {"DONE", "CANCELLED"}:
        raise StoreCommandError("CANCELLED_OPERATION", "terminal operation cannot be taken over")
    old_generation = projection["generation"]
    new_generation = old_generation + 1
    mutations: list[StoreMutation] = []
    working, first = _append_event(snapshot, operation_id=operation_id, generation=old_generation, event_type="operation.superseded", occurred_at=occurred_at, payload={"superseded_generation": old_generation, "next_generation": new_generation}, trusted_context_digest=trusted_context_digest, identity_material={"next_generation": new_generation})
    mutations.append(first)
    working, second = _append_event(working, operation_id=operation_id, generation=new_generation, event_type="operation.generation.started", occurred_at=occurred_at, payload={"previous_generation": old_generation}, trusted_context_digest=trusted_context_digest, identity_material={"generation": new_generation})
    mutations.append(second)
    return _finalize(snapshot, working, mutations, operation_id)


def _plan_persist_event(snapshot: StoreSnapshot, *, operation_id: str, generation: int, event_type: str, feature_event_id: str, expected_revision: int, target_ref: str, candidate_head_sha: str | None, occurred_at: str, trusted_context_digest: str) -> StoreMutationPlan:
    projection = rebuild_projection(snapshot, operation_id)
    if projection["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "persist generation is not current")
    if projection["status"] == "CANCELLED" and event_type != "persist.confirmed":
        raise StoreCommandError("CANCELLED_OPERATION", "persist authorization fenced by cancellation")
    if projection["expected_feature_revision"] != expected_revision:
        raise StoreCommandError("STALE_REVISION", "persist expected revision does not match operation")
    payload = {"feature_event_id": feature_event_id, "expected_revision": expected_revision, "target_ref": target_ref, "candidate_head_sha": candidate_head_sha}
    working, mutation = _append_event(snapshot, operation_id=operation_id, generation=generation, event_type=event_type, occurred_at=occurred_at, payload=payload, trusted_context_digest=trusted_context_digest, identity_material={"feature_event_id": feature_event_id, "event_type": event_type})
    return _finalize(snapshot, working, [mutation], operation_id)


def plan_persist_requested(snapshot: StoreSnapshot, **kwargs: Any) -> StoreMutationPlan:
    return _plan_persist_event(snapshot, event_type="persist.requested", **kwargs)


def plan_persist_linearized(snapshot: StoreSnapshot, **kwargs: Any) -> StoreMutationPlan:
    return _plan_persist_event(snapshot, event_type="persist.linearized", **kwargs)


def plan_persist_confirmed(snapshot: StoreSnapshot, **kwargs: Any) -> StoreMutationPlan:
    return _plan_persist_event(snapshot, event_type="persist.confirmed", **kwargs)


def query_unfinished(snapshot: StoreSnapshot, *, target_repository: str | None = None, feature_id: str | None = None) -> list[dict[str, Any]]:
    return unfinished_operations(snapshot, target_repository=target_repository, feature_id=feature_id)
