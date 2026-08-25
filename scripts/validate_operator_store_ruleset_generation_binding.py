#!/usr/bin/env python3
"""Regression for Issue #343 strictly-new fresh-marker history binding."""
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
BASELINE_VERSION = 500
MARKER_VERSION = 501
CANONICAL_VERSION = 502
NONCE = "8c106bb7c653f7436b1f27c115609a63"
MARKER_NAME = f"{RULESET_WRITER_NAME} [attest:{NONCE}]"
OPAQUE_SOURCE = "opaque:replica-source"


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


def response(payload: dict, *, source: str = REPOSITORY, updated_at: str) -> dict:
    value = state(payload, source=source, rules=omission_rules())
    value["updated_at"] = updated_at
    return value


class LiveReplicaApi:
    def __init__(
        self,
        *,
        marker_version: int = MARKER_VERSION,
        marker_response_repository: str = REPOSITORY,
        baseline_available: bool = True,
        canonical_replica_opaque: bool = False,
    ):
        self.marker_version = marker_version
        self.marker_response_repository = marker_response_repository
        self.baseline_available = baseline_available
        self.canonical_replica_opaque = canonical_replica_opaque
        self.phase = "baseline"
        self.marker_payload = None
        self.canonical_payload = None
        self.put_calls = 0
        self.detail_calls = 0

    def _baseline_payload(self):
        return writer_ruleset_payload(STATE_REF, APP_ID)

    def _history(self):
        if self.phase == "baseline":
            if not self.baseline_available:
                return 404, {}
            version_id = BASELINE_VERSION
            payload = self._baseline_payload()
            history_state = state(payload, source=REPOSITORY)
            updated_at = "2026-08-25T05:40:00Z"
        elif self.phase == "marker":
            version_id = self.marker_version
            payload = self.marker_payload
            history_state = state(payload, source=OPAQUE_SOURCE, rules=omission_rules())
            updated_at = "2026-08-25T05:50:01Z"
        else:
            version_id = CANONICAL_VERSION
            payload = self.canonical_payload
            if self.canonical_replica_opaque:
                history_state = state(payload, source=OPAQUE_SOURCE, rules=omission_rules())
            else:
                history_state = state(payload, source=REPOSITORY)
            updated_at = "2026-08-25T05:50:02Z"
        return 200, [{"version_id": version_id, "updated_at": updated_at}]

    def _version(self, version_id: int):
        if self.phase == "baseline":
            if not self.baseline_available or version_id != BASELINE_VERSION:
                return 404, {}
            payload = self._baseline_payload()
            history_state = state(payload, source=REPOSITORY)
            updated_at = "2026-08-25T05:40:00Z"
        elif self.phase == "marker":
            if version_id != self.marker_version:
                return 404, {}
            history_state = state(
                self.marker_payload,
                source=OPAQUE_SOURCE,
                rules=omission_rules(),
            )
            updated_at = "2026-08-25T05:50:01Z"
        else:
            if version_id != CANONICAL_VERSION:
                return 404, {}
            if self.canonical_replica_opaque:
                history_state = state(
                    self.canonical_payload,
                    source=OPAQUE_SOURCE,
                    rules=omission_rules(),
                )
            else:
                history_state = state(self.canonical_payload, source=REPOSITORY)
            updated_at = "2026-08-25T05:50:02Z"
        return 200, {
            "version_id": version_id,
            "updated_at": updated_at,
            "state": history_state,
        }

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        path = urlparse(url).path
        detail = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        history = f"{detail}/history"

        if method == "PUT" and path == detail:
            self.put_calls += 1
            payload = copy.deepcopy(body)
            if payload.get("name") == MARKER_NAME:
                self.marker_payload = payload
                self.phase = "marker"
                return 200, response(
                    payload,
                    source=self.marker_response_repository,
                    updated_at="2026-08-25T05:50:01.123Z",
                )
            self.canonical_payload = payload
            self.phase = "canonical"
            return 200, response(
                payload,
                updated_at="2026-08-25T05:50:02.456Z",
            )

        if method == "GET" and path == detail:
            self.detail_calls += 1
            if self.phase == "marker":
                value = state(
                    self.marker_payload,
                    source=OPAQUE_SOURCE,
                    rules=omission_rules(),
                )
                value["updated_at"] = "2026-08-25T05:49:59.000Z"
                return 200, value
            if self.phase == "canonical" and self.canonical_replica_opaque:
                value = state(
                    self.canonical_payload,
                    source=OPAQUE_SOURCE,
                    rules=omission_rules(),
                )
                value["updated_at"] = "2026-08-25T05:49:58.000Z"
                return 200, value
            if self.phase == "canonical":
                return 200, response(
                    self.canonical_payload,
                    updated_at="2026-08-25T05:50:02.456Z",
                )
            return 200, response(
                self._baseline_payload(),
                updated_at="2026-08-25T05:40:00Z",
            )

        if method == "GET" and path == history:
            return self._history()
        if method == "GET" and path.startswith(history + "/"):
            try:
                version_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return 404, {}
            return self._version(version_id)
        return 404, {}


def provisioner(api: LiveReplicaApi):
    return GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="generation-bound-test-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: NONCE,
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        attestation_interval_seconds=0,
    )


def expect_failure(api: LiveReplicaApi, label: str) -> None:
    try:
        provisioner(api)._attest_writer_ruleset(REPOSITORY, RULESET_ID, STATE_REF)
    except RulesetProvisioningError:
        return
    raise AssertionError(f"{label} unexpectedly gained fresh-marker authority")


def main() -> None:
    live_shape = LiveReplicaApi()
    writer_id = provisioner(live_shape)._attest_writer_ruleset(
        REPOSITORY,
        RULESET_ID,
        STATE_REF,
    )
    require(writer_id == RULESET_ID, "generation-bound attestation changed ruleset id")
    require(live_shape.put_calls == 2, "marker/canonical writes were not exactly two")
    require(live_shape.detail_calls >= 2, "live-shape regression did not exercise replica detail")

    stale = LiveReplicaApi(marker_version=BASELINE_VERSION)
    expect_failure(stale, "history version not newer than pre-write baseline")

    # The raw PUT response source is no longer repository identity authority.
    # A different/opaque source string is acceptable only because the write was
    # issued to REPOSITORY and that exact repository/ruleset subsequently
    # exposes a strictly-new history generation carrying the fresh nonce.
    replica_variant_source = LiveReplicaApi(marker_response_repository=OTHER_REPOSITORY)
    replica_writer_id = provisioner(replica_variant_source)._attest_writer_ruleset(
        REPOSITORY,
        RULESET_ID,
        STATE_REF,
    )
    require(replica_writer_id == RULESET_ID, "replica-variant PUT source changed ruleset id")

    cross_wire_stale = LiveReplicaApi(
        marker_version=BASELINE_VERSION,
        marker_response_repository=OTHER_REPOSITORY,
    )
    expect_failure(
        cross_wire_stale,
        "replica-variant PUT source without target-repository generation advance",
    )

    no_baseline = LiveReplicaApi(baseline_available=False)
    expect_failure(no_baseline, "existing ruleset without readable pre-write baseline")
    require(no_baseline.put_calls == 0, "missing baseline performed a marker write")

    canonical_opaque = LiveReplicaApi(canonical_replica_opaque=True)
    expect_failure(canonical_opaque, "canonical writer attempted to reuse marker-only fallback")

    print("v0.3 strictly-new fresh-marker generation binding: PASS")


if __name__ == "__main__":
    main()
