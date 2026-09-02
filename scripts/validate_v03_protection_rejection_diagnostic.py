#!/usr/bin/env python3
"""Deterministic checks for trusted-only protection rejection diagnostics."""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_github_ruleset_attested import RulesetWriteAttestation
from operator_store_github_ruleset_causal_summary import (
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_version_diagnostic import (
    VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner,
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


def _detailed_verifier(base_latest, base_get, attestation):
    fake = SimpleNamespace()
    fake.http_get = base_get
    fake._latest_version_state = base_latest(fake)
    fake.protection_diagnostic_categories = lambda: ("underlying-version-proof-rejected",)

    provisioner = VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner.__new__(
        VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner
    )
    provisioner.write_attestations = {attestation.ruleset_id: attestation}

    original = CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner.protection_verifier
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner.protection_verifier = (
        lambda self: fake
    )
    try:
        return provisioner.protection_verifier()
    finally:
        CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner.protection_verifier = original


def main() -> None:
    trusted = _trusted_with_ruleset((
        "history-summary-invalid-metadata",
        "secret=must-never-escape",
    ))
    assert trusted.diagnostic_categories() == ("history-summary-invalid-metadata",)

    detailed = _trusted_with_ruleset((
        "version-proof-current-updated-at-rejected",
        "raw-value=must-never-escape",
    ))
    assert detailed.diagnostic_categories() == ("version-proof-current-updated-at-rejected",)

    empty = _trusted_with_ruleset(())
    assert empty.diagnostic_categories() == ("ruleset-proof-rejected-unclassified",)

    classify = CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner._non_exact_summary_category
    assert classify([{"version_id": 4, "updated_at": "x"}], expected_version_id=5) == "history-summary-older-version"
    assert classify([{"version_id": 6, "updated_at": "x"}], expected_version_id=5) == "history-summary-newer-version"
    assert classify([{"version_id": 5}], expected_version_id=5) == "history-summary-invalid-metadata"
    assert classify([], expected_version_id=5) == "history-summary-malformed"

    attestation = RulesetWriteAttestation(
        ruleset_id=7,
        marker_version_id=10,
        version_id=11,
        current_updated_at="canonical-current",
        state_digest="digest",
    )

    calls = []
    def no_reads(url, headers):
        calls.append(url)
        return 500, {}
    def reject_before_reads(fake):
        def latest(repository, ruleset_id, current_detail):
            return None
        return latest
    verifier = _detailed_verifier(reject_before_reads, no_reads, attestation)
    assert verifier._latest_version_state(
        "DREAM-XIN/ai-sdlc",
        7,
        {"updated_at": "different", "rules": [{"type": "creation"}, {"type": "update"}]},
    ) is None
    assert verifier.protection_diagnostic_categories() == (
        "version-proof-current-updated-at-rejected",
    )
    assert calls == []

    responses = {
        "https://api.github.com/repos/DREAM-XIN/ai-sdlc/rulesets/7/history?per_page=1&page=1": (
            200,
            [{"version_id": 11, "updated_at": "history-summary"}],
        ),
        "https://api.github.com/repos/DREAM-XIN/ai-sdlc/rulesets/7/history/11": (503, {}),
    }
    def endpoint_get(url, headers):
        return responses[url]
    def exact_version_failure(fake):
        def latest(repository, ruleset_id, current_detail):
            history_url = (
                "https://api.github.com/repos/DREAM-XIN/ai-sdlc/rulesets/7/history"
                "?per_page=1&page=1"
            )
            fake.http_get(history_url, {})
            fake.http_get(
                "https://api.github.com/repos/DREAM-XIN/ai-sdlc/rulesets/7/history/11",
                {},
            )
            return None
        return latest
    verifier = _detailed_verifier(exact_version_failure, endpoint_get, attestation)
    assert verifier._latest_version_state(
        "DREAM-XIN/ai-sdlc",
        7,
        {
            "updated_at": "canonical-current",
            "rules": [{"type": "creation"}, {"type": "update"}],
        },
    ) is None
    assert verifier.protection_diagnostic_categories() == (
        "version-proof-exact-version-unavailable",
    )

    tree = ast.parse(textwrap.dedent(inspect.getsource(GitHubRepositoryProtectionVerifier.verify)))
    verify_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "verify"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
        for call in verify_calls
    )

    source = inspect.getsource(GitHubRepositoryProtectionVerifier)
    assert "raw_values_retained" in source
    assert "secret" not in source.lower()
    detailed_source = inspect.getsource(
        VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner
    )
    assert "return resolved" in detailed_source
    assert "raw_values" not in detailed_source
    print("v0.3 trusted protection rejection diagnostic validation passed")


if __name__ == "__main__":
    main()
