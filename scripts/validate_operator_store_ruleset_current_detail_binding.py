#!/usr/bin/env python3
"""Regression for #336 trusted ruleset current-detail write binding."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_attested import RulesetWriteAttestation, _state_digest
from operator_store_github_ruleset_current_detail_bound import (
    CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner,
    CurrentDetailBoundAttestedGitHubRulesetProtectionVerifier,
    _current_detail_binds_exact_write,
    _normalize_history_state_from_bound_current,
)
from operator_store_github_ruleset_provision import RulesetProvisioningError, writer_ruleset_payload
from operator_store_github_ruleset_stabilized import (
    NormalizedAttestedGitHubRulesetProtectionVerifier,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
SECRET = "bound-current-admin-token"
OPAQUE_SOURCE = f"must-never-leak:{SECRET}:history-source"
UPDATED_AT = "2026-08-24T15:00:00Z"
VERSION_ID = 500


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _payload(*, marker: bool = True) -> dict:
    value = writer_ruleset_payload(STATE_REF, APP_ID)
    if marker:
        value["name"] = "AI-SDLC Operator Store writer [attest:aabbccdd]"
    return value


def _state(payload: dict, *, source=REPOSITORY, rules=None) -> dict:
    return {
        "id": RULESET_ID,
        "name": payload["name"],
        "target": payload["target"],
        "source_type": "Repository",
        "source": copy.deepcopy(source),
        "enforcement": payload["enforcement"],
        "conditions": copy.deepcopy(payload["conditions"]),
        "bypass_actors": copy.deepcopy(payload["bypass_actors"]),
        "rules": copy.deepcopy(payload["rules"] if rules is None else rules),
    }


def _omission_rules() -> list[dict]:
    return [{"type": "creation"}, {"type": "update"}]


def _current(payload: dict, *, source=REPOSITORY, updated_at=UPDATED_AT, rules=None) -> dict:
    value = _state(
        payload,
        source=source,
        rules=_omission_rules() if rules is None else rules,
    )
    value["updated_at"] = updated_at
    return value


def _opaque_history(payload: dict) -> dict:
    return _state(payload, source=OPAQUE_SOURCE, rules=_omission_rules())


class BoundHistoryApi:
    """One causally new history version plus independently controlled current detail."""

    def __init__(self, *, history_state: dict, current_detail: dict, write_updated_at=UPDATED_AT):
        self.history_state = copy.deepcopy(history_state)
        self.current_detail = copy.deepcopy(current_detail)
        self.write_updated_at = write_updated_at
        self.put_calls = 0
        self.get_detail_calls = 0
        self.sleeps = 0

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        path = urlparse(url).path
        detail_path = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        history_path = f"{detail_path}/history"

        if method == "PUT" and path == detail_path:
            self.put_calls += 1
            return 200, {"id": RULESET_ID, "updated_at": self.write_updated_at}
        if method == "GET" and path == detail_path:
            self.get_detail_calls += 1
            return 200, copy.deepcopy(self.current_detail)
        if method == "GET" and path == history_path:
            return 200, [{"version_id": VERSION_ID, "updated_at": UPDATED_AT}]
        if method == "GET" and path == f"{history_path}/{VERSION_ID}":
            return 200, {
                "version_id": VERSION_ID,
                "updated_at": UPDATED_AT,
                "state": copy.deepcopy(self.history_state),
            }
        return 404, {}


def _provisioner(api: BoundHistoryApi):
    return CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token=SECRET,
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: setattr(api, "sleeps", api.sleeps + 1),
        nonce_factory=lambda: "aabbccdd",
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        attestation_interval_seconds=0,
    )


def _attempt_write_then_wait(api: BoundHistoryApi, payload: dict, *, minimum_version_id=None):
    provisioner = _provisioner(api)
    ruleset_id, _ = provisioner._write_ruleset(REPOSITORY, RULESET_ID, payload)
    return provisioner._wait_for_exact_history_state(
        REPOSITORY,
        ruleset_id,
        payload,
        minimum_version_id=minimum_version_id,
    )


def _expect_failure(api: BoundHistoryApi, payload: dict, *, minimum_version_id=None) -> str:
    try:
        _attempt_write_then_wait(api, payload, minimum_version_id=minimum_version_id)
    except RulesetProvisioningError as exc:
        text = str(exc)
        require(SECRET not in text, "trusted token leaked through failure diagnostic")
        require(OPAQUE_SOURCE not in text, "opaque history source leaked through failure diagnostic")
        return text
    raise AssertionError("unsafe cross-surface binding was accepted")


def validate_exact_current_detail_can_bind_opaque_history_source():
    payload = _payload()
    api = BoundHistoryApi(
        history_state=_opaque_history(payload),
        current_detail=_current(payload),
    )
    version_id, state = _attempt_write_then_wait(api, payload)
    require(version_id == VERSION_ID, "wrong causal history version returned")
    require(state == _state(payload), "bound history did not canonicalize to exact strict state")
    require(api.put_calls == 1, "write response binding did not originate from exact write")
    require(api.get_detail_calls == 1, "current detail was not re-read for cross-surface proof")
    require(api.sleeps == 0, "exact bound current detail unexpectedly entered a settling wait")


def validate_current_detail_binding_fails_closed():
    payload = _payload()
    cases: list[tuple[str, dict]] = []

    stale = _current(payload, updated_at="2026-08-24T14:59:59Z")
    cases.append(("stale-updated-at", stale))

    owner = _current(payload, source=REPOSITORY.split("/", 1)[0])
    cases.append(("owner-only-current-source", owner))

    opaque = _current(payload, source=OPAQUE_SOURCE)
    cases.append(("opaque-current-source", opaque))

    extra_rule = _current(payload)
    extra_rule["rules"].append({"type": "deletion"})
    cases.append(("extra-current-rule", extra_rule))

    extra_key = _current(payload)
    extra_key["rules"][1]["unexpected"] = True
    cases.append(("extra-current-rule-key", extra_key))

    permissive = _current(
        payload,
        rules=[
            {"type": "creation"},
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
        ],
    )
    cases.append(("permissive-current-rule", permissive))

    drift = _current(payload)
    drift["enforcement"] = "disabled"
    cases.append(("current-identity-drift", drift))

    for label, current in cases:
        api = BoundHistoryApi(history_state=_opaque_history(payload), current_detail=current)
        text = _expect_failure(api, payload)
        require("after 1 attempts" in text, f"{label} gained hidden retry authority")
        require(api.get_detail_calls == 1, f"{label} did not test exact current-detail boundary")


def validate_history_side_remains_exact_and_closed():
    payload = _payload()
    owner, repo = REPOSITORY.split("/", 1)
    cases: list[tuple[str, dict]] = []

    for label, source in (
        ("owner-only", owner),
        ("repo-only", repo),
        ("non-string", {"not": "a source"}),
    ):
        cases.append((label, _state(payload, source=source, rules=_omission_rules())))

    permissive_rules = _opaque_history(payload)
    permissive_rules["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    cases.append(("permissive-history-rule", permissive_rules))

    extra_rule = _opaque_history(payload)
    extra_rule["rules"].append({"type": "deletion"})
    cases.append(("extra-history-rule", extra_rule))

    extra_key = _opaque_history(payload)
    extra_key["rules"][0]["unexpected"] = "no-authority"
    cases.append(("extra-history-key", extra_key))

    identity_drift = _opaque_history(payload)
    identity_drift["target"] = "push"
    cases.append(("history-identity-drift", identity_drift))

    for label, history_state in cases:
        api = BoundHistoryApi(history_state=history_state, current_detail=_current(payload))
        text = _expect_failure(api, payload)
        require("after 1 attempts" in text, f"{label} gained cross-surface authority")
        require(
            api.get_detail_calls == 0,
            f"{label} should fail history eligibility before consulting current detail",
        )


def validate_permissive_submitted_payload_never_binds():
    payload = _payload()
    payload["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    history = _state(payload, source=OPAQUE_SOURCE, rules=_omission_rules())
    current = _state(payload, rules=_omission_rules())
    current["updated_at"] = UPDATED_AT
    api = BoundHistoryApi(history_state=history, current_detail=current)
    _expect_failure(api, payload)
    require(api.get_detail_calls == 0, "permissive submitted payload reached current-detail authority")


def validate_stale_version_never_binds():
    payload = _payload()
    api = BoundHistoryApi(history_state=_opaque_history(payload), current_detail=_current(payload))
    text = _expect_failure(api, payload, minimum_version_id=VERSION_ID)
    require("after 1 attempts" in text, "stale history version gained cross-surface authority")
    require(api.get_detail_calls == 0, "stale history consulted current detail")


def validate_helper_requires_exact_write_response_binding():
    payload = _payload()
    history = _opaque_history(payload)
    current = _current(payload)
    normalized = _normalize_history_state_from_bound_current(
        history,
        current,
        repository=REPOSITORY,
        ruleset_id=RULESET_ID,
        payload=payload,
        expected_updated_at=UPDATED_AT,
    )
    require(normalized == _state(payload), "positive helper binding returned wrong canonical state")
    require(
        _current_detail_binds_exact_write(
            current,
            repository=REPOSITORY,
            ruleset_id=RULESET_ID,
            payload=payload,
            expected_updated_at=UPDATED_AT,
        ),
        "exact current detail did not bind exact write response",
    )
    require(
        _normalize_history_state_from_bound_current(
            history,
            current,
            repository=REPOSITORY,
            ruleset_id=RULESET_ID,
            payload=payload,
            expected_updated_at="2026-08-24T15:00:01Z",
        )
        is None,
        "mismatched write-response timestamp was accepted",
    )


class VerifierApi:
    def __init__(self, *, history_state: dict, current_detail: dict):
        self.history_state = copy.deepcopy(history_state)
        self.current_detail = copy.deepcopy(current_detail)

    def get(self, url: str, headers: dict[str, str]):
        path = urlparse(url).path
        detail_path = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        history_path = f"{detail_path}/history"
        if path == detail_path:
            return 200, copy.deepcopy(self.current_detail)
        if path == history_path:
            return 200, [{"version_id": VERSION_ID, "updated_at": UPDATED_AT}]
        if path == f"{history_path}/{VERSION_ID}":
            return 200, {
                "version_id": VERSION_ID,
                "updated_at": UPDATED_AT,
                "state": copy.deepcopy(self.history_state),
            }
        return 404, {}


def _attestation(payload: dict) -> RulesetWriteAttestation:
    return RulesetWriteAttestation(
        ruleset_id=RULESET_ID,
        marker_version_id=VERSION_ID - 1,
        version_id=VERSION_ID,
        current_updated_at=UPDATED_AT,
        state_digest=_state_digest(_state(payload)),
    )


def validate_only_bound_trusted_verifier_can_revalidate_opaque_source():
    payload = _payload(marker=False)
    current = _current(payload)
    history = _opaque_history(payload)
    api = VerifierApi(history_state=history, current_detail=current)
    attestations = {RULESET_ID: _attestation(payload)}

    legacy = NormalizedAttestedGitHubRulesetProtectionVerifier(
        token=SECRET,
        operator_app_id=APP_ID,
        http_get=api.get,
        write_attestations=attestations,
    )
    require(
        legacy._latest_version_state(REPOSITORY, RULESET_ID, current) is None,
        "pre-#336 verifier unexpectedly accepts opaque history source",
    )

    bound = CurrentDetailBoundAttestedGitHubRulesetProtectionVerifier(
        token=SECRET,
        operator_app_id=APP_ID,
        http_get=api.get,
        write_attestations=attestations,
    )
    result = bound._latest_version_state(REPOSITORY, RULESET_ID, current)
    require(result is not None, "bound trusted verifier rejected exact cross-surface proof")
    state, proof = result
    require(state == _state(payload), "bound trusted verifier returned non-canonical state")
    require(proof["version_id"] == VERSION_ID, "bound verifier proof lost exact version")
    require(proof["current_updated_at"] == UPDATED_AT, "bound verifier proof lost write binding")

    stale_current = copy.deepcopy(current)
    stale_current["updated_at"] = "2026-08-24T15:00:01Z"
    require(
        bound._latest_version_state(REPOSITORY, RULESET_ID, stale_current) is None,
        "bound trusted verifier accepted changed current-detail write binding",
    )


def main():
    validate_exact_current_detail_can_bind_opaque_history_source()
    validate_current_detail_binding_fails_closed()
    validate_history_side_remains_exact_and_closed()
    validate_permissive_submitted_payload_never_binds()
    validate_stale_version_never_binds()
    validate_helper_requires_exact_write_response_binding()
    validate_only_bound_trusted_verifier_can_revalidate_opaque_source()
    print("Operator Store current-detail-bound ruleset attestation validation passed")


if __name__ == "__main__":
    main()
