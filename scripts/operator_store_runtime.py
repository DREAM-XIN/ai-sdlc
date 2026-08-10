#!/usr/bin/env python3
"""Trusted production composition boundary for the v0.3 Operator Store.

This module is deliberately not a canonical capability or Worker-facing config parser.
Trusted installation/control code owns repository, local trusted checkout, verifier,
and state-ref selection. Client/Feature/Worker payloads cannot override them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from operator_store_backends import OperatorStoreRuntime, store_backends
from operator_store_git import GitStateRefBackend
from operator_store_protection import StateRefProtectionVerifier

DEFAULT_OPERATOR_STATE_REF = "refs/heads/ai-sdlc-operator-state"


@dataclass(frozen=True)
class TrustedOperatorStoreConfig:
    repository: str
    trusted_checkout: Path
    state_ref: str = DEFAULT_OPERATOR_STATE_REF

    def __post_init__(self):
        if not self.repository or "/" not in self.repository:
            raise ValueError("trusted Operator Store repository identity is required")
        if not str(self.trusted_checkout):
            raise ValueError("trusted Operator Store checkout is required")
        if not self.state_ref.startswith("refs/heads/"):
            raise ValueError("trusted Operator Store state ref must be a branch ref")


def build_trusted_operator_store_runtime(
    config: TrustedOperatorStoreConfig,
    *,
    protection_verifier: StateRefProtectionVerifier,
    clock=None,
):
    """Compose the repository-backed runtime from trusted control inputs only.

    The verifier remains authoritative for whether semantic writes are enabled.
    Merely constructing this runtime never attests that the state ref is protected.
    """
    backend = GitStateRefBackend(
        repo_path=config.trusted_checkout,
        repository=config.repository,
        state_ref=config.state_ref,
    )
    kwargs = {"backend": backend, "protection_verifier": protection_verifier}
    if clock is not None:
        kwargs["clock"] = clock
    return OperatorStoreRuntime(**kwargs)


def build_trusted_operator_api_backends(
    config: TrustedOperatorStoreConfig,
    *,
    protection_verifier: StateRefProtectionVerifier,
    clock=None,
):
    """Return only the Store capabilities approved for this workstream."""
    runtime = build_trusted_operator_store_runtime(
        config,
        protection_verifier=protection_verifier,
        clock=clock,
    )
    return store_backends(runtime)
