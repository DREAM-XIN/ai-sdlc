#!/usr/bin/env python3
"""Atomic Effect Lineage gate composed with the existing Operator Store snapshot/CAS plan."""
from __future__ import annotations

from typing import Any

from operator_store import StoreCommandError, _append_event
from operator_store_model import (
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    canonical_json,
    external_dispatch_key,
    projection_path,
    rebuild_projection,
    reservation_path,
    semantic_effect_key,
    semantic_effect_material,
)
from operator_vertical_store import vertical_projection
from operator_effect_lineage import add_immutable, append_lineage_event, append_projection
from operator_effect_lineage_model import (
    CausalWorkResolver,
    LineageInvariantError,
    anchor_path,
    effect_lineage_id,
    lineage_key_material,
    make_anchor,
    make_member,
    make_proposal,
    member_lineage,
    member_path,
    proposal_identity,
    proposal_path,
    rebuild_lineage_projection,
)


LINEAGE_REQUIRED_PROFILE = "vertical-implementation-review-qa/v1"


def _add_operation_projection(
    original: StoreSnapshot,
    working: StoreSnapshot,
    mutations: list[StoreMutation],
    *,
    operation_id: str,
) -> StoreSnapshot:
    projection = rebuild_projection(working, operation_id)
    mutation = StoreMutation("replace_projection", projection_path(operation_id), projection)
    mutations.append(mutation)
    return apply_plan_to_snapshot(working, StoreMutationPlan(original.ref_sha, (mutation,), {}))


