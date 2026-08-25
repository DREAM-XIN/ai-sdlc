#!/usr/bin/env python3
"""Regression for Issue #349 canonical writer generation/current binding."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_generation_bound import (
    GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_provision import (
    RULESET_WRITER_NAME,
    RulesetProvisioningError,
    writer_ruleset_payload,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
OTHER_REPOSITORY = "DREAM-XIN/cross-wire"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
BASELINE_VERSION = 800
MARKER_VERSION = 801
CANONICAL_VERSION = 802
NONCE = "a9c2d1e8f9b746b5a144c09e1f6ad321"
MARKER_NAME = f"{RULESET_WRITER_NAME} [attest:{NONCE}]"
OPAQUE_SOURCE = "replica:opaque-source-token"
RESPONSE_TS = "2026-08-25T12:10:02.456Z"
CURRENT_TS = "2026-08-25T12:10:03.789Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def omission_rules() -> list[dict]:
    return [{"type": "creation"}, {"type": "update"}]


def state(payload: dict, *, source: str, rules=None) -> dict:
    return {
        "id": RULESET_ID,
        "name": payload["name"],
        "target": payload["target"],
        "source_type": "Repository",
        "source": source,
        "enforcement": payload["enforcement"],
        "conditions": copy.deepcopy(payload["conditions"]),
        "bypass_actors": copy.deepcopy(payload["bypass_actors"]),
        "rules": copy.deepcopy(payload["rules"] if rules is None else rules),
    }


def response(payload: dict, *, source: str, updated_at: str, rules=None) -> dict:
    value = state(payload, source=source, rules=omission_rules() if rules is None else rules)
    value["updated_at"] = updated_at
    return value


class CanonicalReplicaApi:
    def __init__(
        self,
        *,
        canonical_version: int = CANONICAL_VERSION,
        canonical_response_source: object = OPAQUE_SOURCE,
        canonical_response_rules=None,
        canonical_current_source: object = REPOSITORY,
        canonical_current_target_drift: bool = False,
    ):
        self.phase = "baseline"
        self.marker_payload = None
        self.canonical_payload = None
        self.canonical_version = canonical_version
        self.canonical_response_source = canonical_response_source
        self.canonical_response_rules = canonical_response_rules
        self.canonical_current_source = canonical_current_source
        self.canonical_current_target_drift = canonical_current_target_drift
        self.put_calls = 0
        self.current_calls = 0

    def _baseline_payload(self):
        return writer_ruleset_payload(STATE_REF, APP_ID)

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        path = urlparse(url).path
        detail = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        history = f"{detail}/history"
        if not path.startswith(f"/repos/{REPOSITORY}/"):
            return 404, {}

        if method == "PUT" and path == detail:
            self.put_calls += 1
            payload = copy.deepcopy(body)
            if payload.get("name") == MARKER_NAME:
                self.marker_payload = payload
                self.phase = "marker"
                return 200, response(
                    payload,
                    source=OPAQUE_SOURCE,
                    updated_at="2026-08-25T12:10:01.123Z",
                )
            self.canonical_payload = payload
            self.phase = "canonical"
            value = response(
                payload,
                source=self.canonical_response_source,
                updated_at=RESPONSE_TS,
                rules=self.canonical_response_rules,
            )
            return 200, value

        if method == "GET" and path == detail:
            self.current_calls += 1
            if self.phase == "marker":
                value = state(self.marker_payload, source=OPAQUE_SOURCE, rules=omission_rules())
                value["updated_at"] = "2026-08-25T12:09:59.000Z"
                return 200, value
            if self.phase == "canonical":
                value = state(
                    self.canonical_payload,
                    source=self.canonical_current_source,
                    rules=omission_rules(),
                )
                if self.canonical_current_target_drift:
                    value["target"] = "tag"
                value["updated_at"] = CURRENT_TS
                return 200, value
            return 200, response(
                self._baseline_payload(),
                source=REPOSITORY,
                updated_at="2026-08-25T12:00:00Z",
            )

        if method == "GET" and path == history:
            if self.phase == "baseline":
                version_id = BASELINE_VERSION
            elif self.phase == "marker":
                version_id = MARKER_VERSION
            else:
                version_id = self.canonical_version
            return 200, [{"version_id": version_id, "updated_at": "replica-summary"}]

        if method == "GET" and path.startswith(history + "/"):
            try:
                version_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return 404, {}
            if self.phase == "baseline" and version_id == BASELINE_VERSION:
                payload = self._baseline_payload()
                history_state = state(payload, source=REPOSITORY, rules=omission_rules())
            elif self.phase == "marker" and version_id == MARKER_VERSION:
                history_state = state(self.marker_payload, source=OPAQUE_SOURCE, rules=omission_rules())
            elif self.phase == "canonical" and version_id == self.canonical_version:
                # Exact live #349 shape: canonical generation is strictly new and
                # otherwise exact, but history source/rules remain replica-opaque.
                history_state = state(
                    self.canonical_payload,
                    source=OPAQUE_SOURCE,
                    rules=omission_rules(),
                )
            else:
                return 404, {}
            return 200, {
                "version_id": version_id,
                "updated_at": "replica-version",
                "state": history_state,
            }
        return 404, {}


def provisioner(api: CanonicalReplicaApi):
    return GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="canonical-generation-test-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: NONCE,
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        attestation_interval_seconds=0,
    )


def expect_failure(api: CanonicalReplicaApi, label: str) -> None:
    try:
        provisioner(api)._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    except RulesetProvisioningError:
        return
    raise AssertionError(f"{label} unexpectedly gained canonical writer authority")


def main() -> None:
    live = CanonicalReplicaApi()
    p = provisioner(live)
    writer_id = p._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    require(writer_id == RULESET_ID, "canonical generation changed ruleset id")
    require(live.put_calls == 2, "marker/canonical sequence was not exactly two writes")
    attestation = p.write_attestations[RULESET_ID]
    require(attestation.marker_version_id == MARKER_VERSION, "marker generation drifted")
    require(attestation.version_id == CANONICAL_VERSION, "canonical generation drifted")
    require(
        attestation.current_updated_at == CURRENT_TS,
        "attestation timestamp was not bound to final exact current detail",
    )
    require(
        attestation.current_updated_at != RESPONSE_TS,
        "regression did not exercise PUT/current timestamp replica drift",
    )

    stale = CanonicalReplicaApi(canonical_version=MARKER_VERSION)
    expect_failure(stale, "canonical generation not strictly newer than marker")

    opaque_current = CanonicalReplicaApi(canonical_current_source=OPAQUE_SOURCE)
    expect_failure(opaque_current, "opaque canonical current detail")

    drifted_current = CanonicalReplicaApi(canonical_current_target_drift=True)
    expect_failure(drifted_current, "canonical current target drift")

    malformed_response_source = CanonicalReplicaApi(canonical_response_source=None)
    expect_failure(malformed_response_source, "malformed canonical PUT source")

    unsafe_response = CanonicalReplicaApi(
        canonical_response_rules=[{"type": "creation"}, {"type": "deletion"}],
    )
    expect_failure(unsafe_response, "unsafe canonical PUT rules")

    # No active marker->canonical process sequence may reuse the canonical
    # generation fallback.  Writing canonical directly leaves the sequencing
    # floor unset and must fail even if a strictly-new opaque history row exists.
    direct_api = CanonicalReplicaApi()
    direct = provisioner(direct_api)
    canonical_payload = writer_ruleset_payload(STATE_REF, APP_ID)
    direct_api.phase = "canonical"
    direct_api.canonical_payload = canonical_payload
    direct._pending_write_binding = (
        REPOSITORY,
        RULESET_ID,
        RULESET_WRITER_NAME,
        RESPONSE_TS,
        response(canonical_payload, source=OPAQUE_SOURCE, updated_at=RESPONSE_TS),
    )
    try:
        direct._wait_for_exact_history_state(
            REPOSITORY,
            RULESET_ID,
            canonical_payload,
            minimum_version_id=MARKER_VERSION,
        )
    except RulesetProvisioningError:
        pass
    else:
        raise AssertionError("canonical fallback escaped marker->canonical sequencing fence")

    cross_repo = CanonicalReplicaApi()
    try:
        provisioner(cross_repo)._attest_writer_ruleset(
            OTHER_REPOSITORY,
            RULESET_ID,
            STATE_REF,
        )
    except RulesetProvisioningError:
        pass
    else:
        raise AssertionError("cross-repository request unexpectedly gained writer authority")
    require(cross_repo.put_calls == 0, "cross-repository failure performed a write")

    print("v0.3 canonical writer strictly-new generation binding: PASS")


if __name__ == "__main__":
    main()
