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
from operator_external_create_attempt import is_external_create_attempt_path
from operator_effect_lineage_integration import assert_lineage_member

ROLLOUT_SCHEMA = "ai-sdlc.effect-lineage-rollout/v1"
WRITER_FENCE_SCHEMA = "ai-sdlc.writer-fence-receipt/v1"
LINEAGE_WRITER_CAPABILITY = "lineage-aware-v1"
TRUSTED_WRITER_RESULT_FIELD = "_trusted_writer_capability"
REQUIRED_FENCED_CAPABILITIES = frozenset(
    {
        "raw-semantic-reservation",
        "raw-dispatch-claim",
        "raw-launch-authorization",
        "raw-external-create-attempt",
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
    """Verify protected/default-branch rollout policy plus an external writer-fence receipt."""

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
    """Guard every active runtime commit after lineage enforcement is verified."""

    def __init__(self, rollout: VerifiedEffectLineageRollout):
        self.rollout = rollout

    def __call__(self, snapshot: StoreSnapshot, plan: StoreMutationPlan) -> None:
        if not self.rollout.effect_lineage_required:
            return
        resulting = apply_plan_to_snapshot(snapshot, plan)
        reservation_prefix = "state/operator/v1/reservations/external/"
        claim_prefix = "state/operator/v1/claims/dispatch/"
        protected_writes: list[tuple[str, str, str]] = []

        for mutation in plan.mutations:
            value = mutation.value if isinstance(mutation.value, dict) else {}
            operation_id = None
            semantic_key = None
            kind = None
            if mutation.kind == "create_immutable" and is_external_create_attempt_path(mutation.path):
                operation_id = value.get("created_operation_id")
                semantic_key = value.get("semantic_effect_key")
                kind = "external-create-attempt"
            elif mutation.kind == "create_immutable" and mutation.path.startswith(reservation_prefix):
                operation_id = value.get("created_operation_id")
                semantic_key = value.get("semantic_effect_key")
                kind = "reservation"
            elif mutation.kind == "create_immutable" and mutation.path.startswith(claim_prefix):
                operation_id = value.get("operation_id")
                semantic_key = value.get("semantic_effect_key")
                kind = "claim"
            elif mutation.kind == "create_immutable" and value.get("event_type") == "dispatch.launch.authorized":
                operation_id = value.get("operation_id")
                semantic_key = (value.get("payload") or {}).get("semantic_effect_key")
                kind = "authorization"
            if not operation_id or not semantic_key or not kind:
                continue
            try:
                operation = rebuild_projection(snapshot, str(operation_id))
            except Exception as exc:
                raise StoreCommandError("MIXED_WRITER_FENCED", "external-effect write lacks durable Operation/profile binding") from exc
            if operation.get("operation_profile") != self.rollout.operation_profile:
                continue
            protected_writes.append((kind, str(operation_id), str(semantic_key)))

        if not protected_writes:
            return

        marker_ok = plan.result.get(TRUSTED_WRITER_RESULT_FIELD) == self.rollout.writer_capability
        paths = [mutation.path for mutation in plan.mutations if mutation.kind == "create_immutable"]
        atomic_lineage_activation = (
            all(kind == "reservation" for kind, _operation_id, _semantic_key in protected_writes)
            and any("/effect-lineages/members/" in path for path in paths)
            and (
                any("/effect-lineages/anchors/" in path for path in paths)
                or any("/effect-lineages/resolutions/" in path for path in paths)
            )
        )
        if not marker_ok and not atomic_lineage_activation:
            raise StoreCommandError(
                "MIXED_WRITER_FENCED",
                "raw vertical external-effect writer lacks the verified lineage-aware writer capability",
            )

        for _kind, _operation_id, semantic_key in protected_writes:
            try:
                assert_lineage_member(resulting, semantic_key)
            except Exception as exc:
                raise StoreCommandError(
                    "MIXED_WRITER_FENCED",
                    "vertical reservation/claim/launch/attempt write is not bound to the current Effect Lineage leaf",
                ) from exc
