#!/usr/bin/env python3
"""Trusted production composition boundary for the v0.3 Operator Store.

This module is deliberately not a canonical capability or Worker-facing config parser.
Trusted installation/control code owns repository, trusted checkout, remote, verifier,
and state-ref selection. Client/Feature/Worker payloads cannot override them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from operator_store_backends import OperatorStoreRuntime, store_backends
from operator_store_github_protection_composite import GitHubRepositoryProtectionVerifier
from operator_store_protection import StateRefProtectionVerifier
from operator_store_remote_git import RemoteGitStateRefBackend

DEFAULT_OPERATOR_STATE_REF = "refs/heads/ai-sdlc-operator-state"


@dataclass(frozen=True)
class TrustedOperatorStoreConfig:
    repository: str
    trusted_checkout: Path
    state_ref: str = DEFAULT_OPERATOR_STATE_REF
    remote_name: str = "origin"

    def __post_init__(self):
        if not self.repository or "/" not in self.repository:
            raise ValueError("trusted Operator Store repository identity is required")
        if not str(self.trusted_checkout):
            raise ValueError("trusted Operator Store checkout is required")
        if not self.state_ref.startswith("refs/heads/"):
            raise ValueError("trusted Operator Store state ref must be a branch ref")
        if not self.remote_name or any(ch.isspace() for ch in self.remote_name):
            raise ValueError("trusted Operator Store remote name is invalid")


def _require_production_verifier(protection_verifier: StateRefProtectionVerifier) -> None:
    if bool(getattr(protection_verifier, "test_only", False)):
        raise ValueError("test-only protection verifier cannot enable production Operator Store runtime")


def build_trusted_operator_store_runtime(
    config: TrustedOperatorStoreConfig,
    *,
    protection_verifier: StateRefProtectionVerifier,
    clock=None,
    plan_guard=None,
):
    """Compose the durable remote repository-backed runtime from trusted inputs."""
    _require_production_verifier(protection_verifier)
    backend = RemoteGitStateRefBackend(
        repo_path=config.trusted_checkout,
        repository=config.repository,
        state_ref=config.state_ref,
        remote_name=config.remote_name,
    )
    kwargs = {
        "backend": backend,
        "protection_verifier": protection_verifier,
        "plan_guard": plan_guard,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return OperatorStoreRuntime(**kwargs)


def build_github_operator_store_runtime(
    config: TrustedOperatorStoreConfig,
    *,
    github_token: str,
    operator_app_slug: str,
    operator_app_id: int | None = None,
    github_api_base: str = "https://api.github.com",
    github_api_version: str = "2022-11-28",
    clock=None,
    plan_guard=None,
):
    """Concrete production path: remote Git CAS + trusted GitHub protection proof.

    Organization repositories may continue to prove protection through classic
    branch push restrictions. When trusted installation configuration supplies
    the numeric Operator Integration id, personal repositories may additionally
    prove the same safety boundary through layered repository rulesets.
    """
    verifier = GitHubRepositoryProtectionVerifier(
        token=github_token,
        operator_app_slug=operator_app_slug,
        operator_app_id=operator_app_id,
        api_base=github_api_base,
        api_version=github_api_version,
    )
    return build_trusted_operator_store_runtime(
        config,
        protection_verifier=verifier,
        clock=clock,
        plan_guard=plan_guard,
    )


def build_trusted_operator_api_backends(
    config: TrustedOperatorStoreConfig,
    *,
    protection_verifier: StateRefProtectionVerifier,
    clock=None,
    plan_guard=None,
):
    """Return only the Store capabilities approved for this workstream."""
    runtime = build_trusted_operator_store_runtime(
        config,
        protection_verifier=protection_verifier,
        clock=clock,
        plan_guard=plan_guard,
    )
    return store_backends(runtime)
