#!/usr/bin/env python3
"""Composite trusted GitHub protection proof for Operator Store state refs."""
from __future__ import annotations

from operator_store_github_protection import GitHubBranchProtectionVerifier
from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionReceipt


class GitHubRepositoryProtectionVerifier:
    """Accept either a classic branch-protection proof or a ruleset proof.

    Classic organization branch restrictions remain supported. When a trusted
    Operator Integration id is configured, repository rulesets provide the
    personal-repository-compatible proof path. Any ambiguous inspection fails
    closed as UNKNOWN.
    """

    test_only = False

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
    ):
        branch_kwargs = {
            "token": token,
            "operator_app_slug": operator_app_slug,
            "api_base": api_base,
        }
        if branch_http_get is not None:
            branch_kwargs["http_get"] = branch_http_get
        if clock is not None:
            branch_kwargs["clock"] = clock
        self.branch = GitHubBranchProtectionVerifier(**branch_kwargs)

        self.ruleset = None
        if operator_app_id is not None:
            ruleset_kwargs = {
                "token": token,
                "operator_app_id": operator_app_id,
                "api_base": api_base,
                "api_version": api_version,
            }
            if ruleset_http_get is not None:
                ruleset_kwargs["http_get"] = ruleset_http_get
            if clock is not None:
                ruleset_kwargs["clock"] = clock
            self.ruleset = GitHubRulesetProtectionVerifier(**ruleset_kwargs)

    def verify(self, repository: str, state_ref: str) -> ProtectionReceipt:
        classic = self.branch.verify(repository, state_ref)
        if classic.status == PROTECTED:
            return classic
        if self.ruleset is None:
            return classic

        ruleset = self.ruleset.verify(repository, state_ref)
        if ruleset.status == PROTECTED:
            return ruleset
        if classic.status == UNKNOWN or ruleset.status == UNKNOWN:
            return ProtectionReceipt(
                repository=repository,
                state_ref=state_ref,
                status=UNKNOWN,
                verifier_identity="github-repository-protection",
                verified_at=ruleset.verified_at or classic.verified_at,
                policy_digest=None,
            )
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=UNPROTECTED,
            verifier_identity="github-repository-protection",
            verified_at=ruleset.verified_at or classic.verified_at,
            policy_digest=ruleset.policy_digest or classic.policy_digest,
        )
