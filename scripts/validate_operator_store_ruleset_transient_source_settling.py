#!/usr/bin/env python3
"""Regression for #332's bounded transient ruleset-source settling window."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_provision import RulesetProvisioningError, writer_ruleset_payload
from operator_store_github_ruleset_stabilized import (
    DEFAULT_STABILIZATION_ATTEMPTS,
    DEFAULT_TRANSIENT_SOURCE_SETTLING_ATTEMPTS,
    StabilizedAttestedGitHubOperatorStoreRulesetProvisioner,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
SECRET = "transient-settling-admin-token"
TRANSIENT_RAW_SOURCE = f"must-never-leak:{SECRET}:opaque-github-source"


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


def _omission(state: dict) -> dict:
    result = copy.deepcopy(state)
    result["rules"] = [{"type": "creation"}, {"type": "update"}]
    return result


def _transient(payload: dict) -> dict:
    result = _omission(_state(payload))
    result["source"] = TRANSIENT_RAW_SOURCE
    return result


class SequencedHistoryApi:
    """Return one fixed version id whose serialized state can settle over reads."""

    def __init__(self, states: list[dict], *, version_id: int = 500):
        if not states:
            raise ValueError("states must not be empty")
        self.states = copy.deepcopy(states)
        self.version_id = version_id
        self.history_calls = 0
        self.current_index = 0
        self.sleeps = 0

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        if method != "GET":
            return 405, {}
        path = urlparse(url).path
        history_path = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}/history"
        if path == history_path:
            self.current_index = min(self.history_calls, len(self.states) - 1)
            self.history_calls += 1
            return 200, [{"version_id": self.version_id, "updated_at": "2026-08-24T09:00:00Z"}]
        if path == f"{history_path}/{self.version_id}":
            return 200, {
                "version_id": self.version_id,
                "updated_at": "2026-08-24T09:00:00Z",
                "state": copy.deepcopy(self.states[self.current_index]),
            }
        return 404, {}


def _provisioner(api: SequencedHistoryApi, *, base: int, extended: int):
    return StabilizedAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token=SECRET,
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: setattr(api, "sleeps", api.sleeps + 1),
        nonce_factory=lambda: "aabbccdd",
        attestation_attempts=base,
        transient_source_settling_attempts=extended,
        attestation_interval_seconds=0,
    )


def _failure(api: SequencedHistoryApi, payload: dict, *, base: int, extended: int) -> str:
    try:
        _provisioner(api, base=base, extended=extended)._wait_for_exact_history_state(
            REPOSITORY,
            RULESET_ID,
            payload,
        )
    except RulesetProvisioningError as exc:
        text = str(exc)
        require(SECRET not in text, "failure diagnostic leaked trusted token")
        require(TRANSIENT_RAW_SOURCE not in text, "failure diagnostic leaked raw source")
        return text
    raise AssertionError("expected transient settling path to fail closed")


def _marker() -> dict:
    payload = writer_ruleset_payload(STATE_REF, APP_ID)
    payload["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    return payload


def validate_exact_transient_can_converge_after_normal_budget():
    marker = _marker()
    transient = _transient(marker)
    converged = _omission(_state(marker))
    # The exact transient survives observations 1..5; base budget is only 3.
    api = SequencedHistoryApi([transient] * 5 + [converged])
    version_id, state = _provisioner(api, base=3, extended=7)._wait_for_exact_history_state(
        REPOSITORY,
        RULESET_ID,
        marker,
    )
    require(version_id == 500, "settled attestation returned wrong version")
    require(state == _state(marker), "settled omission state was not canonical strict state")
    require(api.history_calls == 6, "extended settling stopped on the wrong observation")
    require(api.sleeps == 5, "extended settling sleep count drifted")


def validate_persistent_transient_exhausts_extended_budget():
    marker = _marker()
    api = SequencedHistoryApi([_transient(marker)])
    text = _failure(api, marker, base=2, extended=5)
    require("after 5 attempts" in text, "extended cap was not reported exactly")
    require("mismatch_fields=source,rules" in text, "transient mismatch fields were lost")
    require("source_shape=other-string" in text, "bounded source category was lost")
    require(
        "rules_shape=0:creation:parameters=absent|1:update:parameters=absent" in text,
        "bounded omission-only rule shape was lost",
    )
    require(api.history_calls == 5, "persistent transient did not consume exact extended cap")
    require(api.sleeps == 4, "persistent transient sleep count drifted")


def validate_extended_authority_is_exact_shape_only():
    marker = _marker()
    owner, repo = REPOSITORY.split("/", 1)

    cases: list[tuple[str, dict]] = []
    for label, source in (
        ("owner-only", owner),
        ("repo-only", repo),
        ("non-string", {"opaque": "value"}),
    ):
        state = _omission(_state(marker))
        state["source"] = copy.deepcopy(source)
        cases.append((label, state))

    permissive_observed = _state(marker)
    permissive_observed["source"] = TRANSIENT_RAW_SOURCE
    permissive_observed["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    cases.append(("explicit-permissive-observed-update", permissive_observed))

    extra_rule = _transient(marker)
    extra_rule["rules"].append({"type": "deletion"})
    cases.append(("extra-rule", extra_rule))

    extra_creation_key = _transient(marker)
    extra_creation_key["rules"][0]["unexpected"] = "must-not-gain-authority"
    cases.append(("extra-creation-top-level-key", extra_creation_key))

    extra_update_key = _transient(marker)
    extra_update_key["rules"][1]["unexpected"] = {"nested": True}
    cases.append(("extra-update-top-level-key", extra_update_key))

    for label, state in cases:
        api = SequencedHistoryApi([state])
        text = _failure(api, marker, base=1, extended=5)
        require("after 1 attempts" in text, f"{label} gained extended settling authority")
        require(api.history_calls == 1, f"{label} performed extra history reads")
        require(api.sleeps == 0, f"{label} performed an extended settling sleep")


def validate_permissive_submitted_payload_never_gets_extension():
    payload = _marker()
    payload["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    # Reverse direction of the observed-permissive case: the submitted payload
    # itself is unsafe, while GitHub appears to return the omission-only shape.
    # That omission must never convert a permissive submission into an eligible
    # extended-wait candidate.
    observed = _omission(_state(payload))
    observed["source"] = TRANSIENT_RAW_SOURCE
    api = SequencedHistoryApi([observed])
    text = _failure(api, payload, base=1, extended=5)
    require("after 1 attempts" in text, "permissive submitted payload gained extended authority")
    require(api.history_calls == 1, "permissive submitted payload performed extra history reads")
    require(api.sleeps == 0, "permissive submitted payload entered extended settling")


def validate_extended_window_terminates_on_shape_change():
    marker = _marker()
    transient = _transient(marker)
    changed = _omission(_state(marker))
    changed["source"] = REPOSITORY.split("/", 1)[0]
    api = SequencedHistoryApi([transient, changed])
    text = _failure(api, marker, base=1, extended=5)
    require("after 2 attempts" in text, "changed transient shape did not terminate promptly")
    require("source_shape=owner-only" in text, "changed shape diagnostic was not preserved")
    require(api.history_calls == 2, "changed shape continued through extended window")


def validate_invalid_extended_budget_is_rejected():
    marker = _marker()
    api = SequencedHistoryApi([_state(marker)])
    try:
        _provisioner(api, base=3, extended=2)
    except ValueError:
        return
    raise AssertionError("extended budget smaller than base budget was accepted")


def validate_defaults_remain_bounded():
    require(DEFAULT_STABILIZATION_ATTEMPTS == 60, "normal stabilization budget drifted")
    require(
        DEFAULT_TRANSIENT_SOURCE_SETTLING_ATTEMPTS == 900,
        "transient source settling cap must remain explicitly bounded",
    )


def main():
    validate_exact_transient_can_converge_after_normal_budget()
    validate_persistent_transient_exhausts_extended_budget()
    validate_extended_authority_is_exact_shape_only()
    validate_permissive_submitted_payload_never_gets_extension()
    validate_extended_window_terminates_on_shape_change()
    validate_invalid_extended_budget_is_rejected()
    validate_defaults_remain_bounded()
    print("Operator Store transient ruleset-source settling validation passed")


if __name__ == "__main__":
    main()
