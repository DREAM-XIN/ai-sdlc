#!/usr/bin/env python3
"""Fail-closed trusted reconstruction for legacy exact semantic reservations."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from operator_store import StoreCommandError
from operator_store_model import (
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    canonical_json,
    digest_json,
    normalize_repository,
    rebuild_projection,
    reservation_path,
)
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE
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

_HEX40 = r"[0-9a-f]{40}"


@dataclass(frozen=True)
class LegacyLineageReconstruction:
    logical_work_slot: str
    task_id: str | None
    operation_profile: str
    causal_work_id: str
    external_effect_scope: str
    provenance_digest: str


class LegacyReconstructionError(ValueError):
    pass


def validate_lineage_rollout(*, old_writers_quiesced: bool, effect_lineage_required: bool) -> None:
    """Legacy constructor check only; production authority is EffectLineageWriteFence + verified rollout."""
    if effect_lineage_required and not old_writers_quiesced:
        raise StoreCommandError(
            "MIXED_WRITER_FORBIDDEN",
            "effect_lineage_required cannot become authoritative before old reservation writers are fenced",
        )


def _single_task(manifest: dict[str, Any], task_id: str, *, require_done: bool = False) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in manifest.get("tasks", [])
        if row.get("id") == task_id and row.get("kind") == "remediation" and row.get("source_stage") == "code-review"
    ]
    if len(rows) != 1:
        raise LegacyReconstructionError("legacy remediation task identity is not uniquely present in authoritative Feature history")
    if require_done and rows[0].get("status") != "DONE":
        raise LegacyReconstructionError("legacy re-review predecessor is not a completed authoritative remediation task")
    return rows[0]


def _infer_vertical_slot(reservation: dict[str, Any], manifest: dict[str, Any]) -> tuple[str, str | None]:
    role = str(reservation.get("role") or "")
    stage = str(reservation.get("current_stage") or "")
    identity = str(reservation.get("task_identity") or "")
    candidate = reservation.get("candidate_head_sha")
    revision = int(reservation.get("expected_revision", -1))

    if role == "developer" and stage == "implementation" and identity == f"vertical:implementation:{revision}":
        return "IMPLEMENTATION_WORK", None

    if role == "developer" and stage == "code-review":
        match = re.fullmatch(rf"vertical:code-remediation:([^:]+):({_HEX40})", identity)
        if match and candidate == match.group(2):
            task_id = match.group(1)
            _single_task(manifest, task_id)
            return "CODE_REMEDIATION", task_id

    if role == "reviewer" and stage == "code-review":
        match = re.fullmatch(rf"vertical:code-review:({_HEX40})", identity)
        if match and candidate == match.group(1):
            return "CODE_REVIEW", identity
        match = re.fullmatch(rf"vertical:code-rereview:([^:]+):({_HEX40})", identity)
        if match and candidate == match.group(2):
            task_id = match.group(1)
            _single_task(manifest, task_id, require_done=True)
            return "CODE_REREVIEW", task_id

    if role == "qa" and stage == "verification":
        match = re.fullmatch(rf"vertical:verification:({_HEX40})", identity)
        if match and candidate == match.group(1):
            return "VERIFICATION_QA", identity

    raise LegacyReconstructionError("legacy reservation does not map uniquely to reviewed vertical causal-work semantics")


def reconstruct_legacy_lineage(
    snapshot: StoreSnapshot,
    *,
    semantic_effect_key: str,
    trusted_feature: FeatureSnapshot,
    trusted_manifest: dict[str, Any],
    resolver: CausalWorkResolver | None = None,
) -> LegacyLineageReconstruction:
    reservation = snapshot.get(reservation_path(semantic_effect_key))
    if not isinstance(reservation, dict):
        raise LegacyReconstructionError("legacy semantic reservation does not exist")
    if not isinstance(trusted_feature, FeatureSnapshot) or not isinstance(trusted_manifest, dict):
        raise LegacyReconstructionError("fresh trusted Feature truth is required for legacy reconstruction")
    if digest_json(trusted_manifest) != trusted_feature.manifest_digest:
        raise LegacyReconstructionError("trusted Feature manifest digest does not match supplied Feature snapshot")
    if normalize_repository(trusted_feature.repository) != str(reservation.get("target_repository", "")).lower():
        raise LegacyReconstructionError("legacy reservation repository does not match trusted Feature truth")
    if trusted_feature.feature_id != reservation.get("feature_id"):
        raise LegacyReconstructionError("legacy reservation Feature identity does not match trusted Feature truth")
    if str((trusted_manifest.get("feature") or {}).get("id") or "") != trusted_feature.feature_id:
        raise LegacyReconstructionError("trusted Feature manifest identity is inconsistent")

    operation_id = str(reservation.get("created_operation_id") or "")
    if not operation_id:
        raise LegacyReconstructionError("legacy reservation lacks durable creating Operation identity")
    try:
        operation = rebuild_projection(snapshot, operation_id)
    except Exception as exc:
        raise LegacyReconstructionError("legacy reservation creating Operation history is unavailable") from exc
    if operation.get("operation_profile") != VERTICAL_PROFILE:
        raise LegacyReconstructionError("legacy reservation is not bound to the reviewed vertical Operation profile")
    if operation.get("target_repository") != str(reservation.get("target_repository", "")).lower():
        raise LegacyReconstructionError("legacy reservation/Operation repository binding is inconsistent")
    if operation.get("feature_id") != reservation.get("feature_id"):
        raise LegacyReconstructionError("legacy reservation/Operation Feature binding is inconsistent")
    if operation.get("expected_feature_revision") != reservation.get("expected_revision"):
        raise LegacyReconstructionError("legacy reservation/Operation revision binding is inconsistent")

    logical_work_slot, task_id = _infer_vertical_slot(reservation, trusted_manifest)
    causal = (resolver or CausalWorkResolver()).resolve(
        feature_id=trusted_feature.feature_id,
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role=str(reservation["role"]),
        logical_work_slot=logical_work_slot,
        task_id=task_id,
    )
    provenance = {
        "semantic_effect_key": semantic_effect_key,
        "reservation": reservation,
        "operation": {
            "operation_id": operation_id,
            "operation_profile": operation.get("operation_profile"),
            "target_repository": operation.get("target_repository"),
            "feature_id": operation.get("feature_id"),
            "expected_feature_revision": operation.get("expected_feature_revision"),
        },
        "trusted_feature_manifest_digest": trusted_feature.manifest_digest,
        "logical_work_slot": logical_work_slot,
        "task_id": task_id,
        "causal_work_id": causal.causal_work_id,
        "external_effect_scope": causal.external_effect_scope,
    }
    return LegacyLineageReconstruction(
        logical_work_slot=logical_work_slot,
        task_id=task_id,
        operation_profile=VERTICAL_PROFILE,
        causal_work_id=causal.causal_work_id,
        external_effect_scope=causal.external_effect_scope,
        provenance_digest=digest_json(provenance),
    )


def plan_legacy_lineage_attachment(
    snapshot: StoreSnapshot,
    *,
    semantic_effect_key: str,
    trusted_feature: FeatureSnapshot,
    trusted_manifest: dict[str, Any],
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

    try:
        reconstruction = reconstruct_legacy_lineage(
            snapshot,
            semantic_effect_key=semantic_effect_key,
            trusted_feature=trusted_feature,
            trusted_manifest=trusted_manifest,
            resolver=resolver,
        )
    except LegacyReconstructionError as exc:
        return StoreMutationPlan(
            snapshot.ref_sha,
            tuple(),
            {
                "status": "BLOCKED",
                "reason": "LEGACY_UNRESOLVED_LINEAGE",
                "detail": str(exc)[:256],
            },
        )

    target_repository = normalize_repository(trusted_feature.repository)
    feature_id = trusted_feature.feature_id
    role = str(reservation["role"])
    material = lineage_key_material(
        target_repository=target_repository,
        feature_id=feature_id,
        operation_profile=reconstruction.operation_profile,
        effect_kind="worker-dispatch",
        role=role,
        causal_work_id=reconstruction.causal_work_id,
        external_effect_scope=reconstruction.external_effect_scope,
    )
    lineage_id = effect_lineage_id(
        target_repository=target_repository,
        feature_id=feature_id,
        operation_profile=reconstruction.operation_profile,
        effect_kind="worker-dispatch",
        role=role,
        causal_work_id=reconstruction.causal_work_id,
        external_effect_scope=reconstruction.external_effect_scope,
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
    else:
        expected = {k: material[k] for k in material if k != "schema"}
        actual = {k: anchor.get(k) for k in expected}
        if canonical_json(actual) != canonical_json(expected):
            raise LineageInvariantError("legacy target lineage anchor conflicts with reconstructed causal material")

    if lineage_members(working, lineage_id) or any(
        event["event_type"] in {"lineage.root-activated", "lineage.legacy-attached", "lineage.successor-activated"}
        for event in lineage_events(working, lineage_id)
    ):
        return StoreMutationPlan(
            snapshot.ref_sha,
            tuple(),
            {
                "status": "BLOCKED",
                "reason": "LEGACY_UNRESOLVED_LINEAGE",
                "detail": "reconstructed lineage already has competing ordered work",
            },
        )

    member = make_member(
        lineage_id=lineage_id,
        semantic_effect_key=semantic_effect_key,
        external_dispatch_key=str(reservation["external_dispatch_key"]),
        operation_id=str(reservation["created_operation_id"]),
        operation_generation=int(reservation.get("created_generation", 0)),
        expected_revision=int(reservation["expected_revision"]),
        stage=str(reservation["current_stage"]),
        task_identity=str(reservation["task_identity"]),
        role=role,
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
            "provenance_digest": reconstruction.provenance_digest,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={
            "semantic_effect_key": semantic_effect_key,
            "provenance_digest": reconstruction.provenance_digest,
        },
    )
    working = append_projection(snapshot, working, mutations, lineage_id=lineage_id)
    return StoreMutationPlan(
        snapshot.ref_sha,
        tuple(mutations),
        {
            "status": "ATTACHED",
            "effect_lineage_id": lineage_id,
            "semantic_effect_key": semantic_effect_key,
            "provenance_digest": reconstruction.provenance_digest,
        },
    )
