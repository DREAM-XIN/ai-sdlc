#!/usr/bin/env python3
"""Trusted durable executor for the v0.3 vertical Operator loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from operator_store import (
    StoreCommandError,
    plan_authorize_launch,
    plan_dispatch_claim,
    plan_launch_lookup,
    plan_needs_user,
    plan_operation_fact,
)
from operator_store_model import digest_json, projection_public, rebuild_projection
from operator_vertical import (
    FeatureSnapshot,
    VERTICAL_PROFILE,
    VerticalInvariantError,
)
from operator_vertical_controller import VerticalAction, select_vertical_action
from operator_vertical_store import (
    plan_vertical_done,
    plan_vertical_persist_confirmed,
    plan_vertical_persist_linearized,
    plan_vertical_persist_requested,
    plan_vertical_semantic_reservation,
    vertical_projection,
)
from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_effect_lineage_fences import (
    plan_lineage_authorize_launch,
    plan_lineage_dispatch_claim,
)
from operator_effect_migration import validate_lineage_rollout


class VerticalFeatureGateway(Protocol):
    def read_feature(self, *, operation_id: str) -> tuple[FeatureSnapshot, dict[str, Any]]: ...


class FeaturePersistGateway(Protocol):
    def persist_feature_event(self, *, event: dict[str, Any], target_ref: str) -> dict[str, Any]: ...
    def lookup_feature_event(self, *, event_id: str, target_ref: str) -> dict[str, Any] | None: ...


class RoleDispatchGateway(Protocol):
    def launch(self, *, dispatch: dict[str, Any]) -> dict[str, Any]: ...
    def lookup(self, *, external_dispatch_key: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TrustedVerticalExecutorConfig:
    target_ref: str
    trusted_context_digest: str
    max_auto_steps: int = 16
    effect_lineage_required: bool = True
    old_writers_quiesced: bool = True

    def __post_init__(self):
        if not self.target_ref or not self.trusted_context_digest:
            raise ValueError("trusted vertical executor config is incomplete")
        if self.max_auto_steps < 1 or self.max_auto_steps > 64:
            raise ValueError("invalid vertical auto-step bound")
        validate_lineage_rollout(
            old_writers_quiesced=self.old_writers_quiesced,
            effect_lineage_required=self.effect_lineage_required,
        )


class TrustedVerticalExecutor:
    def __init__(
        self,
        *,
        runtime,
        feature_gateway: VerticalFeatureGateway,
        persist_gateway: FeaturePersistGateway,
        dispatch_gateway: RoleDispatchGateway,
        config: TrustedVerticalExecutorConfig,
    ):
        self.runtime = runtime
        self.feature_gateway = feature_gateway
        self.persist_gateway = persist_gateway
        self.dispatch_gateway = dispatch_gateway
        self.config = config

    def _projection(self, operation_id: str):
        return vertical_projection(self.runtime.backend.read_snapshot(), operation_id)

    def _public(self, operation_id: str):
        return projection_public(rebuild_projection(self.runtime.backend.read_snapshot(), operation_id))

    def _commit(self, planner):
        return self.runtime.commit_replanned(planner)

    def _assert_feature_fence(self, operation_id: str, feature: FeatureSnapshot, *, stage: str | None = None, candidate: str | None = None):
        projection = self._projection(operation_id)
        if projection.get("operation_profile") != VERTICAL_PROFILE:
            raise VerticalInvariantError("CAPABILITY_UNAVAILABLE", "Operation profile is not vertical-loop compatible")
        if projection["expected_feature_revision"] != feature.revision:
            raise VerticalInvariantError("STALE_REVISION", "Feature revision changed across vertical fence")
        if stage is not None and feature.current_stage != stage:
            raise VerticalInvariantError("STALE_REVISION", "Feature stage changed across vertical fence")
        if candidate is not None and feature.candidate_head_sha != candidate:
            raise VerticalInvariantError("STALE_REVISION", "candidate head changed across vertical fence")
        return projection

    def _record_fact(self, operation_id: str, event_type: str, payload: dict[str, Any]):
        projection = self._projection(operation_id)
        occurred_at = self.runtime.clock()
        return self._commit(
            lambda snapshot: plan_operation_fact(
                snapshot,
                operation_id=operation_id,
                generation=projection["generation"],
                event_type=event_type,
                payload=payload,
                occurred_at=occurred_at,
                trusted_context_digest=self.config.trusted_context_digest,
            )
        )

    def _stable_stop(self, operation_id: str, *, status: str, reason: str):
        projection = self._projection(operation_id)
        occurred_at = self.runtime.clock()
        if status == "NEEDS_USER":
            self._commit(
                lambda snapshot: plan_needs_user(
                    snapshot,
                    operation_id=operation_id,
                    generation=projection["generation"],
                    reason_code="VERTICAL_NEEDS_USER",
                    summary=reason,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            )
        else:
            self._commit(
                lambda snapshot: plan_operation_fact(
                    snapshot,
                    operation_id=operation_id,
                    generation=projection["generation"],
                    event_type="loop.stable-stop",
                    payload={"status": "BLOCKED", "reason": reason[:512]},
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            )
        return self._public(operation_id)

    def _persist(self, operation_id: str, event: dict[str, Any], feature: FeatureSnapshot):
        projection = self._assert_feature_fence(operation_id, feature)
        generation = projection["generation"]
        event_id = str(event["id"])
        occurred_at = self.runtime.clock()
        common = dict(
            operation_id=operation_id,
            generation=generation,
            feature_event_id=event_id,
            expected_revision=feature.revision,
            target_ref=self.config.target_ref,
            candidate_head_sha=feature.candidate_head_sha,
            occurred_at=occurred_at,
            trusted_context_digest=self.config.trusted_context_digest,
        )
        self._commit(lambda snapshot: plan_vertical_persist_requested(snapshot, **common))

        fresh, _ = self.feature_gateway.read_feature(operation_id=operation_id)
        self._assert_feature_fence(
            operation_id,
            fresh,
            stage=feature.current_stage,
            candidate=feature.candidate_head_sha,
        )
        if fresh.revision != feature.revision or fresh.manifest_digest != feature.manifest_digest:
            raise VerticalInvariantError("STALE_REVISION", "Feature truth changed before Persist linearization")
        self._commit(lambda snapshot: plan_vertical_persist_linearized(snapshot, **common))

        receipt = None
        try:
            receipt = self.persist_gateway.persist_feature_event(event=event, target_ref=self.config.target_ref)
        except Exception:
            receipt = self.persist_gateway.lookup_feature_event(event_id=event_id, target_ref=self.config.target_ref)
            if receipt is None:
                raise VerticalInvariantError("TRANSIENT_FAILURE", "Persist acknowledgement is unknown; exact Event was not found")
        if not isinstance(receipt, dict) or receipt.get("event_id") != event_id:
            raise VerticalInvariantError("INTERNAL_FAILURE", "Persist gateway returned an invalid Event receipt")
        result_revision = receipt.get("result_revision")
        if result_revision != feature.revision + 1:
            raise VerticalInvariantError("STALE_REVISION", "Persist result revision is not the exact next Feature revision")
        confirm = dict(common)
        confirm["result_revision"] = result_revision
        self._commit(lambda snapshot: plan_vertical_persist_confirmed(snapshot, **confirm))
        return self._public(operation_id)

    def _dispatch(self, operation_id: str, action: VerticalAction, feature: FeatureSnapshot):
        projection = self._assert_feature_fence(
            operation_id,
            feature,
            stage=feature.current_stage,
            candidate=action.candidate_head_sha,
        )
        generation = projection["generation"]
        occurred_at = self.runtime.clock()
        lineage_id = None
        if self.config.effect_lineage_required:
            reservation = self._commit(
                lambda snapshot: plan_lineage_gated_reservation(
                    snapshot,
                    operation_id=operation_id,
                    generation=generation,
                    target_repository=feature.repository,
                    feature_id=feature.feature_id,
                    expected_revision=feature.revision,
                    current_stage=feature.current_stage,
                    task_identity=str(action.task_identity),
                    role=str(action.role),
                    candidate_head_sha=action.candidate_head_sha,
                    current_target_ref=feature.target_ref,
                    operation_profile=VERTICAL_PROFILE,
                    effect_kind="worker-dispatch",
                    logical_work_slot=action.step,
                    task_id=action.task_id,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                    trusted_profile_digest=digest_json(
                        {"operation_profile": VERTICAL_PROFILE, "effect_lineage_required": True}
                    ),
                )
            ).result
            if reservation.get("status") == "BLOCKED":
                return self._public(operation_id)
            lineage_id = str(reservation["effect_lineage_id"])
        else:
            reservation = self._commit(
                lambda snapshot: plan_vertical_semantic_reservation(
                    snapshot,
                    operation_id=operation_id,
                    generation=generation,
                    target_repository=feature.repository,
                    feature_id=feature.feature_id,
                    expected_revision=feature.revision,
                    current_stage=feature.current_stage,
                    task_identity=str(action.task_identity),
                    role=str(action.role),
                    candidate_head_sha=action.candidate_head_sha,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            ).result

        effect_key = reservation["semantic_effect_key"]
        if self.config.effect_lineage_required:
            claim = self._commit(
                lambda snapshot: plan_lineage_dispatch_claim(
                    snapshot,
                    effect_lineage_id=str(lineage_id),
                    operation_id=operation_id,
                    generation=generation,
                    effect_key=effect_key,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            ).result
        else:
            claim = self._commit(
                lambda snapshot: plan_dispatch_claim(
                    snapshot,
                    operation_id=operation_id,
                    generation=generation,
                    effect_key=effect_key,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            ).result
        external_key = claim["external_dispatch_key"]
        dispatch_id = "vertical-" + digest_json(
            {"operation_id": operation_id, "generation": generation, "external_dispatch_key": external_key}
        )[:32]

        fresh, _ = self.feature_gateway.read_feature(operation_id=operation_id)
        self._assert_feature_fence(
            operation_id,
            fresh,
            stage=feature.current_stage,
            candidate=action.candidate_head_sha,
        )
        if self.config.effect_lineage_required:
            self._commit(
                lambda snapshot: plan_lineage_authorize_launch(
                    snapshot,
                    effect_lineage_id=str(lineage_id),
                    operation_id=operation_id,
                    generation=generation,
                    claim_id=claim["claim_id"],
                    dispatch_id=dispatch_id,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                    verified_expected_revision=feature.revision,
                    verified_stage=feature.current_stage,
                    verified_candidate_head_sha=action.candidate_head_sha,
                )
            )
        else:
            self._commit(
                lambda snapshot: plan_authorize_launch(
                    snapshot,
                    operation_id=operation_id,
                    generation=generation,
                    claim_id=claim["claim_id"],
                    dispatch_id=dispatch_id,
                    occurred_at=occurred_at,
                    trusted_context_digest=self.config.trusted_context_digest,
                    verified_expected_revision=feature.revision,
                    verified_stage=feature.current_stage,
                    verified_candidate_head_sha=action.candidate_head_sha,
                )
            )
        dispatch = {
            "operation_id": operation_id,
            "operation_generation": generation,
            "operation_profile": VERTICAL_PROFILE,
            "semantic_effect_key": effect_key,
            "external_dispatch_key": external_key,
            "dispatch_id": dispatch_id,
            "target_repository": feature.repository,
            "target_ref": feature.target_ref,
            "feature_id": feature.feature_id,
            "expected_revision": feature.revision,
            "feature_stage": feature.current_stage,
            "task_id": action.task_id,
            "task_identity": action.task_identity,
            "role": action.role,
            "candidate_pr_number": feature.candidate_pr_number,
            "candidate_head_sha": action.candidate_head_sha,
        }
        try:
            launch_receipt = self.dispatch_gateway.launch(dispatch=dispatch)
        except Exception:
            launch_receipt = self.dispatch_gateway.lookup(external_dispatch_key=external_key)
        if not isinstance(launch_receipt, dict):
            launch_receipt = {"lookup_state": "UNKNOWN", "receipt_id": None}
        lookup_state = str(launch_receipt.get("lookup_state", "UNKNOWN"))
        receipt_id = launch_receipt.get("receipt_id")
        if lookup_state not in {"NOT_LAUNCHED", "LAUNCHED", "UNKNOWN"}:
            lookup_state = "UNKNOWN"
        self._commit(
            lambda snapshot: plan_launch_lookup(
                snapshot,
                operation_id=operation_id,
                generation=generation,
                external_dispatch_key_value=external_key,
                lookup_state=lookup_state,
                receipt_id=receipt_id,
                occurred_at=self.runtime.clock(),
                trusted_context_digest=self.config.trusted_context_digest,
            )
        )
        return self._public(operation_id)

    def advance_action(self, *, operation_id: str, action: VerticalAction) -> dict[str, Any]:
        feature, _ = self.feature_gateway.read_feature(operation_id=operation_id)
        self._record_fact(
            operation_id,
            "loop.step.selected",
            {"step": action.step, "kind": action.kind, "feature_revision": feature.revision, "task_identity": action.task_identity},
        )
        if action.kind == "persist":
            if not action.feature_event:
                raise VerticalInvariantError("INTERNAL_FAILURE", "Persist action lacks bounded Feature Event")
            return self._persist(operation_id, action.feature_event, feature)
        if action.kind == "dispatch":
            return self._dispatch(operation_id, action, feature)
        if action.kind == "done":
            projection = self._assert_feature_fence(operation_id, feature)
            self._commit(
                lambda snapshot: plan_vertical_done(
                    snapshot,
                    operation_id=operation_id,
                    generation=projection["generation"],
                    feature_revision=feature.revision,
                    occurred_at=self.runtime.clock(),
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            )
            return self._public(operation_id)
        raise VerticalInvariantError("INTERNAL_FAILURE", "unsupported vertical action kind")

    def advance_until_stop(self, *, operation_id: str) -> dict[str, Any]:
        for _ in range(self.config.max_auto_steps):
            current = self._public(operation_id)
            if current["status"] in {"WAITING_EXTERNAL", "BLOCKED", "NEEDS_USER", "DONE", "CANCELLED"}:
                return current
            feature, manifest = self.feature_gateway.read_feature(operation_id=operation_id)
            try:
                action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=self.runtime.clock())
            except VerticalInvariantError as exc:
                if exc.code in {"BLOCKED", "NEEDS_USER"}:
                    return self._stable_stop(operation_id, status=exc.code, reason=str(exc))
                raise
            current = self.advance_action(operation_id=operation_id, action=action)
        return self._stable_stop(operation_id, status="BLOCKED", reason="vertical auto-step bound exceeded")

    def handle_worker_callback(self, **_kwargs) -> dict[str, Any]:
        """Non-authoritative compatibility trap.

        Production callback handling is intentionally available only through
        TrustedVerticalCallbackCoordinator, which validates the durable launch/reservation
        binding, reconstructs role independence from durable history, and always reloads
        collector bytes before translation.
        """
        raise VerticalInvariantError(
            "CAPABILITY_UNAVAILABLE",
            "direct vertical callback handling is disabled; use TrustedVerticalCallbackCoordinator",
        )
