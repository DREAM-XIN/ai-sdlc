#!/usr/bin/env python3
"""Trusted-only v0.3 protection verifier with causal ruleset attestation.

Generic/read-only protection semantics remain unchanged. This wrapper exists only
for the trusted-main v0.3 Vertical-policy workflow and may emit bounded rejection
category diagnostics when the already-existing proof fails closed. Diagnostics
contain category names only and never raw GitHub values, tokens, or response
bodies.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from operator_store_github_protection_composite import (
    GitHubRepositoryProtectionVerifier as GenericGitHubRepositoryProtectionVerifier,
)
from operator_store_github_ruleset_version_diagnostic import (
    VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_protection import PROTECTED


_PROCESS_ATTESTED_RULESET_VERIFIERS: dict[tuple, object] = {}
_DIAGNOSTIC_CATEGORY_ALLOWLIST = frozenset({
    "history-summary-unavailable",
    "history-summary-malformed",
    "history-summary-older-version",
    "history-summary-newer-version",
    "history-summary-invalid-metadata",
    "history-summary-replay-timeout",
    "history-summary-initial-settle-timeout",
    "underlying-version-proof-rejected",
    "version-proof-attestation-missing",
    "version-proof-current-updated-at-rejected",
    "version-proof-current-rules-rejected",
    "version-proof-initial-history-unavailable",
    "version-proof-initial-history-shape-rejected",
    "version-proof-initial-history-version-rejected",
    "version-proof-exact-version-unavailable",
    "version-proof-exact-version-id-rejected",
    "version-proof-state-shape-rejected",
    "version-proof-state-digest-rejected",
    "version-proof-state-rules-rejected",
    "version-proof-state-current-identity-rejected",
    "version-proof-final-current-unavailable",
    "version-proof-final-current-drift-rejected",
    "version-proof-final-current-updated-at-rejected",
    "version-proof-final-current-rules-rejected",
    "version-proof-final-history-unavailable",
    "version-proof-final-history-shape-rejected",
    "version-proof-final-history-version-rejected",
    "version-proof-final-history-updated-at-rejected",
    "ruleset-proof-rejected-unclassified",
})


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
            or VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner
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

    def diagnostic_categories(self) -> tuple[str, ...]:
        getter = getattr(self.ruleset, "protection_diagnostic_categories", None)
        raw = getter() if callable(getter) else ()
        categories = tuple(
            value
            for value in raw
            if isinstance(value, str) and value in _DIAGNOSTIC_CATEGORY_ALLOWLIST
        )
        return categories or ("ruleset-proof-rejected-unclassified",)

    def verify(self, repository: str, state_ref: str):
        self.ruleset = self._attested_ruleset_verifier(repository, state_ref)
        receipt = super().verify(repository, state_ref)
        if receipt.status != PROTECTED:
            print(json.dumps({
                "protection_rejection_diagnostic": {
                    "categories": self.diagnostic_categories(),
                    "raw_values_retained": False,
                    "receipt_status": receipt.status,
                }
            }, sort_keys=True))
        return receipt
