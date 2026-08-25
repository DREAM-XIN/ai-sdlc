#!/usr/bin/env python3
"""Regression for Issue #346 replica-variant marker PUT source binding."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_generation_bound import (
    GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner,
    _marker_write_response_binds_submitted_generation,
)
from operator_store_github_ruleset_provision import (
    RULESET_WRITER_NAME,
    RulesetProvisioningError,
    writer_ruleset_payload,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
BASELINE_VERSION = 700
MARKER_VERSION = 701
CANONICAL_VERSION = 702
NONCE = "512c37ed336c929168fcf7a8a6664d94"
MARKER_NAME = f"{RULESET_WRITER_NAME} [attest:{NONCE}]"
OPAQUE_SOURCE = "replica:opaque-source-token"


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


def marker_response(payload: dict, *, source=OPAQUE_SOURCE) -> dict:
    value = state(payload, source=source, rules=omission_rules())
    value["updated_at"] = "2026-08-25T06:50:01.123Z"
    return value


class ReplicaVariantWriteApi:
    def __init__(self, *, marker_version: int = MARKER_VERSION, response_source=OPAQUE_SOURCE):
        self.phase = "baseline"
        self.marker_version = marker_version
        self.response_source = response_source
        self.marker_payload = None
        self.canonical_payload = None
        self.put_calls = 0

    def _baseline_payload(self):
        return writer_ruleset_payload(STATE_REF, APP_ID)

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        path = urlparse(url).path
        detail = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        history = f"{detail}/history"

        if method == "PUT" and path == detail:
            self.put_calls += 1
            payload = copy.deepcopy(body)
            if payload.get("name") == MARKER_NAME:
                self.phase = "marker"
                self.marker_payload = payload
                return 200, marker_response(payload, source=self.response_source)
            self.phase = "canonical"
            self.canonical_payload = payload
            value = state(payload, source=REPOSITORY, rules=omission_rules())
            value["updated_at"] = "2026-08-25T06:50:02.456Z"
            return 200, value

        if method == "GET" and path == detail:
            if self.phase == "marker":
                value = state(self.marker_payload, source=OPAQUE_SOURCE, rules=omission_rules())
                value["updated_at"] = "2026-08-25T06:49:59.000Z"
                return 200, value
            if self.phase == "canonical":
                value = state(self.canonical_payload, source=REPOSITORY, rules=omission_rules())
                value["updated_at"] = "2026-08-25T06:50:02.456Z"
                return 200, value
            value = state(self._baseline_payload(), source=REPOSITORY, rules=omission_rules())
            value["updated_at"] = "2026-08-25T06:40:00Z"
            return 200, value

        if method == "GET" and path == history:
            if self.phase == "baseline":
                version_id = BASELINE_VERSION
            elif self.phase == "marker":
                version_id = self.marker_version
            else:
                version_id = CANONICAL_VERSION
            return 200, [{"version_id": version_id, "updated_at": "bounded"}]

        if method == "GET" and path.startswith(history + "/"):
            try:
                version_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return 404, {}
            if self.phase == "baseline" and version_id == BASELINE_VERSION:
                history_state = state(self._baseline_payload(), source=REPOSITORY, rules=omission_rules())
            elif self.phase == "marker" and version_id == self.marker_version:
                history_state = state(self.marker_payload, source=OPAQUE_SOURCE, rules=omission_rules())
            elif self.phase == "canonical" and version_id == CANONICAL_VERSION:
                history_state = state(self.canonical_payload, source=REPOSITORY, rules=omission_rules())
            else:
                return 404, {}
            return 200, {
                "version_id": version_id,
                "updated_at": "bounded",
                "state": history_state,
            }
        return 404, {}


def provisioner(api: ReplicaVariantWriteApi):
    return GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="marker-source-test-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: NONCE,
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        attestation_interval_seconds=0,
    )


def expect_failure(api: ReplicaVariantWriteApi, label: str) -> None:
    try:
        provisioner(api)._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    except RulesetProvisioningError:
        return
    raise AssertionError(f"{label} unexpectedly gained marker authority")


def main() -> None:
    marker_payload = writer_ruleset_payload(STATE_REF, APP_ID)
    marker_payload["name"] = MARKER_NAME
    opaque = marker_response(marker_payload)
    require(
        _marker_write_response_binds_submitted_generation(
            opaque,
            ruleset_id=RULESET_ID,
            payload=marker_payload,
            expected_updated_at=opaque["updated_at"],
        ),
        "opaque replica source was incorrectly treated as repository identity failure",
    )

    malformed_source = copy.deepcopy(opaque)
    malformed_source["source"] = None
    require(
        not _marker_write_response_binds_submitted_generation(
            malformed_source,
            ruleset_id=RULESET_ID,
            payload=marker_payload,
            expected_updated_at=opaque["updated_at"],
        ),
        "malformed write-response source gained authority",
    )

    wrong_id = copy.deepcopy(opaque)
    wrong_id["id"] = RULESET_ID + 1
    require(
        not _marker_write_response_binds_submitted_generation(
            wrong_id,
            ruleset_id=RULESET_ID,
            payload=marker_payload,
            expected_updated_at=opaque["updated_at"],
        ),
        "wrong ruleset id gained marker authority",
    )

    unsafe_rules = copy.deepcopy(opaque)
    unsafe_rules["rules"] = [{"type": "creation"}, {"type": "deletion"}]
    require(
        not _marker_write_response_binds_submitted_generation(
            unsafe_rules,
            ruleset_id=RULESET_ID,
            payload=marker_payload,
            expected_updated_at=opaque["updated_at"],
        ),
        "unsafe write-response rules gained marker authority",
    )

    live = ReplicaVariantWriteApi()
    writer_id = provisioner(live)._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    require(writer_id == RULESET_ID, "replica-variant marker changed ruleset id")
    require(live.put_calls == 2, "marker/canonical writes were not exactly two")

    stale = ReplicaVariantWriteApi(marker_version=BASELINE_VERSION)
    expect_failure(stale, "history generation did not advance beyond baseline")

    malformed_live = ReplicaVariantWriteApi(response_source=None)
    expect_failure(malformed_live, "malformed live marker PUT source")

    print("v0.3 replica-variant marker PUT source binding: PASS")


if __name__ == "__main__":
    main()
