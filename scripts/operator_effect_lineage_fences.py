#!/usr/bin/env python3
"""Lineage-aware wrappers around existing dispatch claim and launch authorization fences."""
from __future__ import annotations

from operator_store import StoreCommandError, plan_authorize_launch, plan_dispatch_claim
from operator_store_model import StoreMutationPlan, StoreSnapshot, dispatch_claim_path
from operator_effect_lineage_integration import assert_lineage_member

LINEAGE_WRITER_CAPABILITY = "lineage-aware-v1"
TRUSTED_WRITER_RESULT_FIELD = "_trusted_writer_capability"


def _mark(plan: StoreMutationPlan) -> StoreMutationPlan:
    result = dict(plan.result)
    result[TRUSTED_WRITER_RESULT_FIELD] = LINEAGE_WRITER_CAPABILITY
    return StoreMutationPlan(plan.expected_ref_sha, plan.mutations, result)


def plan_lineage_dispatch_claim(
    snapshot: StoreSnapshot,
    *,
    effect_lineage_id: str,
    operation_id: str,
    generation: int,
    effect_key: str,
    occurred_at: str,
    trusted_context_digest: str,
):
    assert_lineage_member(snapshot, effect_key, expected_lineage_id=effect_lineage_id)
    return _mark(
        plan_dispatch_claim(
            snapshot,
            operation_id=operation_id,
            generation=generation,
            effect_key=effect_key,
            occurred_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
        )
    )


def plan_lineage_authorize_launch(
    snapshot: StoreSnapshot,
    *,
    effect_lineage_id: str,
    operation_id: str,
    generation: int,
    claim_id: str,
    dispatch_id: str,
    occurred_at: str,
    trusted_context_digest: str,
    verified_expected_revision: int,
    verified_stage: str,
    verified_candidate_head_sha: str | None = None,
):
    claim = snapshot.get(dispatch_claim_path(claim_id))
    if not isinstance(claim, dict):
        raise StoreCommandError("INVALID_REQUEST", "dispatch claim does not exist")
    assert_lineage_member(
        snapshot,
        str(claim["semantic_effect_key"]),
        expected_lineage_id=effect_lineage_id,
    )
    return _mark(
        plan_authorize_launch(
            snapshot,
            operation_id=operation_id,
            generation=generation,
            claim_id=claim_id,
            dispatch_id=dispatch_id,
            occurred_at=occurred_at,
            trusted_context_digest=trusted_context_digest,
            verified_expected_revision=verified_expected_revision,
            verified_stage=verified_stage,
            verified_candidate_head_sha=verified_candidate_head_sha,
        )
    )
