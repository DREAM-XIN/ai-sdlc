#!/usr/bin/env python3
"""Deterministic checks for trusted-only protection rejection diagnostics."""
from __future__ import annotations

import ast
import inspect
import textwrap

from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_github_ruleset_causal_summary import (
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)


class _DiagnosticRuleset:
    def __init__(self, values):
        self.values = values

    def protection_diagnostic_categories(self):
        return self.values


def _trusted_with_ruleset(values):
    verifier = GitHubRepositoryProtectionVerifier.__new__(GitHubRepositoryProtectionVerifier)
    verifier.ruleset = _DiagnosticRuleset(values)
    return verifier


def main() -> None:
    trusted = _trusted_with_ruleset((
        "history-summary-invalid-metadata",
        "secret=must-never-escape",
    ))
    assert trusted.diagnostic_categories() == ("history-summary-invalid-metadata",)

    empty = _trusted_with_ruleset(())
    assert empty.diagnostic_categories() == ("ruleset-proof-rejected-unclassified",)

    classify = CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner._non_exact_summary_category
    assert classify([{"version_id": 4, "updated_at": "x"}], expected_version_id=5) == "history-summary-older-version"
    assert classify([{"version_id": 6, "updated_at": "x"}], expected_version_id=5) == "history-summary-newer-version"
    assert classify([{"version_id": 5}], expected_version_id=5) == "history-summary-invalid-metadata"
    assert classify([], expected_version_id=5) == "history-summary-malformed"

    # Guard the authority boundary mechanically: the trusted verify method still
    # delegates the actual verdict to the inherited composite and only observes
    # the returned receipt afterwards.
    tree = ast.parse(textwrap.dedent(inspect.getsource(GitHubRepositoryProtectionVerifier.verify)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "verify"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
        for call in calls
    )

    source = inspect.getsource(GitHubRepositoryProtectionVerifier)
    assert "raw_values_retained" in source
    assert "secret" not in source.lower()
    print("v0.3 trusted protection rejection diagnostic validation passed")


if __name__ == "__main__":
    main()
