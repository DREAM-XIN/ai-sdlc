#!/usr/bin/env python3
"""Trusted production composition for the v0.3 vertical Operator loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_store_backends import store_backends
from operator_store_runtime import TrustedOperatorStoreConfig, build_trusted_operator_store_runtime
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_callback import TrustedVerticalCallbackCoordinator
from operator_vertical_controller import FeatureTruthGateway, VerticalLoopResumeBackend
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig


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
    executor: TrustedVerticalExecutor
    callback_coordinator: TrustedVerticalCallbackCoordinator
    api_backends: dict[str, Any]


def build_trusted_vertical_runtime(
    config: TrustedVerticalLoopConfig,
    *,
    protection_verifier,
    feature_gateway: FeatureTruthGateway,
    persist_gateway,
    dispatch_gateway,
    clock=None,
) -> TrustedVerticalRuntimeBundle:
    """Build all vertical write surfaces over one protected Store runtime instance."""
    runtime = build_trusted_operator_store_runtime(
        config.store,
        protection_verifier=protection_verifier,
        clock=clock,
    )
    executor = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=persist_gateway,
        dispatch_gateway=dispatch_gateway,
        config=TrustedVerticalExecutorConfig(
            target_ref=config.target_ref,
            trusted_context_digest=config.trusted_context_digest,
            max_auto_steps=config.max_auto_steps,
        ),
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
    callbacks = TrustedVerticalCallbackCoordinator(executor=executor)
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
    feature_gateway: FeatureTruthGateway,
    persist_gateway,
    dispatch_gateway,
    clock=None,
):
    """Compatibility helper returning the canonical backend mapping from the safe bundle."""
    return build_trusted_vertical_runtime(
        config,
        protection_verifier=protection_verifier,
        feature_gateway=feature_gateway,
        persist_gateway=persist_gateway,
        dispatch_gateway=dispatch_gateway,
        clock=clock,
    ).api_backends
