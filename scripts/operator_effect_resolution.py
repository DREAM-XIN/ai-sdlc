#!/usr/bin/env python3
"""Trusted bounded Effect Resolution Authority for durable Effect Lineage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_store import StoreCommandError, _append_event
from operator_store_model import (
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    digest_json,
    projection_path,
    rebuild_projection,
    reservation_path,
)
from operator_vertical_store import vertical_projection
from operator_effect_lineage import add_immutable, append_lineage_event, append_projection
from operator_effect_lineage_integration import _add_reservation
from operator_effect_lineage_model import (
    LINEAGE_RESOLUTION_SCHEMA,
    LineageInvariantError,
    lineage_members,
    lineage_proposals,
    make_member,
    member_path,
    rebuild_lineage_projection,
    resolution_path,
)

ALLOWED_RESOLUTION_CHOICES = frozenset(
    {
        "CORRELATE_EXISTING_RECEIPT",
        "PROVE_NOT_LAUNCHED",
        "RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN",
        "REMAIN_BLOCKED",
    }
)
ALLOWED_EVIDENCE_TYPES = frozenset(
    {
        "EXTERNAL_LAUNCH_RECEIPT",
        "EXTERNAL_NOT_LAUNCHED",
        "EXTERNAL_KEY_INVALIDATED",
        "NON_OVERLAPPING_SCOPE",
        "INSUFFICIENT",
    }
)


@dataclass(frozen=True)
class EffectResolutionAuthority:
    authority_id: str
    allowed_choices: frozenset[str]
    allowed_resolvers: frozenset[str]
    trusted_policy_ref: str
    trusted_policy_digest: str

    def __post_init__(self):
        if not self.authority_id or not self.trusted_policy_ref or not self.trusted_policy_digest:
            raise ValueError("Effect Resolution Authority is incomplete")
        if not self.allowed_choices or not self.allowed_choices.issubset(ALLOWED_RESOLUTION_CHOICES):
            raise ValueError("Effect Resolution Authority expands frozen choices")
        if not self.allowed_resolvers:
            raise ValueError("Effect Resolution Authority requires trusted resolver identities")


class TrustedEffectEvidenceVerifier:
    def verify(self, evidence: list[dict[str, Any]], *, predecessor_external_dispatch_key: str) -> tuple[dict[str, Any], ...]:
        verified: list[dict[str, Any]] = []
        for item in evidence:
            if not isinstance(item, dict) or item.get("type") not in ALLOWED_EVIDENCE_TYPES:
                raise StoreCommandError("INVALID_EVIDENCE", "unsupported Effect Resolution evidence")
            row = dict(item)
            kind = str(row["type"])
            if kind in {"EXTERNAL_LAUNCH_RECEIPT", "EXTERNAL_NOT_LAUNCHED", "EXTERNAL_KEY_INVALIDATED"}:
                if row.get("external_dispatch_key") != predecessor_external_dispatch_key:
                    raise StoreCommandError("STALE_RESOLUTION", "evidence external key does not match predecessor")
            if kind == "EXTERNAL_LAUNCH_RECEIPT" and not row.get("receipt_id"):
                raise StoreCommandError("INVALID_EVIDENCE", "launch receipt evidence lacks receipt_id")
            if kind == "EXTERNAL_NOT_LAUNCHED" and (not row.get("observed_at") or not row.get("source_digest")):
                raise StoreCommandError("INVALID_EVIDENCE", "non-launch evidence is incomplete")
            if kind == "EXTERNAL_KEY_INVALIDATED" and not row.get("fence_receipt"):
                raise StoreCommandError("INVALID_EVIDENCE", "external invalidation evidence lacks fence receipt")
            if kind == "NON_OVERLAPPING_SCOPE" and not row.get("proof_digest"):
                raise StoreCommandError("INVALID_EVIDENCE", "non-overlap evidence lacks proof digest")
            row["evidence_digest"] = digest_json(item)
            verified.append(row)
        return tuple(verified)


def resolution_identity(material: dict[str, Any]) -> str:
    return "res-" + digest_json(material)[:48]


def _has_evidence(verified: tuple[dict[str, Any], ...], kind: str) -> bool:
    return any(row.get("type") == kind for row in verified)


def _append_operation_projection(original: StoreSnapshot, working: StoreSnapshot, mutations: list[StoreMutation], operation_id: str) -> StoreSnapshot:
    projection = rebuild_projection(working, operation_id)
    mutation = StoreMutation("replace_projection", projection_path(operation_id), projection)
    mutations.append(mutation)
    return apply_plan_to_snapshot(working, StoreMutationPlan(original.ref_sha, (mutation,), {}))


def plan_effect_resolution(
    snapshot: StoreSnapshot,
    *,
    authority: EffectResolutionAuthority,
    resolution_id: str,
    target_repository: str,
    feature_id: str,
    effect_lineage_id: str,
    predecessor_semantic_effect_key: str,
    predecessor_external_dispatch_key: str,
    current_operation_id: str,
    current_operation_generation: int,
    current_feature_revision: int,
    current_target_ref: str,
    current_candidate_head_sha: str | None,
    successor_proposal_id: str | None,
    successor_proposed_semantic_effect_key: str | None,
    choice: str,
    resolver_identity: str,
    evidence: list[dict[str, Any]],
    occurred_at: str,
    trusted_context_digest: str,
    evidence_verifier: TrustedEffectEvidenceVerifier | None = None,
) -> StoreMutationPlan:
    if choice not in ALLOWED_RESOLUTION_CHOICES or choice not in authority.allowed_choices:
        raise StoreCommandError("POLICY_RESTRICTED", "resolution choice is not allowed by frozen trusted authority")
    if resolver_identity not in authority.allowed_resolvers:
        raise StoreCommandError("POLICY_RESTRICTED", "resolver identity is not trusted for Effect Resolution")

    operation = vertical_projection(snapshot, current_operation_id)
    if operation["generation"] != current_operation_generation:
        raise StoreCommandError("STALE_RESOLUTION", "Operation generation changed")
    if operation["expected_feature_revision"] != current_feature_revision:
        raise StoreCommandError("STALE_RESOLUTION", "Feature revision changed")
    if effect_lineage_id not in set(operation.get("lineage_blocks", [])) and choice != "REMAIN_BLOCKED":
        raise StoreCommandError("STALE_RESOLUTION", "Operation no longer carries the targeted lineage block")

    lineage = rebuild_lineage_projection(snapshot, effect_lineage_id)
    if lineage.get("current_leaf_semantic_effect_key") != predecessor_semantic_effect_key:
        raise StoreCommandError("STALE_RESOLUTION", "predecessor is no longer the current lineage leaf")
    members = lineage_members(snapshot, effect_lineage_id)
    predecessor = members.get(predecessor_semantic_effect_key)
    if not predecessor or predecessor.get("external_dispatch_key") != predecessor_external_dispatch_key:
        raise StoreCommandError("STALE_RESOLUTION", "predecessor external identity changed")
    reservation = snapshot.get(reservation_path(predecessor_semantic_effect_key))
    if not isinstance(reservation, dict) or reservation.get("external_dispatch_key") != predecessor_external_dispatch_key:
        raise LineageInvariantError("predecessor reservation/member binding is corrupt")
    if reservation.get("feature_id") != feature_id or reservation.get("target_repository") != target_repository.lower():
        raise StoreCommandError("STALE_RESOLUTION", "resolution target repository/Feature binding changed")

    proposals = lineage_proposals(snapshot, effect_lineage_id)
    proposal = None
    if successor_proposal_id is not None:
        proposal = proposals.get(successor_proposal_id)
        if not proposal:
            raise StoreCommandError("STALE_RESOLUTION", "successor proposal no longer exists")
        if lineage.get("current_proposal_id") != successor_proposal_id:
            raise StoreCommandError("STALE_RESOLUTION", "successor proposal is stale")
        if proposal.get("predecessor_semantic_effect_key") != predecessor_semantic_effect_key:
            raise StoreCommandError("STALE_RESOLUTION", "proposal predecessor binding changed")
        if proposal.get("proposed_semantic_effect_key") != successor_proposed_semantic_effect_key:
            raise StoreCommandError("STALE_RESOLUTION", "proposed semantic effect binding changed")
        if proposal.get("current_feature_revision") != current_feature_revision:
            raise StoreCommandError("STALE_RESOLUTION", "proposal revision binding changed")
        if proposal.get("current_target_ref") != current_target_ref:
            raise StoreCommandError("STALE_RESOLUTION", "proposal target ref binding changed")
        if proposal.get("current_candidate_head_sha") != current_candidate_head_sha:
            raise StoreCommandError("STALE_RESOLUTION", "proposal candidate binding changed")
        if proposal.get("operation_id") != current_operation_id or proposal.get("operation_generation") != current_operation_generation:
            raise StoreCommandError("STALE_RESOLUTION", "proposal Operation binding changed")
    elif successor_proposed_semantic_effect_key is not None:
        raise StoreCommandError("INVALID_REQUEST", "successor effect key requires proposal id")

    verifier = evidence_verifier or TrustedEffectEvidenceVerifier()
    verified = verifier.verify(evidence, predecessor_external_dispatch_key=predecessor_external_dispatch_key)
    evidence_digests = [row["evidence_digest"] for row in verified]
    identity_material = {
        "target_repository": target_repository.lower(),
        "feature_id": feature_id,
        "effect_lineage_id": effect_lineage_id,
        "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
        "predecessor_external_dispatch_key": predecessor_external_dispatch_key,
        "current_operation_id": current_operation_id,
        "current_operation_generation": current_operation_generation,
        "current_feature_revision": current_feature_revision,
        "current_target_ref": current_target_ref,
        "current_candidate_head_sha": current_candidate_head_sha,
        "successor_proposal_id": successor_proposal_id,
        "successor_proposed_semantic_effect_key": successor_proposed_semantic_effect_key,
        "choice": choice,
        "trusted_policy_ref": authority.trusted_policy_ref,
        "trusted_policy_digest": authority.trusted_policy_digest,
        "resolver_identity": resolver_identity,
        "evidence_digests": evidence_digests,
    }
    if resolution_id != resolution_identity(identity_material):
        raise StoreCommandError("STALE_RESOLUTION", "resolution id does not bind exact current state/policy/evidence")

    predecessor_state = str(lineage.get("predecessor_state"))
    if choice == "PROVE_NOT_LAUNCHED":
        if predecessor_state != "NEVER_AUTHORIZED":
            raise StoreCommandError("AUTHORIZED_EFFECT_STILL_EXECUTABLE", "durable launch authorization prevents PROVE_NOT_LAUNCHED retirement")
        if not _has_evidence(verified, "EXTERNAL_NOT_LAUNCHED"):
            raise StoreCommandError("INSUFFICIENT_EVIDENCE", "trusted external non-launch evidence is required")
    elif choice == "RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN":
        if not (_has_evidence(verified, "EXTERNAL_KEY_INVALIDATED") or _has_evidence(verified, "NON_OVERLAPPING_SCOPE")):
            raise StoreCommandError("INSUFFICIENT_EVIDENCE", "stronger no-duplicate proof is required")
    elif choice == "CORRELATE_EXISTING_RECEIPT":
        if not _has_evidence(verified, "EXTERNAL_LAUNCH_RECEIPT"):
            raise StoreCommandError("INSUFFICIENT_EVIDENCE", "exact external launch receipt is required")

    record = {
        "schema_version": LINEAGE_RESOLUTION_SCHEMA,
        "resolution_id": resolution_id,
        **identity_material,
        "authority_id": authority.authority_id,
        "evidence": list(verified),
        "resolved_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
    }
    mutations: list[StoreMutation] = []
    working = add_immutable(
        snapshot,
        snapshot,
        mutations,
        path=resolution_path(effect_lineage_id, resolution_id),
        value=record,
    )
    working = append_lineage_event(
        snapshot,
        working,
        mutations,
        lineage_id=effect_lineage_id,
        event_type="lineage.resolution-applied",
        occurred_at=occurred_at,
        payload={
            "resolution_id": resolution_id,
            "choice": choice,
            "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
            "proposal_id": successor_proposal_id,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"resolution_id": resolution_id},
    )

    if choice == "REMAIN_BLOCKED":
        working = append_projection(snapshot, working, mutations, lineage_id=effect_lineage_id)
        return StoreMutationPlan(snapshot.ref_sha, tuple(mutations), {"status": "BLOCKED", "resolution_id": resolution_id})

    if choice == "CORRELATE_EXISTING_RECEIPT":
        receipt = next(row for row in verified if row.get("type") == "EXTERNAL_LAUNCH_RECEIPT")
        working = append_lineage_event(
            snapshot,
            working,
            mutations,
            lineage_id=effect_lineage_id,
            event_type="lineage.predecessor-correlated",
            occurred_at=occurred_at,
            payload={
                "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
                "external_dispatch_key": predecessor_external_dispatch_key,
                "receipt_id": receipt["receipt_id"],
                "resolution_id": resolution_id,
            },
            trusted_context_digest=trusted_context_digest,
            identity_material={"resolution_id": resolution_id, "receipt_id": receipt["receipt_id"]},
        )
        working = append_lineage_event(
            snapshot,
            working,
            mutations,
            lineage_id=effect_lineage_id,
            event_type="lineage.member-adopted",
            occurred_at=occurred_at,
            payload={
                "semantic_effect_key": predecessor_semantic_effect_key,
                "receipt_id": receipt["receipt_id"],
                "resolution_id": resolution_id,
            },
            trusted_context_digest=trusted_context_digest,
            identity_material={"resolution_id": resolution_id, "semantic_effect_key": predecessor_semantic_effect_key},
        )
        working, op_event = _append_event(
            working,
            operation_id=current_operation_id,
            generation=current_operation_generation,
            event_type="dispatch.launch.lookup-recorded",
            occurred_at=occurred_at,
            payload={
                "external_dispatch_key": predecessor_external_dispatch_key,
                "lookup_state": "LAUNCHED",
                "receipt_id": receipt["receipt_id"],
            },
            trusted_context_digest=trusted_context_digest,
            identity_material={"resolution_id": resolution_id, "receipt_id": receipt["receipt_id"]},
        )
        mutations.append(op_event)
        working = append_projection(snapshot, working, mutations, lineage_id=effect_lineage_id)
        working = _append_operation_projection(snapshot, working, mutations, current_operation_id)
        return StoreMutationPlan(snapshot.ref_sha, tuple(mutations), {"status": "CORRELATED", "resolution_id": resolution_id})

    # The two retirement choices are the only choices that may make a different exact successor eligible.
    working = append_lineage_event(
        snapshot,
        working,
        mutations,
        lineage_id=effect_lineage_id,
        event_type="lineage.predecessor-retired",
        occurred_at=occurred_at,
        payload={
            "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
            "resolution_id": resolution_id,
            "choice": choice,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"resolution_id": resolution_id, "predecessor_semantic_effect_key": predecessor_semantic_effect_key},
    )

    activated_key = None
    external_key = None
    if proposal is not None:
        working, successor_reservation = _add_reservation(
            snapshot,
            working,
            mutations,
            operation_id=current_operation_id,
            generation=current_operation_generation,
            exact_material=dict(proposal["proposed_exact_semantic_material"]),
            occurred_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
        )
        activated_key = str(proposal["proposed_semantic_effect_key"])
        external_key = str(successor_reservation["external_dispatch_key"])
        member = make_member(
            lineage_id=effect_lineage_id,
            semantic_effect_key=activated_key,
            external_dispatch_key=external_key,
            operation_id=current_operation_id,
            operation_generation=current_operation_generation,
            expected_revision=current_feature_revision,
            stage=str(proposal["current_stage"]),
            task_identity=str(proposal["proposed_exact_semantic_material"]["task_identity"]),
            role=str(proposal["proposed_exact_semantic_material"]["role"]),
            candidate_head_sha=current_candidate_head_sha,
            predecessor_semantic_effect_key=predecessor_semantic_effect_key,
            activated_from_proposal_id=successor_proposal_id,
            activated_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
        )
        working = add_immutable(snapshot, working, mutations, path=member_path(effect_lineage_id, activated_key), value=member)
        working = append_lineage_event(
            snapshot,
            working,
            mutations,
            lineage_id=effect_lineage_id,
            event_type="lineage.successor-activated",
            occurred_at=occurred_at,
            payload={
                "proposal_id": successor_proposal_id,
                "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
                "semantic_effect_key": activated_key,
            },
            trusted_context_digest=trusted_context_digest,
            identity_material={"resolution_id": resolution_id, "proposal_id": successor_proposal_id},
        )

    working, op_event = _append_event(
        working,
        operation_id=current_operation_id,
        generation=current_operation_generation,
        event_type="effect.lineage.resolved",
        occurred_at=occurred_at,
        payload={
            "effect_lineage_id": effect_lineage_id,
            "resolution_id": resolution_id,
            "predecessor_external_dispatch_key": predecessor_external_dispatch_key,
            "activated_semantic_effect_key": activated_key,
        },
        trusted_context_digest=trusted_context_digest,
        identity_material={"resolution_id": resolution_id, "effect_lineage_id": effect_lineage_id},
    )
    mutations.append(op_event)
    working = append_projection(snapshot, working, mutations, lineage_id=effect_lineage_id)
    working = _append_operation_projection(snapshot, working, mutations, current_operation_id)
    return StoreMutationPlan(
        snapshot.ref_sha,
        tuple(mutations),
        {
            "status": "RESOLVED",
            "resolution_id": resolution_id,
            "semantic_effect_key": activated_key,
            "external_dispatch_key": external_key,
        },
    )
