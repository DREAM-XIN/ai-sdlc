#!/usr/bin/env python3
"""Regression for Issue #355 bounded same-process history replica settling."""
from __future__ import annotations

from urllib.parse import urlparse

from operator_store_github_ruleset_causal_history import (
    CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)
from validate_operator_store_ruleset_canonical_generation_binding import (
    APP_ID,
    CANONICAL_VERSION,
    CURRENT_TS,
    MARKER_VERSION,
    NONCE,
    REPOSITORY,
    RULESET_ID,
    STATE_REF,
    CanonicalReplicaApi,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def provisioner(api: CanonicalReplicaApi, *, attempts: int = 3):
    return CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="causal-history-test-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: NONCE,
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        final_current_settling_attempts=2,
        protection_history_settling_attempts=attempts,
        attestation_interval_seconds=0,
    )


def is_latest_history(url: str) -> bool:
    parsed = urlparse(url)
    expected = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history"
    return parsed.path == expected and parsed.query in {"", "per_page=1&page=1"}


def main() -> None:
    detail_url = (
        f"https://api.github.com/repos/{REPOSITORY}/rulesets/"
        f"{RULESET_ID}?includes_parents=true"
    )

    # The exact #355 live shape: marker->canonical write attestation has already
    # succeeded, but a later latest-history read temporarily returns an older
    # positive generation from another replica. Only the already-attested exact
    # canonical version may eventually close the proof.
    converging_api = CanonicalReplicaApi()
    converging = provisioner(converging_api, attempts=3)
    converging._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    attestation = converging.write_attestations[RULESET_ID]
    require(attestation.version_id == CANONICAL_VERSION, "canonical attestation drifted")
    require(attestation.marker_version_id == MARKER_VERSION, "marker attestation drifted")
    require(attestation.current_updated_at == CURRENT_TS, "current timestamp drifted")

    original = converging_api.request
    latest_calls = 0

    def converging_request(method, url, headers, body=None):
        nonlocal latest_calls
        if method == "GET" and is_latest_history(url):
            latest_calls += 1
            if latest_calls <= 2:
                return 200, [
                    {"version_id": MARKER_VERSION, "updated_at": "lagging-replica"}
                ]
        return original(method, url, headers, body)

    converging.http_request = converging_request
    verifier = converging.protection_verifier()
    status, current = verifier.http_get(detail_url, {})
    require(status == 200, "current detail became unreadable")
    proof = verifier._latest_version_state(REPOSITORY, RULESET_ID, current)
    require(proof is not None, "older history replica did not settle to exact attested version")
    require(latest_calls >= 3, "older history replica did not require bounded rereads")

    # A permanently stale history replica never gains protection authority. The
    # wrapper must exhaust its exact bound and return the last real stale row;
    # it must not synthesize the process-local expected version.
    stale_api = CanonicalReplicaApi()
    stale = provisioner(stale_api, attempts=3)
    stale._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    stale_original = stale_api.request
    stale_calls = 0

    def stale_request(method, url, headers, body=None):
        nonlocal stale_calls
        if method == "GET" and is_latest_history(url):
            stale_calls += 1
            return 200, [
                {"version_id": MARKER_VERSION, "updated_at": "permanent-stale-replica"}
            ]
        return stale_original(method, url, headers, body)

    stale.http_request = stale_request
    stale_verifier = stale.protection_verifier()
    status, stale_current = stale_verifier.http_get(detail_url, {})
    require(status == 200, "stale current detail became unreadable")
    require(
        stale_verifier._latest_version_state(REPOSITORY, RULESET_ID, stale_current) is None,
        "permanently stale history replica incorrectly gained protection authority",
    )
    require(stale_calls == 3, "permanent stale history did not honor exact retry bound")

    # A newer version is not replica lag; it is an authority change. Reject it
    # immediately and never wait for it to flip back to the stale process-local
    # attestation.
    newer_api = CanonicalReplicaApi()
    newer = provisioner(newer_api, attempts=5)
    newer._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    newer_original = newer_api.request
    newer_calls = 0

    def newer_request(method, url, headers, body=None):
        nonlocal newer_calls
        if method == "GET" and is_latest_history(url):
            newer_calls += 1
            return 200, [
                {"version_id": CANONICAL_VERSION + 1, "updated_at": "newer-authority"}
            ]
        return newer_original(method, url, headers, body)

    newer.http_request = newer_request
    newer_verifier = newer.protection_verifier()
    status, newer_current = newer_verifier.http_get(detail_url, {})
    require(status == 200, "newer current detail became unreadable")
    require(
        newer_verifier._latest_version_state(REPOSITORY, RULESET_ID, newer_current) is None,
        "newer history generation incorrectly reused stale process-local authority",
    )
    require(newer_calls == 1, "newer history generation was incorrectly treated as replica lag")

    print("v0.3 causal history replica settling: PASS")


if __name__ == "__main__":
    main()
