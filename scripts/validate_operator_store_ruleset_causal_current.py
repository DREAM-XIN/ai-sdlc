#!/usr/bin/env python3
"""Regression for #263 replica-opaque current/history ruleset source."""
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

    # Exact-main run 32949638735 reached the protection verifier after the
    # marker/canonical sequence, but the final receipt was not PROTECTED.  A
    # canonical current-detail replica must not disable normalization of the
    # independently replica-opaque history state: the exact history generation
    # and canonical state digest are already causally attested in this process.
    canonical_current = CanonicalReplicaApi()
    canonical = provisioner(canonical_current)
    canonical._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    require(
        RULESET_ID not in canonical._opaque_current_bindings,
        "canonical current fixture unexpectedly created an opaque replay binding",
    )
    canonical_verifier = canonical.protection_verifier()
    status, current = canonical_verifier.http_get(detail_url, {})
    require(status == 200 and current.get("source") == REPOSITORY, "canonical current detail drifted")
    require(
        canonical_verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is not None,
        "causally attested opaque history incorrectly depended on an opaque current binding",
    )

    # Exact-main run 32965115049 completed the marker/canonical writes but still
    # failed before materialization.  The remaining replica race is a canonical
    # final attestation read followed by an opaque current-detail replica during
    # protection verification.  That late opaque observation may be normalized
    # only after two stable reads and only when its normalized state digest and
    # updated_at match the already-attested canonical generation exactly.
    late_flip_api = CanonicalReplicaApi()
    late_flip = provisioner(late_flip_api)
    late_flip._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    require(
        RULESET_ID not in late_flip._opaque_current_bindings,
        "late-flip fixture did not begin from canonical current authority",
    )
    late_flip_api.canonical_current_source = OPAQUE_SOURCE
    late_flip_verifier = late_flip.protection_verifier()
    calls_before = late_flip_api.current_calls
    status, late_current = late_flip_verifier.http_get(detail_url, {})
    require(status == 200, "late replica-flip current detail was unreadable")
    require(
        late_current.get("source") == REPOSITORY,
        "stable late opaque replica was not normalized to causally-attested repository",
    )
    require(
        late_flip_api.current_calls == calls_before + 2,
        "late opaque replica did not require an independent stable reread",
    )
    require(
        late_flip_verifier._latest_version_state(REPOSITORY, RULESET_ID, late_current) is not None,
        "late replica-flip current/history proof did not close",
    )

    # A late opaque observation with a different updated_at is not the attested
    # generation and must remain raw/fail closed.
    stale_flip_api = CanonicalReplicaApi()
    stale_flip = provisioner(stale_flip_api)
    stale_flip._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    stale_flip_api.canonical_current_source = OPAQUE_SOURCE
    original_request = stale_flip_api.request

    def stale_request(method, url, headers, body=None):
        status, value = original_request(method, url, headers, body)
        if method == "GET" and isinstance(value, dict) and value.get("source") == OPAQUE_SOURCE:
            value = dict(value)
            value["updated_at"] = "2026-08-25T12:10:04.999Z"
        return status, value

    stale_flip.http_request = stale_request
    stale_verifier = stale_flip.protection_verifier()
    _, stale_current = stale_verifier.http_get(detail_url, {})
    require(
        stale_current.get("source") == OPAQUE_SOURCE,
        "stale late opaque replica incorrectly gained repository normalization",
    )
    require(
        stale_verifier._latest_version_state(REPOSITORY, RULESET_ID, stale_current) is None,
        "stale late opaque replica retained protection authority",
    )

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

    print("v0.3 causal opaque current/history binding: PASS")


if __name__ == "__main__":
    main()
