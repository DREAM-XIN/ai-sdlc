#!/usr/bin/env python3
"""Deterministic regression for trusted v0.3 causal protection wiring."""
from __future__ import annotations

from operator_store_github_protection_v03_trusted import (
    GitHubRepositoryProtectionVerifier,
    _PROCESS_ATTESTED_RULESET_VERIFIERS,
)
from operator_store_protection import PROTECTED, UNPROTECTED, ProtectionReceipt
from validate_operator_store_ruleset_history_semantic_normalization import (
    validate_attested_verifier_binds_normalized_state_to_exact_version_and_current,
    validate_normalization_is_trusted_write_scoped,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
APP_ID = 4576406


def require(value, message):
    if not value:
        raise AssertionError(message)


class FakeBranchVerifier:
    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=UNPROTECTED,
            verifier_identity="fake-classic",
            verified_at="2026-08-17T00:00:00Z",
            policy_digest=None,
        )


class FakeAttestedRulesetVerifier:
    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity=f"github-ruleset:integration:{APP_ID}",
            verified_at="2026-08-17T00:00:01Z",
            policy_digest="a" * 64,
        )


class FakeProvisioner:
    instances = []

    def __init__(self, **kwargs):
        require(kwargs["admin_token"] == "trusted-admin", "trusted token binding drifted")
        require(kwargs["operator_app_id"] == APP_ID, "Operator app id binding drifted")
        self.ensure_calls = []
        self.protection_calls = 0
        self.__class__.instances.append(self)

    def ensure_rulesets(self, repository, state_ref):
        self.ensure_calls.append((repository, state_ref))
        return 101, 102

    def protection_verifier(self):
        self.protection_calls += 1
        return FakeAttestedRulesetVerifier()


def verifier():
    candidate = GitHubRepositoryProtectionVerifier(
        token="trusted-admin",
        operator_app_slug="runtime-app",
        operator_app_id=APP_ID,
        provisioner_factory=FakeProvisioner,
    )
    candidate.branch = FakeBranchVerifier()
    return candidate


def validate_process_local_attestation_reuse():
    _PROCESS_ATTESTED_RULESET_VERIFIERS.clear()
    FakeProvisioner.instances.clear()

    first = verifier().verify(REPOSITORY, STATE_REF)
    require(first.status == PROTECTED, "trusted causal verifier did not produce PROTECTED")
    require(len(FakeProvisioner.instances) == 1, "first proof did not create exactly one provisioner")
    provisioner = FakeProvisioner.instances[0]
    require(
        provisioner.ensure_calls == [(REPOSITORY, STATE_REF)],
        "first proof did not establish exact repository/ref causal attestation",
    )
    require(provisioner.protection_calls == 1, "first proof built wrong attested verifier count")

    second = verifier().verify(REPOSITORY, STATE_REF)
    require(second.status == PROTECTED, "reused trusted causal verifier did not remain PROTECTED")
    require(
        len(FakeProvisioner.instances) == 1,
        "same-process repeated proof re-provisioned rulesets and changed generation",
    )
    require(
        second.policy_digest == first.policy_digest,
        "same-process repeated proof changed protection digest",
    )

    other_ref = "refs/heads/other-state"
    verifier().verify(REPOSITORY, other_ref)
    require(
        len(FakeProvisioner.instances) == 2,
        "different state ref incorrectly reused causal attestation",
    )


def validate_no_generic_normalization_widening():
    # Re-run the #262 semantic boundary directly. These checks prove that exact
    # omission normalization is scoped to the stabilized trusted write and that
    # the generic attested verifier still rejects the same omission state.
    validate_normalization_is_trusted_write_scoped()
    validate_attested_verifier_binds_normalized_state_to_exact_version_and_current()


def main():
    validate_process_local_attestation_reuse()
    validate_no_generic_normalization_widening()
    print("PASS: trusted v0.3 causal protection is process-local, reusable, and fail-closed")


if __name__ == "__main__":
    main()
