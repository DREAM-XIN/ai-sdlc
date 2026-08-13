#!/usr/bin/env python3
"""Regression for live omission-only ruleset-history semantic normalization."""
from __future__ import annotations

import copy

from operator_store_github_ruleset_attested import (
    AttestedGitHubRulesetProtectionVerifier,
    RulesetWriteAttestation,
    _state_digest,
)
from operator_store_github_ruleset_provision import writer_ruleset_payload
from operator_store_github_ruleset_stabilized import (
    NormalizedAttestedGitHubRulesetProtectionVerifier,
    StabilizedAttestedGitHubOperatorStoreRulesetProvisioner,
    _normalize_trusted_write_state,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
UPDATED_AT = "2026-08-13T06:20:00.123Z"
VERSION_ID = 46402203
MARKER_VERSION_ID = 46402202


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _canonical_payload(name="AI-SDLC Operator Store writer"):
    payload = writer_ruleset_payload(STATE_REF, APP_ID)
    payload["name"] = name
    return payload


def _state(payload: dict) -> dict:
    result = copy.deepcopy(payload)
    result.update({
        "id": RULESET_ID,
        "source_type": "Repository",
        "source": REPOSITORY,
    })
    return result


def _omission_state(payload: dict) -> dict:
    result = _state(payload)
    result["rules"] = [{"type": "creation"}, {"type": "update"}]
    return result


class HistoryOnlyApi:
    def __init__(self, state: dict, *, version_id: int = VERSION_ID):
        self.state = copy.deepcopy(state)
        self.version_id = version_id

    def request(self, method, url, headers, body=None):
        if method != "GET":
            return 405, {}
        if url.endswith("/history?per_page=1&page=1"):
            return 200, [{"version_id": self.version_id, "updated_at": "2026-08-13T06:20:00Z"}]
        if url.endswith(f"/history/{self.version_id}"):
            return 200, {
                "version_id": self.version_id,
                "updated_at": "2026-08-13T06:20:00Z",
                "state": copy.deepcopy(self.state),
            }
        return 404, {}


class VerifierApi:
    def __init__(self, state: dict, current: dict, *, final_version_id: int = VERSION_ID):
        self.state = copy.deepcopy(state)
        self.current = copy.deepcopy(current)
        self.final_version_id = final_version_id
        self.history_calls = 0

    def get(self, url, headers):
        if url.endswith("/history?per_page=1&page=1"):
            self.history_calls += 1
            version_id = VERSION_ID if self.history_calls == 1 else self.final_version_id
            return 200, [{"version_id": version_id, "updated_at": "2026-08-13T06:20:00Z"}]
        if url.endswith(f"/history/{VERSION_ID}"):
            return 200, {
                "version_id": VERSION_ID,
                "updated_at": "2026-08-13T06:20:00Z",
                "state": copy.deepcopy(self.state),
            }
        if f"/rulesets/{RULESET_ID}?includes_parents=true" in url:
            return 200, copy.deepcopy(self.current)
        return 404, {}


def _current_detail(payload: dict):
    result = _omission_state(payload)
    result["updated_at"] = UPDATED_AT
    return result


def validate_normalization_is_trusted_write_scoped():
    payload = _canonical_payload()
    omitted = _omission_state(payload)
    normalized = _normalize_trusted_write_state(
        omitted,
        repository=REPOSITORY,
        ruleset_id=RULESET_ID,
        payload=payload,
    )
    require(normalized == _state(payload), "exact live omission shape did not canonicalize")

    permissive = copy.deepcopy(omitted)
    permissive["rules"][1]["parameters"] = {"update_allows_fetch_and_merge": True}
    require(
        _normalize_trusted_write_state(
            permissive,
            repository=REPOSITORY,
            ruleset_id=RULESET_ID,
            payload=payload,
        ) is None,
        "explicit permissive update history was normalized as strict",
    )

    extra_rule = copy.deepcopy(omitted)
    extra_rule["rules"].append({"type": "deletion"})
    require(
        _normalize_trusted_write_state(
            extra_rule,
            repository=REPOSITORY,
            ruleset_id=RULESET_ID,
            payload=payload,
        ) is None,
        "history with an extra rule was normalized as strict",
    )

    non_strict_payload = _canonical_payload()
    non_strict_payload["rules"][1]["parameters"]["update_allows_fetch_and_merge"] = True
    require(
        _normalize_trusted_write_state(
            omitted,
            repository=REPOSITORY,
            ruleset_id=RULESET_ID,
            payload=non_strict_payload,
        ) is None,
        "normalization accepted a payload that did not explicitly write false",
    )


def validate_stabilized_attestation_accepts_only_exact_omission_shape():
    marker = _canonical_payload("AI-SDLC Operator Store writer [attest:aabbccdd]")
    omitted = _omission_state(marker)
    api = HistoryOnlyApi(omitted)
    provisioner = StabilizedAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: "aabbccdd",
        attestation_attempts=1,
        attestation_interval_seconds=0,
    )
    version_id, state = provisioner._wait_for_exact_history_state(
        REPOSITORY,
        RULESET_ID,
        marker,
    )
    require(version_id == VERSION_ID, "normalized marker attestation returned wrong version")
    require(state == _state(marker), "normalized marker attestation did not return strict canonical state")

    permissive = copy.deepcopy(omitted)
    permissive["rules"][1]["parameters"] = {"update_allows_fetch_and_merge": True}
    bad = StabilizedAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=HistoryOnlyApi(permissive).request,
        sleeper=lambda _: None,
        nonce_factory=lambda: "aabbccdd",
        attestation_attempts=1,
        attestation_interval_seconds=0,
    )
    try:
        bad._wait_for_exact_history_state(REPOSITORY, RULESET_ID, marker)
    except Exception:
        pass
    else:
        raise AssertionError("permissive history unexpectedly satisfied trusted write attestation")


