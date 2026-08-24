#!/usr/bin/env python3
"""Trusted-only v0.3 protection verifier with causal ruleset attestation.

The generic repository/ruleset protection verifiers remain read-only and
fail-closed. This wrapper is intentionally for the explicit trusted-main v0.3
Vertical policy workflow only: before relying on the ruleset path it performs
#262's marker -> canonical strict write attestation. #336 additionally binds the
repository source identity to the same ruleset's exact current-detail surface and
the just-completed write response when GitHub history retains its observed opaque
source serialization. The resulting in-memory verifier is injected into the
existing composite; no generic read-only authority is widened.

Attestation is process-local and is never serialized. A process cache reuses the
same attested verifier for repeated checks of the same repository/ref (notably
the before/after postverify checks) so verification itself does not create a new
ruleset generation between those checks.
"""
from __future__ import annotations

import hashlib
from typing import Callable

from operator_store_github_protection_composite import (
    GitHubRepositoryProtectionVerifier as GenericGitHubRepositoryProtectionVerifier,
)
from operator_store_github_ruleset_current_detail_bound import (
    CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner,
)


_PROCESS_ATTESTED_RULESET_VERIFIERS: dict[tuple, object] = {}


def _token_binding(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class GitHubRepositoryProtectionVerifier(GenericGitHubRepositoryProtectionVerifier):
    """Trusted v0.3 composite that establishes causal ruleset proof per process."""

    def __init__(
        self,
        *,
        token: str,
        operator_app_slug: str,
        operator_app_id: int | None = None,
        api_base: str = "https://api.github.com",
        api_version: str = "2022-11-28",
        branch_http_get=None,
        ruleset_http_get=None,
        clock=None,
        provisioner_factory: Callable | None = None,
    ):
        if ruleset_http_get is not None:
            raise ValueError(
                "trusted causal protection does not accept a read-only ruleset transport"
            )
        super().__init__(
            token=token,
            operator_app_slug=operator_app_slug,
            operator_app_id=None,
            api_base=api_base,
            api_version=api_version,
            branch_http_get=branch_http_get,
            clock=clock,
        )
        self._admin_token = token
        self._operator_app_id = operator_app_id
        self._api_base = api_base
        self._api_version = api_version
        self._provisioner_factory = (
            provisioner_factory
            or CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner
        )

    def _attested_ruleset_verifier(self, repository: str, state_ref: str):
        if self._operator_app_id is None:
            return None
        key = (
            repository.lower(),
            state_ref,
            self._operator_app_id,
            self._api_base,
            self._api_version,
            _token_binding(self._admin_token),
            id(self._provisioner_factory),
        )
        cached = _PROCESS_ATTESTED_RULESET_VERIFIERS.get(key)
        if cached is not None:
            return cached

        provisioner = self._provisioner_factory(
            admin_token=self._admin_token,
            operator_app_id=self._operator_app_id,
            api_base=self._api_base,
            api_version=self._api_version,
        )
        provisioner.ensure_rulesets(repository, state_ref)
        verifier = provisioner.protection_verifier()
        _PROCESS_ATTESTED_RULESET_VERIFIERS[key] = verifier
        return verifier

    def verify(self, repository: str, state_ref: str):
        self.ruleset = self._attested_ruleset_verifier(repository, state_ref)
        return super().verify(repository, state_ref)
