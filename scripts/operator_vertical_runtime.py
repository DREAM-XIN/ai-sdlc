#!/usr/bin/env python3
"""Trusted production composition for the v0.3 vertical Operator loop."""
from __future__ import annotations

from dataclasses import dataclass

from operator_store_backends import store_backends
from operator_store_runtime import TrustedOperatorStoreConfig, build_trusted_operator_store_runtime
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_controller import FeatureTruthGateway, VerticalExecutor, VerticalLoopResumeBackend


@dataclass(frozen=True)
class TrustedVerticalLoopConfig:
    store: TrustedOperatorStoreConfig
    target_ref: str
    manifest_path: str
    collector_namespace_policy: str
    trusted_role_policy: str

    def __post_init__(self):
        if not self.target_ref or not self.manifest_path:
            raise ValueError("trusted Feature target ref and manifest path are required")
        if not self.manifest_path.startswith("state/features/") or not self.manifest_path.endswith(('.yaml', '.yml')):
            raise ValueError("trusted vertical manifest path must be under state/features/")
        if not self.collector_namespace_policy or not self.trusted_role_policy:
            raise ValueError("trusted collector and role policies are required")


def build_trusted_vertical_operator_api_backends(
    config: TrustedVerticalLoopConfig,
    *,
    protection_verifier,
    feature_gateway: FeatureTruthGateway,
    executor: VerticalExecutor,
    clock=None,
):
    """Enable start/resume only from trusted composition; clients cannot select profile."""
    runtime = build_trusted_operator_store_runtime(
        config.store,
        protection_verifier=protection_verifier,
        clock=clock,
    )
    resume = VerticalLoopResumeBackend(
        runtime=runtime,
        feature_gateway=feature_gateway,
        executor=executor,
    )
    return store_backends(
        runtime,
        operation_profile=VERTICAL_PROFILE,
        resume_backend=resume,
    )
