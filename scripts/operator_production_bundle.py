#!/usr/bin/env python3
"""Authoritative trusted production backend-bundle builder.

This is the adapter-facing composition entrypoint. It combines target-repository
truth with a separately configured durable control/Store repository and wraps all
Store capabilities in target-scope enforcement before they can be exposed by an
AI-client transport.
"""
from __future__ import annotations

from typing import Any, Callable

from operator_decision_backends import DecisionListBackend, NotificationListBackend, OperatorInboxBackend
from operator_production_runtime import (
    BoundedTrustedContextProvider,
    FeatureStatusBackend,
    GitHubTrustedProjectFeatureReader,
    ProjectInspectBackend,
    TrustedOperatorReadBundle,
    TrustedOperatorRuntimeConfig,
    _default_get,
)
from operator_production_store_backends import scoped_store_backends
from operator_store_runtime import TrustedOperatorStoreConfig, build_github_operator_store_runtime, build_trusted_operator_store_runtime


def build_trusted_operator_backend_bundle(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    store_token: str,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    protection_verifier: Any = None,
    operation_profile: str | None = None,
) -> TrustedOperatorReadBundle:
    """Build the shared target-scoped canonical backend bundle.

    `target_read_token` is used only for target Project/Feature truth.
    `store_token` is used only for control-repository protection inspection when
    a verifier is not injected. Store Git authentication is owned by the trusted
    checkout configured outside the AI-client protocol.
    """
    if not target_read_token or not store_token:
        raise ValueError("separate trusted target-read and Store tokens are required")

    store_config = TrustedOperatorStoreConfig(
        repository=config.store_repository,
        trusted_checkout=config.store_checkout,
        state_ref=config.state_ref,
        remote_name=config.store_remote_name,
    )
    if protection_verifier is None:
        runtime = build_github_operator_store_runtime(
            store_config,
            github_token=store_token,
            operator_app_slug=config.operator_app_slug,
            github_api_base=github_api_base,
        )
    else:
        runtime = build_trusted_operator_store_runtime(
            store_config,
            protection_verifier=protection_verifier,
        )

    reader = GitHubTrustedProjectFeatureReader(
        config=config,
        token=target_read_token,
        api_base=github_api_base,
        http_get=reader_http_get,
    )
    provider = BoundedTrustedContextProvider(config=config, adapter_id=adapter_id)
    scoped_store = scoped_store_backends(
        config=config,
        adapter_id=adapter_id,
        runtime=runtime,
        reader=reader,
        operation_profile=operation_profile,
    )

    backends: dict[str, Any] = {
        "project.inspect": ProjectInspectBackend(config=config, adapter_id=adapter_id, reader=reader),
        "feature.status": FeatureStatusBackend(config=config, adapter_id=adapter_id, reader=reader),
        "operator.inbox": OperatorInboxBackend(runtime),
        "decision.list": DecisionListBackend(runtime),
        "notification.list": NotificationListBackend(runtime),
        **scoped_store,
    }
    return TrustedOperatorReadBundle(
        config=config,
        trusted_context_provider=provider,
        backends=backends,
        runtime=runtime,
    )
