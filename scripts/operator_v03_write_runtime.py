#!/usr/bin/env python3
"""Trusted v0.3 write-ready Operator runtime composition.

`build_v03_write_ready_operator_bundle()` is retained as the #245 semantic-write
composition helper for compatibility and focused backend validation. It forces
the vertical Operation profile but does not by itself compose the trusted
Vertical executor/recovery/Persist stack.

A production write-capable adapter that is expected to run the v0.3 Vertical
loop must use `build_v03_vertical_write_ready_operator_bundle()`, which delegates
to the shared-runtime composition. Both factories are transport-neutral and
grant no client capability by themselves.
"""
from __future__ import annotations

from typing import Any, Callable

from operator_production_bundle import build_trusted_operator_backend_bundle
from operator_production_runtime import TrustedOperatorRuntimeConfig, _default_get
from operator_production_write_bundle import (
    REQUIRED_V03_WRITE_SLICE,
    TrustedOperatorWriteBundle,
    extend_with_trusted_decision_writes,
)
from operator_vertical import VERTICAL_PROFILE


def build_v03_write_ready_operator_bundle(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    store_token: str,
    policy_verifier: Any,
    feature_gateway: Any,
    trusted_context_digest: str,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    protection_verifier: Any = None,
) -> TrustedOperatorWriteBundle:
    """Build only the accepted target-scoped semantic write backend layer.

    This compatibility helper does not create the trusted Vertical executor,
    callback recovery, exact Persist gateway, or failure-classifying recovery
    executor. New production Vertical adapters must use the full factory below.
    """
    read_bundle = build_trusted_operator_backend_bundle(
        config=config,
        adapter_id=adapter_id,
        target_read_token=target_read_token,
        store_token=store_token,
        github_api_base=github_api_base,
        reader_http_get=reader_http_get,
        protection_verifier=protection_verifier,
        operation_profile=VERTICAL_PROFILE,
    )
    write_bundle = extend_with_trusted_decision_writes(
        read_bundle,
        policy_verifier=policy_verifier,
        feature_gateway=feature_gateway,
        trusted_context_digest=trusted_context_digest,
    )
    missing = REQUIRED_V03_WRITE_SLICE - set(write_bundle.backends)
    if missing:
        raise RuntimeError(f"v0.3 write-ready runtime missing frozen write slice: {sorted(missing)}")
    return write_bundle


def build_v03_vertical_write_ready_operator_bundle(
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
    policy_verifier: Any,
    trusted_context_digest: str,
    collector_namespace_policy: str,
    trusted_role_policy: str,
    max_auto_steps: int = 16,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    clock=None,
):
    """Build the authoritative full Vertical production bundle for one Feature.

    The import is intentionally lazy so the semantic-only compatibility helper
    remains standalone. This full factory resolves one shared-runtime builder and
    introduces no alternate Store, Vertical, Persist, or dispatch authority.
    """
    from operator_v03_vertical_production_runtime import build_v03_vertical_production_bundle

    return build_v03_vertical_production_bundle(
        config=config,
        adapter_id=adapter_id,
        feature_id=feature_id,
        target_read_token=target_read_token,
        protection_verifier=protection_verifier,
        rollout_verifier=rollout_verifier,
        resolution_policy_verifier=resolution_policy_verifier,
        feature_gateway=feature_gateway,
        feature_event_gateway=feature_event_gateway,
        dispatch_gateway=dispatch_gateway,
        collector_content_loader=collector_content_loader,
        policy_verifier=policy_verifier,
        trusted_context_digest=trusted_context_digest,
        collector_namespace_policy=collector_namespace_policy,
        trusted_role_policy=trusted_role_policy,
        max_auto_steps=max_auto_steps,
        github_api_base=github_api_base,
        reader_http_get=reader_http_get,
        clock=clock,
    )