def _add_reservation(
    original: StoreSnapshot,
    working: StoreSnapshot,
    mutations: list[StoreMutation],
    *,
    operation_id: str,
    generation: int,
    exact_material: dict[str, Any],
    occurred_at: str,
    trusted_context_digest: str,
) -> tuple[StoreSnapshot, dict[str, Any]]:
    key = semantic_effect_key(**exact_material)
    value = {
        "semantic_effect_key": key,
        "external_dispatch_key": external_dispatch_key(key),
        **exact_material,
        "created_operation_id": operation_id,
        "created_generation": generation,
        "created_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    path = reservation_path(key)
    existing = working.get(path)
    if existing is not None:
        left = dict(existing)
        right = dict(value)
        for row in (left, right):
            for field in ("created_at", "created_operation_id", "created_generation", "trusted_context_digest"):
                row.pop(field, None)
        if canonical_json(left) != canonical_json(right):
            raise StoreCommandError("ALREADY_CLAIMED", "semantic effect reservation conflicts with existing identity")
        return working, existing
    working = add_immutable(original, working, mutations, path=path, value=value)
    return working, value


def assert_lineage_member(snapshot: StoreSnapshot, semantic_effect_key_value: str, *, expected_lineage_id: str | None = None) -> dict[str, Any]:
    lineage_id = member_lineage(snapshot, semantic_effect_key_value)
    if lineage_id is None:
        raise StoreCommandError("EFFECT_LINEAGE_REQUIRED", "launch-eligible reservation lacks Effect Lineage member")
    if expected_lineage_id is not None and lineage_id != expected_lineage_id:
        raise StoreCommandError("EFFECT_LINEAGE_REQUIRED", "reservation is attached to another Effect Lineage")
    projection = rebuild_lineage_projection(snapshot, lineage_id)
    if projection.get("current_leaf_semantic_effect_key") != semantic_effect_key_value:
        raise StoreCommandError("EFFECT_LINEAGE_BLOCKED", "reservation is not the current launch-eligible lineage member")
    return {"effect_lineage_id": lineage_id, "projection": projection}


def plan_lineage_gated_reservation(
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
    current_target_ref: str,
    operation_profile: str,
    effect_kind: str,
    logical_work_slot: str,
    task_id: str | None,
    occurred_at: str,
    trusted_context_digest: str,
    trusted_profile_digest: str,
    resolver: CausalWorkResolver | None = None,
) -> StoreMutationPlan:
    operation = vertical_projection(snapshot, operation_id)
    if operation["generation"] != generation:
        raise StoreCommandError("SUPERSEDED_GENERATION", "operation generation is no longer current")
    if operation.get("operation_profile") != operation_profile or operation_profile != LINEAGE_REQUIRED_PROFILE:
        raise StoreCommandError("CAPABILITY_UNAVAILABLE", "operation profile is not Effect-Lineage-enabled")
    if operation["expected_feature_revision"] != expected_revision:
        raise StoreCommandError("STALE_REVISION", "expected Feature revision does not match vertical Operation fence")

    resolver = resolver or CausalWorkResolver()
    causal = resolver.resolve(
        feature_id=feature_id,
        operation_profile=operation_profile,
        effect_kind=effect_kind,
        role=role,
        logical_work_slot=logical_work_slot,
        task_id=task_id,
    )
    exact_material = semantic_effect_material(
        target_repository=target_repository,
        feature_id=feature_id,
        expected_revision=expected_revision,
        current_stage=current_stage,
        task_identity=task_identity,
        role=role,
        candidate_head_sha=candidate_head_sha,
    )
    exact_key = semantic_effect_key(**exact_material)
    lineage_material = lineage_key_material(
        target_repository=target_repository,
        feature_id=feature_id,
        operation_profile=operation_profile,
        effect_kind=effect_kind,
        role=role,
        causal_work_id=causal.causal_work_id,
        external_effect_scope=causal.external_effect_scope,
    )
    lineage_id = effect_lineage_id(
        target_repository=target_repository,
        feature_id=feature_id,
        operation_profile=operation_profile,
        effect_kind=effect_kind,
        role=role,
        causal_work_id=causal.causal_work_id,
        external_effect_scope=causal.external_effect_scope,
    )

    existing_member_lineage = member_lineage(snapshot, exact_key)
    if existing_member_lineage is not None and existing_member_lineage != lineage_id:
        raise StoreCommandError("EFFECT_LINEAGE_CONFLICT", "exact semantic effect is already attached to another lineage")

    mutations: list[StoreMutation] = []
    working = snapshot
    anchor = working.get(anchor_path(lineage_id))

    if anchor is None:
        # Existing exact reservations pre-dating lineage require the explicit legacy resolver.
        if working.get(reservation_path(exact_key)) is not None:
            raise StoreCommandError("LEGACY_UNRESOLVED_LINEAGE", "legacy reservation requires trusted lineage migration")
        anchor_value = make_anchor(
            lineage_id=lineage_id,
            material=lineage_material,
            created_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
        )
        working = add_immutable(original=snapshot, working=working, mutations=mutations, path=anchor_path(lineage_id), value=anchor_value)
        working, reservation = _add_reservation(
            snapshot,
            working,
            mutations,
            operation_id=operation_id,
            generation=generation,
            exact_material=exact_material,
            occurred_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
        )
        member = make_member(
            lineage_id=lineage_id,
            semantic_effect_key=exact_key,
            external_dispatch_key=reservation["external_dispatch_key"],
            operation_id=operation_id,
            operation_generation=generation,
            expected_revision=expected_revision,
            stage=current_stage,
            task_identity=task_identity,
            role=role,
            candidate_head_sha=candidate_head_sha,
            predecessor_semantic_effect_key=None,
            activated_from_proposal_id=None,
            activated_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
        )
        working = add_immutable(snapshot, working, mutations, path=member_path(lineage_id, exact_key), value=member)
        working = append_lineage_event(
            snapshot,
            working,
            mutations,
            lineage_id=lineage_id,
            event_type="lineage.root-activated",
            occurred_at=occurred_at,
            payload={"semantic_effect_key": exact_key},
            trusted_context_digest=trusted_context_digest,
            identity_material={"semantic_effect_key": exact_key},
        )
        working = append_projection(snapshot, working, mutations, lineage_id=lineage_id)
        return StoreMutationPlan(
            snapshot.ref_sha,
            tuple(mutations),
            {
                "status": "ACTIVE",
                "effect_lineage_id": lineage_id,
                "semantic_effect_key": exact_key,
                "external_dispatch_key": reservation["external_dispatch_key"],
            },
        )

    if not isinstance(anchor, dict) or canonical_json({k: anchor.get(k) for k in lineage_material if k != "schema"}) != canonical_json({k: lineage_material[k] for k in lineage_material if k != "schema"}):
        raise LineageInvariantError("existing lineage anchor conflicts with trusted causal material")

    projection = rebuild_lineage_projection(working, lineage_id)
    current_leaf = projection.get("current_leaf_semantic_effect_key")
    if current_leaf is None:
        raise LineageInvariantError("lineage anchor exists without active root/member history")

    if exact_key == current_leaf:
        reservation = working.get(reservation_path(exact_key))
        if not isinstance(reservation, dict):
            raise LineageInvariantError("current lineage member lacks exact reservation")
        return StoreMutationPlan(
            snapshot.ref_sha,
            tuple(),
            {
                "status": "EXISTING_MEMBER",
                "effect_lineage_id": lineage_id,
                "semantic_effect_key": exact_key,
                "external_dispatch_key": reservation["external_dispatch_key"],
            },
        )

    pid = proposal_identity(
        lineage_id=lineage_id,
        predecessor_semantic_effect_key=current_leaf,
        proposed_exact_semantic_material=exact_material,
        current_feature_revision=expected_revision,
        current_stage=current_stage,
        current_target_ref=current_target_ref,
        current_candidate_head_sha=candidate_head_sha,
        operation_id=operation_id,
        operation_generation=generation,
        trusted_profile_digest=trusted_profile_digest,
    )
    proposal = make_proposal(
        proposal_id=pid,
        lineage_id=lineage_id,
        predecessor_semantic_effect_key=current_leaf,
        proposed_semantic_effect_key=exact_key,
        proposed_exact_semantic_material=exact_material,
        current_feature_revision=expected_revision,
        current_stage=current_stage,
        current_target_ref=current_target_ref,
        current_candidate_head_sha=candidate_head_sha,
        operation_id=operation_id,
        operation_generation=generation,
        trusted_profile_digest=trusted_profile_digest,
        proposed_at=occurred_at,
        trusted_context_digest=trusted_context_digest,
    )
    working = add_immutable(snapshot, working, mutations, path=proposal_path(lineage_id, pid), value=proposal)
    working = append_lineage_event(
        snapshot,
        working,
        mutations,
        lineage_id=lineage_id,
        event_type="lineage.successor-proposed",
        occurred_at=occurred_at,
        payload={
            "proposal_id": pid,
            "predecessor_semantic_effect_key": current_leaf,
            "proposed_semantic_effect_key": exact_key,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"proposal_id": pid},
    )
    working = append_lineage_event(
        snapshot,
        working,
        mutations,
        lineage_id=lineage_id,
        event_type="lineage.predecessor-blocked",
        occurred_at=occurred_at,
        payload={
            "proposal_id": pid,
            "predecessor_semantic_effect_key": current_leaf,
            "predecessor_state": projection.get("predecessor_state"),
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"proposal_id": pid, "predecessor_semantic_effect_key": current_leaf},
    )
    working, op_event = _append_event(
        working,
        operation_id=operation_id,
        generation=generation,
        event_type="effect.lineage.blocked",
        occurred_at=occurred_at,
        payload={
            "effect_lineage_id": lineage_id,
            "proposal_id": pid,
            "predecessor_semantic_effect_key": current_leaf,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"effect_lineage_id": lineage_id, "proposal_id": pid},
    )
    mutations.append(op_event)
    working = append_projection(snapshot, working, mutations, lineage_id=lineage_id)
    working = _add_operation_projection(snapshot, working, mutations, operation_id=operation_id)
    if working.get(reservation_path(exact_key)) is not None:
        raise LineageInvariantError("blocked successor unexpectedly has an exact reservation")
    return StoreMutationPlan(
        snapshot.ref_sha,
        tuple(mutations),
        {
            "status": "BLOCKED",
            "reason": "UNRESOLVED_PREDECESSOR",
            "effect_lineage_id": lineage_id,
            "proposal_id": pid,
            "predecessor_semantic_effect_key": current_leaf,
            "predecessor_state": projection.get("predecessor_state"),
            "proposed_semantic_effect_key": exact_key,
        },
    )
