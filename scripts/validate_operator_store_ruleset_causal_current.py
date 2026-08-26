#!/usr/bin/env python3
"""Regression for #263 persistent replica-opaque current ruleset source."""
from __future__ import annotations

from operator_store_github_ruleset_causal_current import (
    CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_provision import RulesetProvisioningError
from validate_operator_store_ruleset_canonical_generation_binding import (
    APP_ID,
    CANONICAL_VERSION,
    CURRENT_TS,
    NONCE,
    OPAQUE_SOURCE,
    REPOSITORY,
    RULESET_ID,
    STATE_REF,
    CanonicalReplicaApi,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def provisioner(api: CanonicalReplicaApi):
    return CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="causal-current-test-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: NONCE,
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        final_current_settling_attempts=2,
        attestation_interval_seconds=0,
    )


def expect_failure(api: CanonicalReplicaApi, label: str) -> None:
    try:
        provisioner(api)._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    except RulesetProvisioningError:
        return
    raise AssertionError(f"{label} unexpectedly gained causal current authority")


def main() -> None:
    # Exact live failure shape from run 32935176323: canonical generation is
    # strictly newer, but the admin-token current-detail view keeps a stable
    # opaque repository source and omission-only writer rules.
    opaque = CanonicalReplicaApi(canonical_current_source=OPAQUE_SOURCE)
    p = provisioner(opaque)
    writer_id = p._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    require(writer_id == RULESET_ID, "causal current binding changed ruleset id")
    require(
        p._opaque_current_bindings.get(RULESET_ID) == (OPAQUE_SOURCE, CURRENT_TS),
        "stable opaque current observation was not process-bound",
    )
    attestation = p.write_attestations[RULESET_ID]
    require(attestation.version_id == CANONICAL_VERSION, "canonical generation drifted")

    verifier = p.protection_verifier()
    detail_url = f"https://api.github.com/repos/{REPOSITORY}/rulesets/{RULESET_ID}?includes_parents=true"
    status, current = verifier.http_get(detail_url, {})
    require(status == 200, "normalized current detail was unreadable")
    require(current.get("source") == REPOSITORY, "causal verifier did not normalize exact bound source")
    require(current.get("updated_at") == CURRENT_TS, "causal verifier changed current timestamp")
    proof = verifier._latest_version_state(REPOSITORY, RULESET_ID, current)
    require(proof is not None, "causal verifier could not revalidate exact history/current proof")

    # The opaque token is only a replay fence.  A later different token must not
    # be silently normalized under the old process-local attestation.
    opaque.canonical_current_source = "replica:different-source-token"
    _, changed = verifier.http_get(detail_url, {})
    require(
        changed.get("source") == "replica:different-source-token",
        "changed opaque source was incorrectly normalized",
    )
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, changed) is None,
        "changed opaque source retained protection authority",
    )

    # Any drift outside source + omission-only rules remains fail-closed.
    expect_failure(
        CanonicalReplicaApi(
            canonical_current_source=OPAQUE_SOURCE,
            canonical_current_target_drift=True,
        ),
        "target drift",
    )
    expect_failure(
        CanonicalReplicaApi(canonical_current_source=None),
        "malformed current source",
    )

    print("v0.3 causal opaque current-source binding: PASS")


if __name__ == "__main__":
    main()
