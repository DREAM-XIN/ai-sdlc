#!/usr/bin/env python3
"""Authoritative trusted production backend-bundle builder.

This is the adapter-facing composition entrypoint. It combines target-repository
truth with a separately configured durable control/Store repository and wraps all
Store capabilities in target-scope enforcement before they can be exposed by an
AI-client transport.
"""
from __future__ import annotations

from pathlib import Path
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
from operator_store_backends import OperatorStoreRuntime
from operator_store_model import normalize_repository
from operator_store_remote_git import RemoteGitStateRefBackend
from operator_store_runtime import TrustedOperatorStoreConfig, build_github_operator_store_runtime, build_trusted_operator_store_runtime


def _validate_existing_store_runtime(config: TrustedOperatorRuntimeConfig, runtime: OperatorStoreRuntime) -> None:
    if not isinstance(runtime, OperatorStoreRuntime):
        raise ValueError("existing trusted Store runtime must be OperatorStoreRuntime")
    backend = getattr(runtime, "backend", None)
    if not isinstance(backend, RemoteGitStateRefBackend):
        raise ValueError("existing production Store runtime must use durable remote-Git CAS backend")
    verifier = getattr(runtime, "protection_verifier", None)
    if verifier is None or bool(getattr(verifier, "test_only", False)):
        raise ValueError("existing production Store runtime requires a non-test protection verifier")

    repository = str(getattr(backend, "repository", "") or "")
    state_ref = str(getattr(backend, "state_ref", "") or "")
    remote_name = str(getattr(backend, "remote_name", "") or "")
    repo_path = Path(getattr(backend, "repo_path", ""))
    if not repository or normalize_repository(repository) != config.store_repository:
        raise ValueError("existing trusted Store runtime repository does not match production configuration")
    if state_ref != config.state_ref:
        raise ValueError("existing trusted Store runtime state ref does not match production configuration")
    if remote_name != config.store_remote_name:
        raise ValueError("existing trusted Store runtime remote does not match production configuration")
    if repo_path.resolve() != Path(config.store_checkout).resolve():
        raise ValueError("existing trusted Store runtime checkout does not match production configuration")


def build_trusted_operator_backend_bundle_from_runtime(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    runtime: OperatorStoreRuntime,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    operation_profile: str | None = None,
) -> TrustedOperatorReadBundle:
    """Compose target-scoped adapter backends over one existing production Store runtime.

    This is a server-side composition helper, not a canonical/client capability.
    It accepts only the same durable remote-Git CAS + non-test protection runtime
    class used by production Store composition and requires its repository, ref,
    remote and trusted checkout bindings to match the closed runtime config.
    """
    if not target_read_token:
        raise ValueError("trusted target-read token is required")
    _validate_existing_store_runtime(config, runtime)

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

    return build_trusted_operator_backend_bundle_from_runtime(
        config=config,
        adapter_id=adapter_id,
        target_read_token=target_read_token,
        runtime=runtime,
        github_api_base=github_api_base,
        reader_http_get=reader_http_get,
        operation_profile=operation_profile,
    )
