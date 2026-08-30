#!/usr/bin/env python3
"""Regression for #263 exact-history replica-metadata causal binding."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_causal_summary import (
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from validate_operator_store_ruleset_canonical_generation_binding import (
    APP_ID,
    CANONICAL_VERSION,
    NONCE,
    REPOSITORY,
    RULESET_ID,
    STATE_REF,
    CanonicalReplicaApi,
)

SUMMARY_REPLICA_TS = "2026-08-30T12:54:39Z"
VERSION_REPLICA_TS = "2026-08-30T12:54:40Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def is_latest_history(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.path == f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history"
        and parsed.query in {"", "per_page=1&page=1"}
    )


def is_exact_version(url: str) -> bool:
    return urlparse(url).path == (
        f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history/{CANONICAL_VERSION}"
    )


def is_current_detail(url: str) -> bool:
    return urlparse(url).path == f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"


def provisioner(api: CanonicalReplicaApi, *, attempts: int = 3):
    return CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="history-metadata-test-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: NONCE,
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        final_current_settling_attempts=2,
        protection_history_settling_attempts=attempts,
        exact_history_summary_settling_attempts=attempts,
        attestation_interval_seconds=0,
    )


def current_detail(verifier):
    url = f"https://api.github.com/repos/{REPOSITORY}/rulesets/{RULESET_ID}?includes_parents=true"
    status, value = verifier.http_get(url, {})
    require(status == 200 and isinstance(value, dict), "current detail fixture is unreadable")
    return value


def main() -> None:
    # Live #263 shape: current detail is already causally attested, while both
    # exact latest-history summary and exact history-version detail expose
    # replica timestamps that differ from current. The trusted wrapper may
    # normalize those timestamp fields only after stable exact-version and exact
    # state-digest proof; the unchanged generic verifier must still reject it.
    api = CanonicalReplicaApi()
    p = provisioner(api)
    p._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    original = api.request

    def replica_request(method, url, headers, body=None):
        status, value = original(method, url, headers, body)
        if method != "GET":
            return status, value
        if is_latest_history(url) and status == 200 and isinstance(value, list) and value:
            value = copy.deepcopy(value)
            value[0]["updated_at"] = SUMMARY_REPLICA_TS
        elif is_exact_version(url) and status == 200 and isinstance(value, dict):
            value = copy.deepcopy(value)
            value["updated_at"] = VERSION_REPLICA_TS
        return status, value

    p.http_request = replica_request
    trusted = p.protection_verifier()
    current = current_detail(trusted)
    require(
        trusted._latest_version_state(REPOSITORY, RULESET_ID, current) is not None,
        "stable exact history replica metadata did not bind to attested current authority",
    )

    generic = GitHubRulesetProtectionVerifier(
        token="generic-read-only-token",
        operator_app_id=APP_ID,
        http_get=lambda url, headers: replica_request("GET", url, headers, None),
    )
    generic_status, generic_current = replica_request(
        "GET",
        f"https://api.github.com/repos/{REPOSITORY}/rulesets/{RULESET_ID}?includes_parents=true",
        {},
        None,
    )
    require(generic_status == 200 and isinstance(generic_current, dict), "generic fixture missing current")
    require(
        generic._latest_version_state(REPOSITORY, RULESET_ID, generic_current) is None,
        "generic/read-only verifier authority was widened by trusted metadata binding",
    )

    # Exact-version detail metadata must itself be stable. A -> B is not a
    # causal replay fence and cannot be normalized into current authority.
    unstable_api = CanonicalReplicaApi()
    unstable = provisioner(unstable_api)
    unstable._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    unstable_original = unstable_api.request
    detail_calls = 0

    def unstable_request(method, url, headers, body=None):
        nonlocal detail_calls
        status, value = unstable_original(method, url, headers, body)
        if method == "GET" and is_latest_history(url) and isinstance(value, list) and value:
            value = copy.deepcopy(value)
            value[0]["updated_at"] = SUMMARY_REPLICA_TS
        elif method == "GET" and is_exact_version(url) and isinstance(value, dict):
            detail_calls += 1
            value = copy.deepcopy(value)
            value["updated_at"] = "version-a" if detail_calls == 1 else "version-b"
        return status, value

    unstable.http_request = unstable_request
    unstable_verifier = unstable.protection_verifier()
    unstable_current = current_detail(unstable_verifier)
    require(
        unstable_verifier._latest_version_state(REPOSITORY, RULESET_ID, unstable_current) is None,
        "unstable exact-version metadata incorrectly gained trusted authority",
    )

    # Exact state digest remains the authority anchor; matching version ids and
    # stable timestamps cannot repair a changed state.
    drift_api = CanonicalReplicaApi()
    drift = provisioner(drift_api)
    drift._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    drift_original = drift_api.request

    def drift_request(method, url, headers, body=None):
        status, value = drift_original(method, url, headers, body)
        if method == "GET" and is_latest_history(url) and isinstance(value, list) and value:
            value = copy.deepcopy(value)
            value[0]["updated_at"] = SUMMARY_REPLICA_TS
        elif method == "GET" and is_exact_version(url) and isinstance(value, dict):
            value = copy.deepcopy(value)
            value["updated_at"] = VERSION_REPLICA_TS
            state = value.get("state")
            if isinstance(state, dict):
                state["enforcement"] = "evaluate"
        return status, value

    drift.http_request = drift_request
    drift_verifier = drift.protection_verifier()
    drift_current = current_detail(drift_verifier)
    require(
        drift_verifier._latest_version_state(REPOSITORY, RULESET_ID, drift_current) is None,
        "state-digest drift incorrectly gained trusted authority",
    )

    # A newer history generation is an authority change, never replica metadata
    # lag, even if its timestamp is stable.
    newer_api = CanonicalReplicaApi()
    newer = provisioner(newer_api)
    newer._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    newer_original = newer_api.request

    def newer_request(method, url, headers, body=None):
        status, value = newer_original(method, url, headers, body)
        if method == "GET" and is_latest_history(url) and isinstance(value, list) and value:
            value = copy.deepcopy(value)
            value[0]["version_id"] = CANONICAL_VERSION + 1
            value[0]["updated_at"] = SUMMARY_REPLICA_TS
        return status, value

    newer.http_request = newer_request
    newer_verifier = newer.protection_verifier()
    newer_current = current_detail(newer_verifier)
    require(
        newer_verifier._latest_version_state(REPOSITORY, RULESET_ID, newer_current) is None,
        "newer history generation incorrectly reused process-local attestation",
    )

    print("v0.3 exact-history replica metadata causal binding: PASS")


if __name__ == "__main__":
    main()
