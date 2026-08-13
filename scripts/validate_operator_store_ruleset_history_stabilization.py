#!/usr/bin/env python3
"""Deterministic validation for bounded ruleset-history stabilization diagnostics."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_provision import RulesetProvisioningError, writer_ruleset_payload
from operator_store_github_ruleset_stabilized import (
    DEFAULT_STABILIZATION_ATTEMPTS,
    StabilizedAttestedGitHubOperatorStoreRulesetProvisioner,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
SECRET = "diagnostic-test-admin-token"
MALICIOUS_TEXT = "must-not-appear-in-diagnostic"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _state(payload: dict) -> dict:
    return {
        "id": RULESET_ID,
        "name": payload["name"],
        "target": payload["target"],
        "source_type": "Repository",
        "source": REPOSITORY,
        "enforcement": payload["enforcement"],
        "conditions": copy.deepcopy(payload["conditions"]),
        "bypass_actors": copy.deepcopy(payload["bypass_actors"]),
        "rules": copy.deepcopy(payload["rules"]),
    }


class HistoryApi:
    def __init__(
        self,
        *,
        history_versions: list[int],
        states: dict[int, dict],
        history_status: int = 200,
        version_status: int = 200,
    ):
        self.history_versions = list(history_versions)
        self.states = copy.deepcopy(states)
        self.history_status = history_status
        self.version_status = version_status
        self.history_calls = 0
        self.sleeps = 0

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        if method != "GET":
            return 405, {}
        path = urlparse(url).path
        history_path = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history"
        if path == history_path:
            if self.history_status != 200:
                return self.history_status, {}
            if not self.history_versions:
                return 200, []
            index = min(self.history_calls, len(self.history_versions) - 1)
            version_id = self.history_versions[index]
            self.history_calls += 1
            return 200, [{"version_id": version_id, "updated_at": "2026-08-13T05:00:00Z"}]
        if path.startswith(history_path + "/"):
            if self.version_status != 200:
                return self.version_status, {}
            try:
                version_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                return 404, {}
            state = self.states.get(version_id)
            if state is None:
                return 404, {}
            return 200, {
                "version_id": version_id,
                "updated_at": "2026-08-13T05:00:00Z",
                "state": copy.deepcopy(state),
            }
        return 404, {}


def _provisioner(api: HistoryApi, *, attempts: int):
    return StabilizedAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token=SECRET,
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: setattr(api, "sleeps", api.sleeps + 1),
        nonce_factory=lambda: "aabbccdd",
        attestation_attempts=attempts,
        attestation_interval_seconds=0,
    )


def _expect_failure(provisioner, payload, expected: str, *, minimum_version_id=None):
    try:
        provisioner._wait_for_exact_history_state(
            REPOSITORY,
            RULESET_ID,
            payload,
            minimum_version_id=minimum_version_id,
        )
    except RulesetProvisioningError as exc:
        text = str(exc)
        require(expected in text, f"missing diagnostic category {expected!r}: {text}")
        require(SECRET not in text, "diagnostic leaked trusted admin token")
        require(MALICIOUS_TEXT not in text, "diagnostic leaked arbitrary history payload text")
        return text
    raise AssertionError(f"expected fail-closed diagnostic {expected!r}")


def validate_delayed_history_visibility():
    marker = writer_ruleset_payload(STATE_REF, APP_ID)
    marker["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    old = writer_ruleset_payload(STATE_REF, APP_ID)
    api = HistoryApi(
        history_versions=[5] * 12 + [6],
        states={5: _state(old), 6: _state(marker)},
    )
    provisioner = _provisioner(api, attempts=20)
    version_id, state = provisioner._wait_for_exact_history_state(
        REPOSITORY,
        RULESET_ID,
        marker,
    )
    require(version_id == 6, "delayed marker did not converge to exact newer version")
    require(state["name"] == marker["name"], "delayed marker returned wrong state")
    require(api.history_calls == 13, "stabilization stopped at the wrong observation")
    require(api.sleeps == 12, "stabilization sleep count drifted")


def validate_persistent_stale_history_diagnostic():
    marker = writer_ruleset_payload(STATE_REF, APP_ID)
    marker["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    old = writer_ruleset_payload(STATE_REF, APP_ID)
    api = HistoryApi(history_versions=[5], states={5: _state(old)})
    text = _expect_failure(_provisioner(api, attempts=3), marker, "state-name-mismatch")
    require("version_id=5" in text, "stale history diagnostic omitted version id")
    require("mismatch_fields=name" in text, "stale history diagnostic omitted name mismatch")
    require("rules_shape=" not in text, "rules shape should not be emitted for name-only mismatch")


def validate_live_omission_shape_is_scoped_to_trusted_strict_write():
    marker = writer_ruleset_payload(STATE_REF, APP_ID)
    marker["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    normalized = _state(marker)
    normalized["rules"] = [{"type": "creation"}, {"type": "update"}]
    api = HistoryApi(history_versions=[6], states={6: normalized})
    version_id, state = _provisioner(api, attempts=2)._wait_for_exact_history_state(
        REPOSITORY,
        RULESET_ID,
        marker,
    )
    require(version_id == 6, "live omission-only history did not bind the observed version")
    require(state == _state(marker), "live omission-only history did not canonicalize to strict write")

    permissive = _state(marker)
    permissive["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    text = _expect_failure(
        _provisioner(HistoryApi(history_versions=[7], states={7: permissive}), attempts=1),
        marker,
        "state-shape-mismatch",
    )
    require("mismatch_fields=rules" in text, "permissive history mismatch did not identify rules")
    require(
        "1:update:parameters=present:update_allows_fetch_and_merge=true:other_keys=0" in text,
        "permissive history diagnostic did not expose the bounded true classification",
    )


def validate_state_shape_mismatch_diagnostic():
    marker = writer_ruleset_payload(STATE_REF, APP_ID)
    marker["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    malformed = _state(marker)
    malformed["rules"] = [
        {"type": "creation"},
        {"type": "update"},
        {"type": "deletion"},
    ]
    api = HistoryApi(history_versions=[6], states={6: malformed})
    text = _expect_failure(_provisioner(api, attempts=2), marker, "state-shape-mismatch")
    require("mismatch_fields=rules" in text, "shape diagnostic did not identify rules mismatch")
    require(
        "rules_shape=0:creation:parameters=absent|1:update:parameters=absent|2:other:parameters=absent" in text,
        "shape diagnostic did not expose bounded extra-rule shape",
    )


def validate_rules_shape_reports_only_bounded_semantics():
    marker = writer_ruleset_payload(STATE_REF, APP_ID)
    marker["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"

    explicit_false = _state(marker)
    explicit_false["rules"] = [
        {"type": "creation", "parameters": {}},
        {
            "type": "update",
            "parameters": {
                "update_allows_fetch_and_merge": False,
                MALICIOUS_TEXT: MALICIOUS_TEXT,
            },
        },
        {"type": MALICIOUS_TEXT, "parameters": {MALICIOUS_TEXT: MALICIOUS_TEXT}},
    ]
    text = _expect_failure(
        _provisioner(HistoryApi(history_versions=[6], states={6: explicit_false}), attempts=1),
        marker,
        "state-shape-mismatch",
    )
    require(
        "0:creation:parameters=present:update_allows_fetch_and_merge=absent:other_keys=0" in text,
        "diagnostic did not classify empty creation parameters",
    )
    require(
        "1:update:parameters=present:update_allows_fetch_and_merge=false:other_keys=1" in text,
        "diagnostic did not classify explicit false update semantics",
    )
    require(
        "2:other:parameters=present:update_allows_fetch_and_merge=absent:other_keys=1" in text,
        "diagnostic did not redact unknown rule type/parameter keys",
    )

    explicit_true = _state(marker)
    explicit_true["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    text = _expect_failure(
        _provisioner(HistoryApi(history_versions=[7], states={7: explicit_true}), attempts=1),
        marker,
        "state-shape-mismatch",
    )
    require(
        "1:update:parameters=present:update_allows_fetch_and_merge=true:other_keys=0" in text,
        "diagnostic did not make permissive true visible",
    )

    malformed = _state(marker)
    malformed["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": MALICIOUS_TEXT}},
    ]
    text = _expect_failure(
        _provisioner(HistoryApi(history_versions=[8], states={8: malformed}), attempts=1),
        marker,
        "state-shape-mismatch",
    )
    require(
        "1:update:parameters=present:update_allows_fetch_and_merge=malformed:other_keys=0" in text,
        "diagnostic did not classify malformed update parameter without echoing it",
    )


def validate_canonical_requires_strictly_newer_version():
    canonical = writer_ruleset_payload(STATE_REF, APP_ID)
    api = HistoryApi(history_versions=[6], states={6: _state(canonical)})
    text = _expect_failure(
        _provisioner(api, attempts=2),
        canonical,
        "stale-version",
        minimum_version_id=6,
    )
    require("version_id=6" in text, "strictly-newer diagnostic omitted observed version")


def validate_transport_diagnostics_fail_closed():
    marker = writer_ruleset_payload(STATE_REF, APP_ID)
    marker["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    old = writer_ruleset_payload(STATE_REF, APP_ID)

    history_failure = HistoryApi(
        history_versions=[5],
        states={5: _state(old)},
        history_status=500,
    )
    text = _expect_failure(_provisioner(history_failure, attempts=2), marker, "history-http")
    require("http_status=500" in text, "history HTTP diagnostic omitted status")

    version_failure = HistoryApi(
        history_versions=[5],
        states={5: _state(old)},
        version_status=403,
    )
    text = _expect_failure(_provisioner(version_failure, attempts=2), marker, "version-http")
    require("http_status=403" in text, "version HTTP diagnostic omitted status")


def validate_bounded_default():
    require(
        DEFAULT_STABILIZATION_ATTEMPTS == 60,
        "trusted stabilization default must remain explicitly bounded",
    )


def main():
    validate_delayed_history_visibility()
    validate_persistent_stale_history_diagnostic()
    validate_live_omission_shape_is_scoped_to_trusted_strict_write()
    validate_state_shape_mismatch_diagnostic()
    validate_rules_shape_reports_only_bounded_semantics()
    validate_canonical_requires_strictly_newer_version()
    validate_transport_diagnostics_fail_closed()
    validate_bounded_default()
    print("Operator Store ruleset history stabilization diagnostics passed")


if __name__ == "__main__":
    main()
