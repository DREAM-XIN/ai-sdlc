#!/usr/bin/env python3
"""Trusted Effect Lineage rollout proof and authoritative mixed-writer fence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from operator_store import StoreCommandError
from operator_store_model import (
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    digest_json,
    normalize_repository,
    rebuild_projection,
)
from operator_effect_lineage_integration import assert_lineage_member

ROLLOUT_SCHEMA = "ai-sdlc.effect-lineage-rollout/v1"
WRITER_FENCE_SCHEMA = "ai-sdlc.writer-fence-receipt/v1"
LINEAGE_WRITER_CAPABILITY = "lineage-aware-v1"
REQUIRED_FENCED_CAPABILITIES = frozenset(
    {
        "raw-semantic-reservation",
        "raw-dispatch-claim",
        "raw-launch-authorization",
    }
)


@dataclass(frozen=True)
class VerifiedEffectLineageRollout:
    repository: str
    state_ref: str
    operation_profile: str
    effect_lineage_required: bool
    policy_ref: str
    policy_digest: str
    writer_capability: str
    writer_fence_receipt_ref: str | None
    writer_fence_receipt_digest: str | None
    test_only: bool = False

    def validate_for(self, *, repository: str, state_ref: str, operation_profile: str) -> None:
        if normalize_repository(self.repository) != normalize_repository(repository):
            raise StoreCommandError("POLICY_DENIED", "Effect Lineage rollout repository binding mismatch")
        if self.state_ref != state_ref or self.operation_profile != operation_profile:
            raise StoreCommandError("POLICY_DENIED", "Effect Lineage rollout profile/state-ref binding mismatch")
        if self.effect_lineage_required:
            if self.writer_capability != LINEAGE_WRITER_CAPABILITY:
                raise StoreCommandError("MIXED_WRITER_FENCED", "lineage-required rollout lacks lineage-aware writer capability")
            if not self.writer_fence_receipt_ref or not self.writer_fence_receipt_digest:
                raise StoreCommandError("MIXED_WRITER_FENCED", "lineage-required rollout lacks verified old-writer fence receipt")


def _policy_material(policy: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in policy.items() if k != "policy_digest"}


class ProtectedEffectLineageRolloutVerifier:
    """Verify protected/default-branch rollout policy plus an external writer-fence receipt.

    The loader dependencies are trusted installation/control-plane dependencies. Canonical requests,
    Feature branches and Workers never supply either loader or their returned documents.
    """

    def __init__(
        self,
        *,
        policy_loader: Callable[[str, str, str], dict[str, Any]],
        writer_fence_receipt_loader: Callable[[str], dict[str, Any]],
    ):
        if not callable(policy_loader) or not callable(writer_fence_receipt_loader):
            raise ValueError("trusted rollout policy and writer-fence receipt loaders are required")
        self._policy_loader = policy_loader
        self._fence_loader = writer_fence_receipt_loader

    def verify(self, *, repository: str, state_ref: str, operation_profile: str) -> VerifiedEffectLineageRollout:
        repo = normalize_repository(repository)
        policy = self._policy_loader(repo, state_ref, operation_profile)
        if not isinstance(policy, dict) or policy.get("schema_version") != ROLLOUT_SCHEMA:
            raise StoreCommandError("POLICY_DENIED", "invalid protected Effect Lineage rollout policy")
        if normalize_repository(str(policy.get("repository", ""))) != repo:
            raise StoreCommandError("POLICY_DENIED", "rollout policy repository binding mismatch")
        if policy.get("state_ref") != state_ref or policy.get("operation_profile") != operation_profile:
            raise StoreCommandError("POLICY_DENIED", "rollout policy state-ref/profile binding mismatch")
        policy_ref = str(policy.get("policy_ref") or "")
        if not policy_ref.startswith(("protected://", "default-branch://")):
            raise StoreCommandError("POLICY_DENIED", "rollout policy is not sourced from protected installation/default-branch state")
        expected_digest = digest_json(_policy_material(policy))
        if policy.get("policy_digest") != expected_digest:
            raise StoreCommandError("POLICY_DENIED", "rollout policy digest mismatch")

        required = policy.get("effect_lineage_required")
        if not isinstance(required, bool):
            raise StoreCommandError("POLICY_DENIED", "rollout policy must explicitly select Effect Lineage enforcement")
        writer_capability = str(policy.get("writer_capability") or "")
        fence_ref = policy.get("writer_fence_receipt_ref")
        fence_digest = None
        if required:
            if writer_capability != LINEAGE_WRITER_CAPABILITY or not fence_ref:
                raise StoreCommandError("MIXED_WRITER_FENCED", "Effect Lineage enforcement requires a lineage-aware writer and fence receipt")
            receipt = self._fence_loader(str(fence_ref))
            if not isinstance(receipt, dict) or receipt.get("schema_version") != WRITER_FENCE_SCHEMA:
                raise StoreCommandError("MIXED_WRITER_FENCED", "writer-fence receipt is invalid")
            if normalize_repository(str(receipt.get("repository", ""))) != repo:
                raise StoreCommandError("MIXED_WRITER_FENCED", "writer-fence receipt repository binding mismatch")
            if receipt.get("state_ref") != state_ref or receipt.get("operation_profile") != operation_profile:
                raise StoreCommandError("MIXED_WRITER_FENCED", "writer-fence receipt state-ref/profile binding mismatch")
            if receipt.get("state") != "QUIESCED":
                raise StoreCommandError("MIXED_WRITER_FENCED", "old production writers are not proven quiesced")
            fenced = frozenset(str(v) for v in receipt.get("fenced_capabilities", []))
            if not REQUIRED_FENCED_CAPABILITIES.issubset(fenced):
                raise StoreCommandError("MIXED_WRITER_FENCED", "writer-fence receipt does not cover every raw external-effect write capability")
            if not receipt.get("receipt_id") or not receipt.get("issued_at") or not receipt.get("issuer"):
                raise StoreCommandError("MIXED_WRITER_FENCED", "writer-fence receipt lacks audit identity")
            fence_digest = digest_json(receipt)

        verified = VerifiedEffectLineageRollout(
            repository=repo,
            state_ref=state_ref,
            operation_profile=operation_profile,
            effect_lineage_required=required,
            policy_ref=policy_ref,
            policy_digest=expected_digest,
            writer_capability=writer_capability,
            writer_fence_receipt_ref=str(fence_ref) if fence_ref else None,
            writer_fence_receipt_digest=fence_digest,
        )
        verified.validate_for(repository=repo, state_ref=state_ref, operation_profile=operation_profile)
        return verified


def legacy_compatibility_rollout_for_tests(*, repository: str, state_ref: str, operation_profile: str) -> VerifiedEffectLineageRollout:
    """Explicit test-only compatibility mode for pre-lineage recovery fixtures."""
    return VerifiedEffectLineageRollout(
        repository=normalize_repository(repository),
        state_ref=state_ref,
        operation_profile=operation_profile,
        effect_lineage_required=False,
        policy_ref="test-only://legacy-compatibility",
        policy_digest=digest_json({"test_only": True, "effect_lineage_required": False}),
        writer_capability="legacy-test-only",
        writer_fence_receipt_ref=None,
        writer_fence_receipt_digest=None,
        test_only=True,
    )


class EffectLineageWriteFence:
    """Guard every active runtime commit after lineage enforcement is verified.

    This is defense in depth behind the externally verified old-writer quiescence receipt. It
    prevents any retained raw planner inside the active runtime from creating a vertical-profile
    reservation, claim or launch authorization without current lineage membership.
    """

    def __init__(self, rollout: VerifiedEffectLineageRollout):
        self.rollout = rollout

    def __call__(self, snapshot: StoreSnapshot, plan: StoreMutationPlan) -> None:
        if not self.rollout.effect_lineage_required:
            return
        resulting = apply_plan_to_snapshot(snapshot, plan)
        reservation_prefix = "state/operator/v1/reservations/external/"
        claim_prefix = "state/operator/v1/claims/dispatch/"

        for mutation in plan.mutations:
            value = mutation.value if isinstance(mutation.value, dict) else {}
            operation_id = None
            semantic_key = None
            if mutation.kind == "create_immutable" and mutation.path.startswith(reservation_prefix):
                operation_id = value.get("created_operation_id")
                semantic_key = value.get("semantic_effect_key")
            elif mutation.kind == "create_immutable" and mutation.path.startswith(claim_prefix):
                operation_id = value.get("operation_id")
                semantic_key = value.get("semantic_effect_key")
            elif mutation.kind == "create_immutable" and value.get("event_type") == "dispatch.launch.authorized":
                operation_id = value.get("operation_id")
                semantic_key = (value.get("payload") or {}).get("semantic_effect_key")
            if not operation_id or not semantic_key:
                continue
            try:
                operation = rebuild_projection(snapshot, str(operation_id))
            except Exception as exc:
                raise StoreCommandError("MIXED_WRITER_FENCED", "external-effect write lacks durable Operation/profile binding") from exc
            if operation.get("operation_profile") != self.rollout.operation_profile:
                continue
            try:
                assert_lineage_member(resulting, str(semantic_key))
            except Exception as exc:
                raise StoreCommandError(
                    "MIXED_WRITER_FENCED",
                    "raw vertical reservation/claim/launch authorization is fenced after Effect Lineage enforcement",
                ) from exc
