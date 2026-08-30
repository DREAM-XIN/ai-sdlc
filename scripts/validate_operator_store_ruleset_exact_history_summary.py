#!/usr/bin/env python3
"""Regression for Issue #357 exact-version history-summary replica settling."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_causal_summary import (
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)
from validate_operator_store_ruleset_canonical_generation_binding import (
    APP_ID,
    CANONICAL_VERSION,
    CURRENT_TS,
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
    return CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="exact-summary-test-token",
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


def is_latest_history(url: str) -> bool:
    parsed = urlparse(url)
    expected = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history"
    return parsed.path == expected and parsed.query in {"", "per_page=1&page=1"}


def is_exact_version(url: str) -> bool:
    return urlparse(url).path == (
        f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history/{CANONICAL_VERSION}"
    )


def exact_summary(updated_at: str, *, version_id: int = CANONICAL_VERSION):
    return 200, [{"version_id": version_id, "updated_at": updated_at}]


def current_and_proof(p):
    verifier = p.protection_verifier()
    detail_url = (
        f"https://api.github.com/repos/{REPOSITORY}/rulesets/"
        f"{RULESET_ID}?includes_parents=true"
    )
    status, current = verifier.http_get(detail_url, {})
    require(status == 200, "current detail became unreadable")
    return verifier, current


def main() -> None:
    # Initial exact-version metadata flips A -> B, then B stabilizes.  The final
    # reread first sees C, but GitHub subsequently returns the already-bound B.
    # The inherited equality fence therefore closes using only real responses.
    converging_api = CanonicalReplicaApi()
    converging = provisioner(converging_api, attempts=4)
    converging._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    original = converging_api.request
    calls = 0
    sequence = ["summary-a", "summary-b", "summary-b", "summary-c", "summary-b"]

    def converging_request(method, url, headers, body=None):
        nonlocal calls
        if method == "GET" and is_latest_history(url):
            value = sequence[min(calls, len(sequence) - 1)]
            calls += 1
            return exact_summary(value)
        return original(method, url, headers, body)

    converging.http_request = converging_request
    verifier, current = current_and_proof(converging)
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is not None,
        "same-version summary replica did not reconverge to the bound real observation",
    )
    require(calls == 5, "exact-summary convergence did not exercise initial and final settling")

    # The initial A summary stabilizes, but every final reread stays on B.  The
    # wrapper must exhaust the exact bound and return the last real B row so the
    # inherited initial/final updated_at equality fence rejects it.
    stale_api = CanonicalReplicaApi()
    stale = provisioner(stale_api, attempts=3)
    stale._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    stale_original = stale_api.request
    stale_calls = 0

    def stale_request(method, url, headers, body=None):
        nonlocal stale_calls
        if method == "GET" and is_latest_history(url):
            stale_calls += 1
            return exact_summary("summary-a" if stale_calls <= 2 else "summary-b")
        return stale_original(method, url, headers, body)

    stale.http_request = stale_request
    stale_verifier, stale_current = current_and_proof(stale)
    require(
        stale_verifier._latest_version_state(REPOSITORY, RULESET_ID, stale_current) is None,
        "permanent exact-version metadata drift incorrectly gained protection authority",
    )
    require(stale_calls == 5, "permanent summary drift did not honor the exact retry bound")

    # A newer generation during the final replay is an authority change, not
    # metadata lag.  #355 returns it immediately and this layer must not wait for
    # the old process-local generation to reappear.
    newer_api = CanonicalReplicaApi()
    newer = provisioner(newer_api, attempts=5)
    newer._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    newer_original = newer_api.request
    newer_calls = 0

    def newer_request(method, url, headers, body=None):
        nonlocal newer_calls
        if method == "GET" and is_latest_history(url):
            newer_calls += 1
            if newer_calls <= 2:
                return exact_summary("summary-a")
            return exact_summary("newer", version_id=CANONICAL_VERSION + 1)
        return newer_original(method, url, headers, body)

    newer.http_request = newer_request
    newer_verifier, newer_current = current_and_proof(newer)
    require(
        newer_verifier._latest_version_state(REPOSITORY, RULESET_ID, newer_current) is None,
        "newer generation incorrectly reused the bound exact-version summary",
    )
    require(newer_calls == 3, "newer generation was incorrectly treated as summary lag")

    # Unavailable history is terminal and never converted into the expected
    # exact summary.
    unavailable_api = CanonicalReplicaApi()
    unavailable = provisioner(unavailable_api, attempts=4)
    unavailable._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    unavailable_original = unavailable_api.request
    unavailable_calls = 0

    def unavailable_request(method, url, headers, body=None):
        nonlocal unavailable_calls
        if method == "GET" and is_latest_history(url):
            unavailable_calls += 1
            return 503, {}
        return unavailable_original(method, url, headers, body)

    unavailable.http_request = unavailable_request
    unavailable_verifier, unavailable_current = current_and_proof(unavailable)
    require(
        unavailable_verifier._latest_version_state(
            REPOSITORY, RULESET_ID, unavailable_current
        ) is None,
        "unavailable history incorrectly gained protection authority",
    )
    require(unavailable_calls == 1, "unavailable history was incorrectly retried as metadata lag")

    # Exact-version history with a missing updated_at is a subtle malformed case:
    # the inherited initial/final equality fence alone would otherwise see
    # None == None.  The trusted summary layer must mark that real observation
    # invalid and force the overall proof to remain non-PROTECTED without
    # fabricating any replacement metadata.
    malformed_api = CanonicalReplicaApi()
    malformed = provisioner(malformed_api, attempts=3)
    malformed._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    malformed_original = malformed_api.request
    malformed_calls = 0

    def malformed_request(method, url, headers, body=None):
        nonlocal malformed_calls
        if method == "GET" and is_latest_history(url):
            malformed_calls += 1
            return 200, [{"version_id": CANONICAL_VERSION}]
        return malformed_original(method, url, headers, body)

    malformed.http_request = malformed_request
    malformed_verifier, malformed_current = current_and_proof(malformed)
    require(
        malformed_verifier._latest_version_state(
            REPOSITORY, RULESET_ID, malformed_current
        ) is None,
        "missing exact-history updated_at incorrectly gained protection authority",
    )
    require(malformed_calls == 2, "malformed exact summary was rewritten or unexpectedly retried")

    # Existing current-detail updated_at authority remains exact.  Summary
    # settling must not rescue a different current generation.
    current_drift_api = CanonicalReplicaApi()
    current_drift = provisioner(current_drift_api, attempts=3)
    current_drift._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    current_original = current_drift_api.request

    def current_drift_request(method, url, headers, body=None):
        status, value = current_original(method, url, headers, body)
        if (
            method == "GET"
            and isinstance(value, dict)
            and urlparse(url).path
            == f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        ):
            value = copy.deepcopy(value)
            value["updated_at"] = "2026-08-25T12:10:09.999Z"
        return status, value

    current_drift.http_request = current_drift_request
    current_verifier, current_value = current_and_proof(current_drift)
    require(
        current_value.get("updated_at") != CURRENT_TS,
        "current-drift fixture did not change updated_at",
    )
    require(
        current_verifier._latest_version_state(REPOSITORY, RULESET_ID, current_value) is None,
        "current-detail updated_at drift incorrectly gained protection authority",
    )

    # Exact version-state content drift also remains terminal.  The causal
    # history-state normalizer may only normalize replica source/rules omission;
    # it cannot repair a changed identity field or state digest.
    state_drift_api = CanonicalReplicaApi()
    state_drift = provisioner(state_drift_api, attempts=3)
    state_drift._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    state_original = state_drift_api.request

    def state_drift_request(method, url, headers, body=None):
        status, value = state_original(method, url, headers, body)
        if method == "GET" and is_exact_version(url) and isinstance(value, dict):
            value = copy.deepcopy(value)
            state = value.get("state")
            if isinstance(state, dict):
                state["enforcement"] = "evaluate"
        return status, value

    state_drift.http_request = state_drift_request
    state_verifier, state_current = current_and_proof(state_drift)
    require(
        state_verifier._latest_version_state(REPOSITORY, RULESET_ID, state_current) is None,
        "exact version-state drift incorrectly gained protection authority",
    )

    print("v0.3 exact-version history-summary settling: PASS")


if __name__ == "__main__":
    main()