def validate_attested_verifier_binds_normalized_state_to_exact_version_and_current():
    payload = _canonical_payload()
    canonical_state = _state(payload)
    omitted_state = _omission_state(payload)
    current = _current_detail(payload)
    attestation = RulesetWriteAttestation(
        ruleset_id=RULESET_ID,
        marker_version_id=MARKER_VERSION_ID,
        version_id=VERSION_ID,
        current_updated_at=UPDATED_AT,
        state_digest=_state_digest(canonical_state),
    )

    api = VerifierApi(omitted_state, current)
    verifier = NormalizedAttestedGitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=api.get,
        write_attestations={RULESET_ID: attestation},
    )
    result = verifier._latest_version_state(REPOSITORY, RULESET_ID, current)
    require(result is not None, "causally-attested omission history was not accepted")
    state, proof = result
    require(state == canonical_state, "verifier did not expose canonical strict history semantics")
    require(proof["version_id"] == VERSION_ID, "verifier proof lost exact version binding")

    generic_api = VerifierApi(omitted_state, current)
    generic = AttestedGitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=generic_api.get,
        write_attestations={RULESET_ID: attestation},
    )
    require(
        generic._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "generic attested verifier gained omission normalization authority",
    )

    permissive_state = copy.deepcopy(omitted_state)
    permissive_state["rules"][1]["parameters"] = {"update_allows_fetch_and_merge": True}
    permissive_api = VerifierApi(permissive_state, current)
    permissive = NormalizedAttestedGitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=permissive_api.get,
        write_attestations={RULESET_ID: attestation},
    )
    require(
        permissive._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "explicit true historical state was authorized",
    )

    drift_api = VerifierApi(omitted_state, current, final_version_id=VERSION_ID + 1)
    drift = NormalizedAttestedGitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=drift_api.get,
        write_attestations={RULESET_ID: attestation},
    )
    require(
        drift._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "post-attestation latest-version drift was authorized",
    )

    changed_current = copy.deepcopy(current)
    changed_current["updated_at"] = "2026-08-13T06:20:00.999Z"
    current_drift_api = VerifierApi(omitted_state, changed_current)
    current_drift = NormalizedAttestedGitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=current_drift_api.get,
        write_attestations={RULESET_ID: attestation},
    )
    require(
        current_drift._latest_version_state(REPOSITORY, RULESET_ID, changed_current) is None,
        "current-detail drift was authorized",
    )


def main():
    validate_normalization_is_trusted_write_scoped()
    validate_stabilized_attestation_accepts_only_exact_omission_shape()
    validate_attested_verifier_binds_normalized_state_to_exact_version_and_current()
    print("Operator Store ruleset history semantic-normalization validation passed")


if __name__ == "__main__":
    main()
