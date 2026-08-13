#!/usr/bin/env python3
"""Deterministic validation for causal ruleset write attestation."""
from __future__ import annotations

import copy
from urllib.parse import parse_qs, urlparse

from operator_store_github_ruleset_attested import (
    AttestedGitHubOperatorStoreRulesetProvisioner,
    MARKER_PREFIX,
)
from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from operator_store_protection import PROTECTED, UNKNOWN

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
BRANCH = "ai-sdlc-operator-state"
APP_ID = 4576406
HISTORY_UPDATED = "2026-08-13T02:08:25Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _update_rule(payload):
    matches = [
        row for row in payload.get("rules") or []
        if isinstance(row, dict) and row.get("type") == "update"
    ]
    require(len(matches) == 1, "expected exactly one update rule")
    return matches[0]


class FakeAttestedRulesetApi:
    """Model fractional current detail and whole-second history serialization."""

    def __init__(self):
        self.rulesets: dict[int, dict] = {}
        self.current_updated_at: dict[int, str] = {}
        self.versions: dict[int, list[dict]] = {}
        self.next_version: dict[int, int] = {}
        self.next_id = 101
        self.write_counter = 0
        self.writer_id: int | None = None
        self.integrity_id: int | None = None
        self.history_override: dict[int, int] = {}
        self.normalize_writer_current = True
        self.calls: list[tuple[str, str, dict | None]] = []

    def _raw_updated_at(self):
        self.write_counter += 1
        return f"2026-08-13T02:08:25.{self.write_counter:03d}Z"

    def _summary(self, ruleset_id: int, payload: dict):
        return {
            "id": ruleset_id,
            "name": payload["name"],
            "source_type": "Repository",
            "source": REPOSITORY,
            "enforcement": payload["enforcement"],
        }

    def _state(self, ruleset_id: int, payload: dict):
        result = copy.deepcopy(payload)
        result.update({
            "id": ruleset_id,
            "source_type": "Repository",
            "source": REPOSITORY,
        })
        return result

    def _current_detail(self, ruleset_id: int):
        result = self._state(ruleset_id, self.rulesets[ruleset_id])
        if self.normalize_writer_current and ruleset_id == self.writer_id:
            _update_rule(result).pop("parameters", None)
        result["updated_at"] = self.current_updated_at[ruleset_id]
        return result

    def _record_version(self, ruleset_id: int, payload: dict):
        version_id = self.next_version.get(ruleset_id, 1)
        self.next_version[ruleset_id] = version_id + 1
        raw_updated = self._raw_updated_at()
        self.current_updated_at[ruleset_id] = raw_updated
        version = {
            "version_id": version_id,
            "actor": {"id": 1, "type": "User"},
            "updated_at": HISTORY_UPDATED,
            "state": self._state(ruleset_id, payload),
        }
        self.versions.setdefault(ruleset_id, []).append(version)
        return version

    def _history_version(self, ruleset_id: int, version_id: int):
        for version in self.versions.get(ruleset_id, []):
            if version["version_id"] == version_id:
                return copy.deepcopy(version)
        return None

    def _latest_history_version(self, ruleset_id: int):
        versions = self.versions.get(ruleset_id, [])
        if not versions:
            return None
        override = self.history_override.get(ruleset_id)
        if override is not None:
            return self._history_version(ruleset_id, override)
        return copy.deepcopy(versions[-1])

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url, copy.deepcopy(body)))
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)

        list_path = f"/repos/{REPOSITORY}/rulesets"
        if method == "GET" and path == list_path:
            page = int((query.get("page") or ["1"])[0])
            if page > 1:
                return 200, []
            return 200, [
                self._summary(ruleset_id, payload)
                for ruleset_id, payload in sorted(self.rulesets.items())
            ]

        if method == "POST" and path == list_path:
            ruleset_id = self.next_id
            self.next_id += 1
            payload = copy.deepcopy(body or {})
            self.rulesets[ruleset_id] = payload
            if isinstance(payload.get("name"), str) and payload["name"].startswith(MARKER_PREFIX):
                self.writer_id = ruleset_id
            elif payload.get("name") == "AI-SDLC Operator Store integrity":
                self.integrity_id = ruleset_id
            self._record_version(ruleset_id, payload)
            return 201, self._current_detail(ruleset_id)

        branch_path = f"/repos/{REPOSITORY}/rules/branches/{BRANCH}"
        if method == "GET" and path == branch_path:
            page = int((query.get("page") or ["1"])[0])
            if page > 1:
                return 200, []
            rows = []
            for ruleset_id, payload in sorted(self.rulesets.items()):
                if payload.get("enforcement") != "active" or payload.get("target") != "branch":
                    continue
                includes = (((payload.get("conditions") or {}).get("ref_name") or {}).get("include") or [])
                if STATE_REF not in includes:
                    continue
                for rule in payload.get("rules") or []:
                    rows.append({
                        "type": rule.get("type"),
                        "ruleset_id": ruleset_id,
                        "ruleset_source_type": "Repository",
                        "ruleset_source": REPOSITORY,
                    })
            return 200, rows

        prefix = f"/repos/{REPOSITORY}/rulesets/"
        if path.startswith(prefix):
            tail = path[len(prefix):]
            parts = tail.split("/")
            try:
                ruleset_id = int(parts[0])
            except ValueError:
                return 404, {}

            if len(parts) == 2 and parts[1] == "history" and method == "GET":
                latest = self._latest_history_version(ruleset_id)
                if latest is None:
                    return 200, []
                return 200, [{
                    "version_id": latest["version_id"],
                    "actor": copy.deepcopy(latest["actor"]),
                    "updated_at": latest["updated_at"],
                }]

            if len(parts) == 3 and parts[1] == "history" and method == "GET":
                try:
                    version_id = int(parts[2])
                except ValueError:
                    return 404, {}
                version = self._history_version(ruleset_id, version_id)
                return (200, version) if version is not None else (404, {})

            if len(parts) == 1:
                if ruleset_id not in self.rulesets:
                    return 404, {}
                if method == "GET":
                    return 200, self._current_detail(ruleset_id)
                if method == "PUT":
                    payload = copy.deepcopy(body or {})
                    self.rulesets[ruleset_id] = payload
                    if isinstance(payload.get("name"), str) and (
                        payload["name"].startswith(MARKER_PREFIX)
                        or payload["name"] == "AI-SDLC Operator Store writer"
                    ):
                        self.writer_id = ruleset_id
                    self._record_version(ruleset_id, payload)
                    return 200, self._current_detail(ruleset_id)

        return 404, {}

    def get(self, url, headers):
        return self.request("GET", url, headers, None)

    def mutate_writer_hidden_permissive_same_second(self):
        require(self.writer_id is not None, "writer must exist before mutation")
        previous_latest = self.versions[self.writer_id][-1]["version_id"]
        payload = copy.deepcopy(self.rulesets[self.writer_id])
        _update_rule(payload)["parameters"] = {"update_allows_fetch_and_merge": True}
        self.rulesets[self.writer_id] = payload
        self._record_version(self.writer_id, payload)
        self.history_override[self.writer_id] = previous_latest


