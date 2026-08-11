#!/usr/bin/env python3
"""Trusted v0.3 adapter + Vertical production composition.

This module joins the adapter-facing production bundle to the existing trusted
Vertical runtime without creating a second Store runtime or another Persist
authority path. Canonical/client writes remain the frozen v0.3 slice; resume,
callback coordination, and executor access remain server-side only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from operator_decision_policy import ProtectedDecisionPolicyVerifier
from operator_production_bundle import build_trusted_operator_backend_bundle_from_runtime
from operator_production_runtime import (
    TrustedFeatureBinding,
    TrustedOperatorReadBundle,
    TrustedOperatorRuntimeConfig,
    _default_get,
)
from operator_production_write_bundle import (
    REQUIRED_V03_WRITE_SLICE,
    TrustedOperatorWriteBundle,
    extend_with_trusted_decision_writes,
)
from operator_store_model import normalize_repository
from operator_store_runtime import TrustedOperatorStoreConfig
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
from operator_vertical_runtime import (
    TrustedVerticalLoopConfig,
    TrustedVerticalRuntimeBundle,
    VerticalLoopStartBackend,
    build_trusted_vertical_runtime,
)


class _DeferredExactVerticalPersistGateway:
    """Fail-closed one-time bridge for the Vertical builder/runtime dependency cycle.

    `build_trusted_vertical_runtime()` owns creation of the protected Store
    runtime, while `DurableVerticalFeaturePersistGateway` must be bound to that
    exact runtime. The executor receives this bridge during construction; it is
    bound exactly once immediately after the Vertical bundle is returned and
    before any adapter backend is exposed or invoked.
    """

    def __init__(self):
        self._delegate: DurableVerticalFeaturePersistGateway | None = None

    def bind(self, delegate: DurableVerticalFeaturePersistGateway) -> None:
        if self._delegate is not None:
            raise RuntimeError("Vertical Persist delegate is already bound")
        if not isinstance(delegate, DurableVerticalFeaturePersistGateway):
            raise ValueError("Vertical Persist delegate must be DurableVerticalFeaturePersistGateway")
        self._delegate = delegate

    @property
    def delegate(self) -> DurableVerticalFeaturePersistGateway:
        if self._delegate is None:
            raise RuntimeError("Vertical Persist delegate is not bound")
        return self._delegate

    def lookup_feature_event(self, *, event_id: str, target_ref: str):
        return self.delegate.lookup_feature_event(event_id=event_id, target_ref=target_ref)

    def persist_feature_event(self, *, event: dict[str, Any], target_ref: str):
        return self.delegate.persist_feature_event(event=event, target_ref=target_ref)


@dataclass(frozen=True)
class TrustedV03VerticalProductionBundle:
    """Server-side production bundle for one trusted Feature and adapter."""

    write_bundle: TrustedOperatorWriteBundle
    vertical_bundle: TrustedVerticalRuntimeBundle
    feature_id: str

    @property
    def runtime(self):
        return self.write_bundle.runtime

    @property
    def backends(self):
        return self.write_bundle.backends

    @property
    def adapter_write_backends(self) -> dict[str, Any]:
        return {name: self.write_bundle.backends[name] for name in REQUIRED_V03_WRITE_SLICE}

    @property
    def executor(self):
        return self.vertical_bundle.executor

    @property
    def callback_coordinator(self):
        return self.vertical_bundle.callback_coordinator

    @property
    def decision_notification_coordinator(self):
        return self.write_bundle.decision_notification_coordinator


def _validate_decision_policy_binding(
    config: TrustedOperatorRuntimeConfig,
    policy_verifier: ProtectedDecisionPolicyVerifier,
) -> None:
    """Preserve the accepted Vertical Decision-policy trust-domain fence."""
    if (
        not isinstance(policy_verifier, ProtectedDecisionPolicyVerifier)
        or policy_verifier.repository != normalize_repository(config.store_repository)
        or policy_verifier.state_ref != config.state_ref
        or policy_verifier.operation_profile != VERTICAL_PROFILE
    ):
        raise ValueError("Decision policy verifier is not bound to this production Store/profile")


def _feature_scoped_adapter_config(
    config: TrustedOperatorRuntimeConfig,
    *,
    feature_id: str,
    target_ref: str,
) -> TrustedOperatorRuntimeConfig:
    """Narrow a trusted multi-Feature install to one Vertical executor binding."""
    return TrustedOperatorRuntimeConfig(
        target_repository=config.target_repository,
        store_repository=config.store_repository,
        installation_ref=config.installation_ref,
        store_checkout=config.store_checkout,
        principal=config.principal,
        feature_bindings=(TrustedFeatureBinding(feature_id, target_ref),),
        state_ref=config.state_ref,
        store_remote_name=config.store_remote_name,
        operator_app_slug=config.operator_app_slug,
    )


def build_v03_vertical_production_bundle(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    feature_id: str,
    target_read_token: str,
    protection_verifier: Any,
    rollout_verifier: Any,
    resolution_policy_verifier: Any,
    feature_gateway: Any,
    feature_event_gateway: Any,
    dispatch_gateway: Any,
    collector_content_loader: Callable[..., Any],
    policy_verifier: ProtectedDecisionPolicyVerifier,
    trusted_context_digest: str,
    collector_namespace_policy: str,
    trusted_role_policy: str,
    max_auto_steps: int = 16,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    clock=None,
) -> TrustedV03VerticalProductionBundle:
    """Compose one Store runtime across adapter writes and trusted Vertical work.

    One returned bundle is intentionally scoped to exactly one configured
    Feature/ref pair because the accepted Vertical executor itself binds one
    target ref and one Feature Manifest path. A trusted installation that owns
    multiple Features creates one bundle per Feature rather than exposing all
    Feature starts through one executor binding.
    """
    if not feature_id:
        raise ValueError("trusted Feature id is required for Vertical composition")
    target_ref = config.feature_ref(feature_id)
    if not trusted_context_digest:
        raise ValueError("trusted context digest is required for Vertical composition")
    _validate_decision_policy_binding(config, policy_verifier)
    adapter_config = _feature_scoped_adapter_config(
        config,
        feature_id=feature_id,
        target_ref=target_ref,
    )

    store_config = TrustedOperatorStoreConfig(
        repository=config.store_repository,
        trusted_checkout=config.store_checkout,
        state_ref=config.state_ref,
        remote_name=config.store_remote_name,
    )
    vertical_config = TrustedVerticalLoopConfig(
        store=store_config,
        target_ref=target_ref,
        manifest_path=f"state/features/{feature_id}.yaml",
        collector_namespace_policy=collector_namespace_policy,
        trusted_role_policy=trusted_role_policy,
        trusted_context_digest=trusted_context_digest,
        max_auto_steps=max_auto_steps,
    )

    deferred_persist = _DeferredExactVerticalPersistGateway()
    vertical = build_trusted_vertical_runtime(
        vertical_config,
        protection_verifier=protection_verifier,
        rollout_verifier=rollout_verifier,
        resolution_policy_verifier=resolution_policy_verifier,
        feature_gateway=feature_gateway,
        persist_gateway=deferred_persist,
        dispatch_gateway=dispatch_gateway,
        collector_content_loader=collector_content_loader,
        decision_policy_verifier=None,
        clock=clock,
    )

    durable_persist = DurableVerticalFeaturePersistGateway(
        runtime=vertical.runtime,
        event_gateway=feature_event_gateway,
    )
    deferred_persist.bind(durable_persist)
    if deferred_persist.delegate.runtime is not vertical.runtime:
        raise RuntimeError("Vertical Persist gateway is not bound to the unique production Store runtime")

    read_bundle = build_trusted_operator_backend_bundle_from_runtime(
        config=adapter_config,
        adapter_id=adapter_id,
        target_read_token=target_read_token,
        runtime=vertical.runtime,
        github_api_base=github_api_base,
        reader_http_get=reader_http_get,
        operation_profile=VERTICAL_PROFILE,
    )

    # Keep #245's target-scoped start authority, then add only the accepted
    # Vertical auto-advance behavior. Do not expose the raw Vertical backend map
    # because that would leak server-only operation.resume into the adapter map.
    scoped_backends = dict(read_bundle.backends)
    scoped_backends["operation.start"] = VerticalLoopStartBackend(
        delegate=scoped_backends["operation.start"],
        executor=vertical.executor,
    )
    adapter_read_bundle = TrustedOperatorReadBundle(
        config=read_bundle.config,
        trusted_context_provider=read_bundle.trusted_context_provider,
        backends=scoped_backends,
        runtime=read_bundle.runtime,
    )
    write_bundle = extend_with_trusted_decision_writes(
        adapter_read_bundle,
        policy_verifier=policy_verifier,
        feature_gateway=feature_gateway,
        trusted_context_digest=trusted_context_digest,
    )

    if write_bundle.runtime is not vertical.runtime:
        raise RuntimeError("adapter and Vertical composition do not share one Store runtime")
    if write_bundle.config.feature_ids != frozenset({feature_id}):
        raise RuntimeError("adapter Vertical production bundle is not scoped to exactly one trusted Feature")
    if set(REQUIRED_V03_WRITE_SLICE) - set(write_bundle.backends):
        raise RuntimeError("integrated runtime is missing the frozen v0.3 adapter write slice")
    if "operation.resume" in write_bundle.backends:
        raise RuntimeError("server-only operation.resume leaked into adapter-facing production backends")
    if not isinstance(write_bundle.backends["operation.start"], VerticalLoopStartBackend):
        raise RuntimeError("adapter operation.start is not bound to trusted Vertical executor")

    return TrustedV03VerticalProductionBundle(
        write_bundle=write_bundle,
        vertical_bundle=vertical,
        feature_id=feature_id,
    )
