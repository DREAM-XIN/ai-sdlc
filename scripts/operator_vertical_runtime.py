#!/usr/bin/env python3
"""Trusted production composition for the v0.3 vertical Operator loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_store_backends import store_backends
from operator_store_model import normalize_repository
from operator_store_runtime import TrustedOperatorStoreConfig, build_trusted_operator_store_runtime
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_callback import TrustedVerticalCallbackCoordinator
from operator_vertical_controller import FeatureTruthGateway, VerticalLoopResumeBackend
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from operator_effect_rollout import EffectLineageWriteFence
from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier


@dataclass(frozen=True)
class TrustedVerticalLoopConfig:
    store: TrustedOperatorStoreConfig
    target_ref: str
    manifest_path: str
    collector_namespace_policy: str
    trusted_role_policy: str
    trusted_context_digest: str
    max_auto_steps: int = 16

    def __post_init__(self):
        if not self.target_ref or not self.manifest_path:
            raise ValueError("trusted Feature target ref and manifest path are required")
        if not self.manifest_path.startswith("state/features/") or not self.manifest_path.endswith((".yaml", ".yml")):
            raise ValueError("trusted vertical manifest path must be under state/features/")
        if not self.collector_namespace_policy or not self.trusted_role_policy or not self.trusted_context_digest:
            raise ValueError("trusted collector, role and context policies are required")
        if self.max_auto_steps < 1 or self.max_auto_steps > 64:
            raise ValueError("invalid vertical max_auto_steps")


@dataclass(frozen=True)
class TrustedVerticalRuntimeBundle:
    runtime: Any
    executor: Any
    callback_coordinator: TrustedVerticalCallbackCoordinator
    api_backends: dict[str, Any]


class VerticalLoopStartBackend:
    """Profile-bound operation.start that immediately advances the approved vertical slice."""

    def __init__(self, *, delegate, executor):
        self.delegate = delegate
        self.executor = executor

    def availability(self, capability, trusted_context):
        return self.delegate.availability(capability, trusted_context)

    def invoke(self, request, trusted_context):
        started = self.delegate.invoke(request, trusted_context)
        operation_id = started.get("operation_id")
        if not operation_id:
            raise RuntimeError("profile-bound operation.start returned no operation id")
        return self.executor.advance_until_stop(operation_id=operation_id)


def build_trusted_vertical_runtime(
    config: TrustedVerticalLoopConfig,
    *,
    protection_verifier,
    rollout_verifier,
    resolution_policy_verifier: ProtectedEffectResolutionPolicyVerifier,
    feature_gateway: FeatureTruthGateway,
    persist_gateway,
    dispatch_gateway,
    collector_content_loader,
    clock=None,
) -> TrustedVerticalRuntimeBundle:
    """Build all vertical write surfaces only after trusted rollout/fence/policy verification."""
    if not callable(collector_content_loader):
        raise ValueError("trusted collector content loader is required")
    if rollout_verifier is None or not callable(getattr(rollout_verifier, "verify", None)):
        raise ValueError("trusted Effect Lineage rollout verifier is required")
    if not isinstance(resolution_policy_verifier, ProtectedEffectResolutionPolicyVerifier):
        raise ValueError("trusted current Effect Resolution policy verifier is required")
    if (
        resolution_policy_verifier.repository != normalize_repository(config.store.repository)
        or resolution_policy_verifier.state_ref != config.store.state_ref
        or resolution_policy_verifier.operation_profile != VERTICAL_PROFILE
    ):
        raise ValueError("Effect Resolution policy verifier is not bound to this production Store/profile")

    rollout = rollout_verifier.verify(
        repository=config.store.repository,
        state_ref=config.store.state_ref,
        operation_profile=VERTICAL_PROFILE,
    )
    rollout.validate_for(
        repository=config.store.repository,
        state_ref=config.store.state_ref,
        operation_profile=VERTICAL_PROFILE,
    )
    if bool(getattr(rollout, "test_only", False)):
        raise ValueError("test-only Effect Lineage rollout cannot enable production vertical runtime")

    resolution_policy_verifier.verify_current()

    runtime = build_trusted_operator_store_runtime(
        config.store,
        protection_verifier=protection_verifier,
        clock=clock,
        plan_guard=EffectLineageWriteFence(rollout),
    )
    base_executor = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=persist_gateway,
        dispatch_gateway=dispatch_gateway,
        config=TrustedVerticalExecutorConfig(
            target_ref=config.target_ref,
            trusted_context_digest=config.trusted_context_digest,
            effect_lineage_required=rollout.effect_lineage_required,
            old_writers_quiesced=bool(rollout.writer_fence_receipt_digest),
            rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest,
            max_auto_steps=config.max_auto_steps,
        ),
        resolution_policy_verifier=resolution_policy_verifier,
    )
    executor = TrustedRecoveringVerticalExecutor(
        base_executor=base_executor,
        content_loader=collector_content_loader,
        trusted_role_policy=config.trusted_role_policy,
        collector_namespace_policy=config.collector_namespace_policy,
    )
    resume = VerticalLoopResumeBackend(
        runtime=runtime,
        feature_gateway=feature_gateway,
        executor=executor,
    )
    backends = store_backends(
        runtime,
        operation_profile=VERTICAL_PROFILE,
        resume_backend=resume,
    )
    backends["operation.start"] = VerticalLoopStartBackend(
        delegate=backends["operation.start"],
        executor=executor,
    )
    callbacks = TrustedVerticalCallbackCoordinator(
        executor=executor,
        trusted_role_policy=config.trusted_role_policy,
        collector_namespace_policy=config.collector_namespace_policy,
        content_loader=collector_content_loader,
    )
    return TrustedVerticalRuntimeBundle(
        runtime=runtime,
        executor=executor,
        callback_coordinator=callbacks,
        api_backends=backends,
    )


def build_trusted_vertical_operator_api_backends(
    config: TrustedVerticalLoopConfig,
    *,
    protection_verifier,
    rollout_verifier,
    resolution_policy_verifier: ProtectedEffectResolutionPolicyVerifier,
    feature_gateway: FeatureTruthGateway,
    persist_gateway,
    dispatch_gateway,
    collector_content_loader,
    clock=None,
):
    """Compatibility helper returning the canonical backend mapping from the safe bundle."""
    return build_trusted_vertical_runtime(
        config,
        protection_verifier=protection_verifier,
        rollout_verifier=rollout_verifier,
        resolution_policy_verifier=resolution_policy_verifier,
        feature_gateway=feature_gateway,
        persist_gateway=persist_gateway,
        dispatch_gateway=dispatch_gateway,
        collector_content_loader=collector_content_loader,
        clock=clock,
    ).api_backends
