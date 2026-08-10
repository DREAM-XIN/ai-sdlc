#!/usr/bin/env python3
"""Fail-closed migration helpers for legacy exact semantic reservations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_store import StoreCommandError
from operator_store_model import StoreMutation, StoreMutationPlan, StoreSnapshot, reservation_path
from operator_effect_lineage import add_immutable, append_lineage_event, append_projection
from operator_effect_lineage_model import (
    CausalWorkResolver,
    LineageInvariantError,
    anchor_path,
    effect_lineage_id,
    lineage_events,
    lineage_key_material,
    lineage_members,
    make_anchor,
    make_member,
    member_lineage,
    member_path,
)


@dataclass(frozen=True)
class LegacyMigrationEvidence:
    source: str
    provenance_digest: str
    unique_lineage_proven: bool

    def __post_init__(self):
        if self.source not in {"protected-store-reconstruction", "trusted-profile-reconstruction"}:
            raise ValueError("legacy migration evidence source is not trusted")
        if not self.provenance_digest:
            raise ValueError("legacy migration evidence lacks provenance digest")


def validate_lineage_rollout(*, old_writers_quiesced: bool, effect_lineage_required: bool) -> None:
    if effect_lineage_required and not old_writers_quiesced:
        raise StoreCommandError(
            "MIXED_WRITER_FORBIDDEN",
            "effect_lineage_required cannot become authoritative before old reservation writers are fenced",
        )


def plan_legacy_lineage_attachment(
    snapshot: StoreSnapshot,
    *,
    semantic_effect_key: str,
    target_repository: str,
    feature_id: str,
    operation_profile: str,
    effect_kind: str,
    role: str,
    logical_work_slot: str,
    task_id: str | None,
    evidence: LegacyMigrationEvidence,
    occurred_at: str,
    trusted_context_digest: str,
    resolver: CausalWorkResolver | None = None,
) -> StoreMutationPlan:
    reservation = snapshot.get(reservation_path(semantic_effect_key))
    if not isinstance(reservation, dict):
        raise StoreCommandError("INVALID_REQUEST", "legacy semantic reservation does not exist")
    existing = member_lineage(snapshot, semantic_effect_key)
    if existing is not None:
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {"status": "ALREADY_ATTACHED", "effect_lineage_id": existing})
    if reservation.get("feature_id") != feature_id or reservation.get("role") != role:
        raise StoreCommandError("LEGACY_UNRESOLVED_LINEAGE", "legacy reservation trusted identity does not match requested lineage")

    causal = (resolver or CausalWorkResolver()).resolve(
        feature_id=feature_id,
        operation_profile=operation_profile,
        effect_kind=effect_kind,
        role=role,
        logical_work_slot=logical_work_slot,
        task_id=task_id,
    )
    material = lineage_key_material(
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
    mutations: list[StoreMutation] = []
    working = snapshot
    anchor = working.get(anchor_path(lineage_id))
    if anchor is None:
        working = add_immutable(
            snapshot,
            working,
            mutations,
            path=anchor_path(lineage_id),
            value=make_anchor(
                lineage_id=lineage_id,
                material=material,
                created_at=occurred_at,
                trusted_context_digest=trusted_context_digest,
            ),
        )
    elif not isinstance(anchor, dict):
        raise LineageInvariantError("legacy target lineage anchor is invalid")

    if not evidence.unique_lineage_proven:
        working = append_lineage_event(
            snapshot,
            working,
            mutations,
            lineage_id=lineage_id,
            event_type="lineage.legacy-unresolved",
            occurred_at=occurred_at,
            payload={
                "semantic_effect_key": semantic_effect_key,
                "state": "LEGACY_UNRESOLVED_LINEAGE",
                "provenance_digest": evidence.provenance_digest,
            },
            trusted_context_digest=trusted_context_digest,
            identity_material={"semantic_effect_key": semantic_effect_key, "provenance_digest": evidence.provenance_digest},
        )
        working = append_projection(snapshot, working, mutations, lineage_id=lineage_id)
        return StoreMutationPlan(
            snapshot.ref_sha,
            tuple(mutations),
            {"status": "BLOCKED", "reason": "LEGACY_UNRESOLVED_LINEAGE", "effect_lineage_id": lineage_id},
        )

    if lineage_members(working, lineage_id) or any(
        event["event_type"] in {"lineage.root-activated", "lineage.legacy-attached", "lineage.successor-activated"}
        for event in lineage_events(working, lineage_id)
    ):
        raise StoreCommandError("LEGACY_UNRESOLVED_LINEAGE", "safe legacy attachment would create ambiguous lineage ordering")

    member = make_member(
        lineage_id=lineage_id,
        semantic_effect_key=semantic_effect_key,
        external_dispatch_key=str(reservation["external_dispatch_key"]),
        operation_id=str(reservation.get("created_operation_id") or "legacy"),
        operation_generation=int(reservation.get("created_generation", 0)),
        expected_revision=int(reservation["expected_revision"]),
        stage=str(reservation["current_stage"]),
        task_identity=str(reservation["task_identity"]),
        role=str(reservation["role"]),
        candidate_head_sha=reservation.get("candidate_head_sha"),
        predecessor_semantic_effect_key=None,
        activated_from_proposal_id=None,
        activated_at=occurred_at,
        trusted_context_digest=trusted_context_digest,
    )
    working = add_immutable(snapshot, working, mutations, path=member_path(lineage_id, semantic_effect_key), value=member)
    working = append_lineage_event(
        snapshot,
        working,
        mutations,
        lineage_id=lineage_id,
        event_type="lineage.legacy-attached",
        occurred_at=occurred_at,
        payload={
            "semantic_effect_key": semantic_effect_key,
            "provenance_digest": evidence.provenance_digest,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"semantic_effect_key": semantic_effect_key, "provenance_digest": evidence.provenance_digest},
    )
    working = append_projection(snapshot, working, mutations, lineage_id=lineage_id)
    return StoreMutationPlan(
        snapshot.ref_sha,
        tuple(mutations),
        {"status": "ATTACHED", "effect_lineage_id": lineage_id, "semantic_effect_key": semantic_effect_key},
    )