class NeverConvergingHistoryApi(FakeAttestedRulesetApi):
    def _latest_history_version(self, ruleset_id: int):
        if ruleset_id == self.writer_id:
            return None
        return super()._latest_history_version(ruleset_id)


def make_provisioner(api, *, nonce="a1b2c3d4"):
    return AttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: nonce,
        attestation_attempts=3,
        attestation_interval_seconds=0,
    )


def main():
    api = FakeAttestedRulesetApi()
    provisioner = make_provisioner(api)
    writer_id, integrity_id = provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    require(writer_id != integrity_id, "writer/integrity rulesets collapsed")
    require(len(api.rulesets) == 2, "causal attestation created extra persistent rulesets")
    require(api.rulesets[writer_id]["name"] == "AI-SDLC Operator Store writer", "marker name was not restored")

    attestation = provisioner.write_attestations.get(writer_id)
    require(attestation is not None, "writer write attestation was not materialized")
    require(
        attestation.marker_version_id < attestation.version_id,
        "canonical version was not causally ordered after marker version",
    )
    require(
        api.current_updated_at[writer_id] == attestation.current_updated_at,
        "attestation did not bind exact current write-response timestamp",
    )

    marker_versions = [
        version for version in api.versions[writer_id]
        if version["state"].get("name", "").startswith(MARKER_PREFIX)
    ]
    require(len(marker_versions) == 1, "expected exactly one unique marker version")
    require(
        marker_versions[0]["version_id"] == attestation.marker_version_id,
        "attestation did not bind the observed marker version",
    )

    receipt = provisioner.protection_verifier().verify(REPOSITORY, STATE_REF)
    require(
        receipt.status == PROTECTED and receipt.policy_digest,
        "whole-second history normalization was not proven through causal write attestation",
    )

    generic = GitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=api.get,
    )
    require(
        generic.verify(REPOSITORY, STATE_REF).status == UNKNOWN,
        "generic read-only verifier guessed omission-only update semantics without write attestation",
    )

    stale_history = FakeAttestedRulesetApi()
    stale_provisioner = make_provisioner(stale_history, nonce="deadbeef")
    stale_writer_id, _ = stale_provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    stale_attestation = stale_provisioner.write_attestations[stale_writer_id]
    stale_history.mutate_writer_hidden_permissive_same_second()
    require(
        stale_history.current_updated_at[stale_writer_id].split(".")[0]
        == stale_attestation.current_updated_at.split(".")[0],
        "adversarial mutation did not remain in the same UTC second",
    )
    require(
        stale_history.history_override[stale_writer_id] == stale_attestation.version_id,
        "adversarial history did not remain stale on the safe attested version",
    )
    require(
        stale_provisioner.protection_verifier().verify(REPOSITORY, STATE_REF).status == UNKNOWN,
        "same-second current V4 with stale safe V3 history was authorized",
    )

    history_advanced = FakeAttestedRulesetApi()
    advanced_provisioner = make_provisioner(history_advanced, nonce="c0ffee12")
    advanced_writer_id, _ = advanced_provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    payload = copy.deepcopy(history_advanced.rulesets[advanced_writer_id])
    history_advanced.rulesets[advanced_writer_id] = payload
    history_advanced._record_version(advanced_writer_id, payload)
    require(
        advanced_provisioner.protection_verifier().verify(REPOSITORY, STATE_REF).status == UNKNOWN,
        "post-attestation latest-version drift was authorized",
    )

    unavailable = NeverConvergingHistoryApi()
    try:
        make_provisioner(unavailable, nonce="facefeed").ensure_rulesets(REPOSITORY, STATE_REF)
        raise AssertionError("unobservable marker history unexpectedly produced an attestation")
    except Exception as exc:
        require(
            "history did not causally attest write" in str(exc),
            f"unexpected non-convergence failure: {exc}",
        )
    require(
        unavailable.writer_id is not None
        and unavailable.rulesets[unavailable.writer_id]["name"].startswith(MARKER_PREFIX),
        "failed attestation did not remain fail-closed before canonical authorization",
    )

    crash_recovery = FakeAttestedRulesetApi()
    first = make_provisioner(crash_recovery, nonce="1111aaaa")
    marker_payload = {
        "name": f"AI-SDLC Operator Store writer [attest:1111aaaa]",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [{"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}],
        "conditions": {"ref_name": {"include": [STATE_REF], "exclude": []}},
        "rules": [
            {"type": "creation"},
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
        ],
    }
    writer_id, _ = first._write_ruleset(REPOSITORY, None, marker_payload)
    require(crash_recovery.rulesets[writer_id]["name"].startswith(MARKER_PREFIX), "marker fixture missing")
    recovered = make_provisioner(crash_recovery, nonce="2222bbbb")
    recovered_writer, _ = recovered.ensure_rulesets(REPOSITORY, STATE_REF)
    require(recovered_writer == writer_id, "orphaned marker recovery created a duplicate writer ruleset")
    require(len(crash_recovery.rulesets) == 2, "orphaned marker recovery left duplicate rulesets")
    require(
        crash_recovery.rulesets[recovered_writer]["name"] == "AI-SDLC Operator Store writer",
        "orphaned marker recovery did not restore canonical writer name",
    )
    require(
        recovered.protection_verifier().verify(REPOSITORY, STATE_REF).status == PROTECTED,
        "recovered marker sequence did not produce a valid causal attestation",
    )

    print("Operator Store causal ruleset write-attestation validation passed")


if __name__ == "__main__":
    main()
