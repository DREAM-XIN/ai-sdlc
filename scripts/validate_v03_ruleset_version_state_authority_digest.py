#!/usr/bin/env python3
"""Regression for trusted exact-version authority-field digest binding."""
from __future__ import annotations

import copy

from operator_store_github_ruleset_attested import (
    AttestedGitHubRulesetProtectionVerifier,
    RulesetWriteAttestation,
    _state_digest,
)
from operator_store_github_ruleset_causal_current import (
    CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner,
    _authority_state,
    _canonical_writer_authority_state,
)
from operator_store_github_ruleset_provision import writer_ruleset_payload

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
VERSION_ID = 46402203
MARKER_VERSION_ID = 46402202
UPDATED_AT = "2026-09-02T04:52:26Z"
HISTORY_UPDATED_AT = "2026-09-02T04:52:25Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def canonical_state() -> dict:
    return _canonical_writer_authority_state(
        REPOSITORY,
        RULESET_ID,
        STATE_REF,
        APP_ID,
    )


def omission_state(*, extra_metadata: bool = True) -> dict:
    state = canonical_state()
    state["rules"] = [{"type": "creation"}, {"type": "update"}]
    if extra_metadata:
        state["created_at"] = "2026-09-02T04:52:25Z"
        state["updated_at"] = "2026-09-02T04:52:25Z"
    return state


def current_detail() -> dict:
    state = omission_state(extra_metadata=False)
    state["updated_at"] = UPDATED_AT
    return state


class Api:
    def __init__(self, state: dict, current: dict):
        self.state = copy.deepcopy(state)
        self.current = copy.deepcopy(current)

    def request(self, method, url, headers, body=None):
        if method != "GET":
            return 405, {}
        if url.endswith("/history?per_page=1&page=1"):
            return 200, [{"version_id": VERSION_ID, "updated_at": HISTORY_UPDATED_AT}]
        if url.endswith(f"/history/{VERSION_ID}"):
            return 200, {
                "version_id": VERSION_ID,
                "updated_at": HISTORY_UPDATED_AT,
                "state": copy.deepcopy(self.state),
            }
        if url.endswith(f"/rulesets/{RULESET_ID}?includes_parents=true"):
            return 200, copy.deepcopy(self.current)
        return 404, {}


def verifier_for(state: dict):
    current = current_detail()
    attestation = RulesetWriteAttestation(
        ruleset_id=RULESET_ID,
        marker_version_id=MARKER_VERSION_ID,
        version_id=VERSION_ID,
        current_updated_at=UPDATED_AT,
        state_digest=_state_digest(canonical_state()),
    )
    api = Api(state, current)
    provisioner = CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
    )
    provisioner.write_attestations = {RULESET_ID: attestation}
    return provisioner.protection_verifier(), current, api


def validate_observed_timestamp_metadata_does_not_change_policy_digest():
    state = omission_state(extra_metadata=True)
    require(
        _state_digest(_authority_state(canonical_state()))
        == _state_digest(_authority_state({**canonical_state(), "updated_at": HISTORY_UPDATED_AT})),
        "observed timestamp metadata changed authority projection digest",
    )
    verifier, current, _ = verifier_for(state)
    resolved = verifier._latest_version_state(REPOSITORY, RULESET_ID, current)
    require(resolved is not None, "bounded timestamp-only history drift was rejected")
    resolved_state, proof = resolved
    require(resolved_state == canonical_state(), "resolved state was not exact authority projection")
    require(proof["version_id"] == VERSION_ID, "exact history generation binding was lost")


def validate_unknown_or_malformed_metadata_fails_closed():
    unknown = omission_state(extra_metadata=True)
    unknown["future_protection_semantics"] = {"enabled": True}
    verifier, current, _ = verifier_for(unknown)
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "unknown exact-version state key was silently projected away",
    )

    malformed = omission_state(extra_metadata=True)
    malformed["updated_at"] = {"unexpected": "shape"}
    verifier, current, _ = verifier_for(malformed)
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "malformed admitted timestamp metadata was accepted",
    )


def validate_authoritative_drift_remains_rejected():
    drift = omission_state(extra_metadata=True)
    drift["enforcement"] = "disabled"
    verifier, current, _ = verifier_for(drift)
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "authoritative enforcement drift was accepted",
    )

    permissive = omission_state(extra_metadata=True)
    permissive["rules"][1]["parameters"] = {"update_allows_fetch_and_merge": True}
    verifier, current, _ = verifier_for(permissive)
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "permissive update rule was accepted",
    )

    extra_rule = omission_state(extra_metadata=True)
    extra_rule["rules"].append({"type": "deletion"})
    verifier, current, _ = verifier_for(extra_rule)
    require(
        verifier._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "extra protection rule was accepted",
    )


def validate_generic_attested_verifier_remains_strict():
    state = omission_state(extra_metadata=True)
    current = current_detail()
    attestation = RulesetWriteAttestation(
        ruleset_id=RULESET_ID,
        marker_version_id=MARKER_VERSION_ID,
        version_id=VERSION_ID,
        current_updated_at=UPDATED_AT,
        state_digest=_state_digest(canonical_state()),
    )
    api = Api(state, current)
    generic = AttestedGitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=lambda url, headers: api.request("GET", url, headers),
        write_attestations={RULESET_ID: attestation},
    )
    require(
        generic._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "generic attested verifier gained trusted projection authority",
    )


def validate_canonical_projection_matches_submitted_writer():
    payload = writer_ruleset_payload(STATE_REF, APP_ID)
    projected = canonical_state()
    require(projected["name"] == payload["name"], "writer name projection drifted")
    require(projected["rules"] == payload["rules"], "writer rule projection drifted")
    require(projected["source"] == REPOSITORY, "repository authority projection drifted")


def main():
    validate_observed_timestamp_metadata_does_not_change_policy_digest()
    validate_unknown_or_malformed_metadata_fails_closed()
    validate_authoritative_drift_remains_rejected()
    validate_generic_attested_verifier_remains_strict()
    validate_canonical_projection_matches_submitted_writer()
    print("v0.3 ruleset version-state authority-digest validation passed")


if __name__ == "__main__":
    main()
