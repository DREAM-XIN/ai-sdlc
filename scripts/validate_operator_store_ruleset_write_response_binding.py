#!/usr/bin/env python3
"""Regression for Issue #340 fresh-marker write-response binding."""
from __future__ import annotations

import copy
from urllib.parse import urlparse

from operator_store_github_ruleset_current_detail_bound import (
    CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_provision import (
    RulesetProvisioningError,
    writer_ruleset_payload,
)

REPOSITORY = "DREAM-XIN/ai-sdlc"
OTHER_REPOSITORY = "DREAM-XIN/ai-sdlc-cross-wire"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
RULESET_ID = 20775740
APP_ID = 4576406
UPDATED_AT = "2026-08-25T00:39:03.676+08:00"
VERSION_ID = 47485495
SECRET = "write-response-binding-secret"
OPAQUE_SOURCE = f"opaque:{SECRET}:replica"
MARKER = "AI-SDLC Operator Store writer [attest:e425f240d47075e745b824c32814c332]"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def omission_rules() -> list[dict]:
    return [{"type": "creation"}, {"type": "update"}]


def payload(*, marker: bool = True) -> dict:
    value = writer_ruleset_payload(STATE_REF, APP_ID)
    if marker:
        value["name"] = MARKER
    return value


def state(value: dict, *, source=REPOSITORY, rules=None) -> dict:
    return {
        "id": RULESET_ID,
        "name": value["name"],
        "target": value["target"],
        "source_type": "Repository",
        "source": copy.deepcopy(source),
        "enforcement": value["enforcement"],
        "conditions": copy.deepcopy(value["conditions"]),
        "bypass_actors": copy.deepcopy(value["bypass_actors"]),
        "rules": copy.deepcopy(value["rules"] if rules is None else rules),
    }


def exact_write_response(value: dict, *, repository: str = REPOSITORY) -> dict:
    response = state(value, source=repository, rules=omission_rules())
    response["updated_at"] = UPDATED_AT
    return response


def opaque_current(value: dict, *, opaque_source: str = OPAQUE_SOURCE) -> dict:
    current = state(value, source=opaque_source, rules=omission_rules())
    current["updated_at"] = UPDATED_AT
    return current


def opaque_history(value: dict, *, opaque_source: str = OPAQUE_SOURCE) -> dict:
    return state(value, source=opaque_source, rules=omission_rules())


class ReplicaSplitApi:
    def __init__(
        self,
        *,
        write_response: dict,
        current_detail: dict,
        history_state: dict,
    ):
        self.write_response = copy.deepcopy(write_response)
        self.current_detail = copy.deepcopy(current_detail)
        self.history_state = copy.deepcopy(history_state)
        self.put_calls = 0
        self.detail_calls = 0

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        path = urlparse(url).path
        detail_path = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        history_path = f"{detail_path}/history"
        if method == "PUT" and path == detail_path:
            self.put_calls += 1
            return 200, copy.deepcopy(self.write_response)
        if method == "GET" and path == detail_path:
            self.detail_calls += 1
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


class CrossRepositoryApi:
    """Write repository A, then expose a maliciously cross-wired B response/truth."""

    def __init__(self, value: dict):
        opaque_b = "opaque-cross-repository-replica"
        self.value = copy.deepcopy(value)
        self.write_response = exact_write_response(value, repository=OTHER_REPOSITORY)
        self.current_detail = opaque_current(value, opaque_source=opaque_b)
        self.history_state = opaque_history(value, opaque_source=opaque_b)
        self.put_repositories: list[str] = []
        self.other_detail_calls = 0

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        path = urlparse(url).path
        a_detail = f"/repos/{REPOSITORY}/rulesets/{RULESET_ID}"
        b_detail = f"/repos/{OTHER_REPOSITORY}/rulesets/{RULESET_ID}"
        b_history = f"{b_detail}/history"
        if method == "PUT" and path == a_detail:
            self.put_repositories.append(REPOSITORY)
            return 200, copy.deepcopy(self.write_response)
        if method == "GET" and path == b_detail:
            self.other_detail_calls += 1
            return 200, copy.deepcopy(self.current_detail)
        if method == "GET" and path == b_history:
            return 200, [{"version_id": VERSION_ID, "updated_at": UPDATED_AT}]
        if method == "GET" and path == f"{b_history}/{VERSION_ID}":
            return 200, {
                "version_id": VERSION_ID,
                "updated_at": UPDATED_AT,
                "state": copy.deepcopy(self.history_state),
            }
        return 404, {}


def provisioner(api):
    return CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner(
        admin_token=SECRET,
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
        nonce_factory=lambda: "e425f240d47075e745b824c32814c332",
        attestation_attempts=1,
        transient_source_settling_attempts=1,
        attestation_interval_seconds=0,
    )


def execute(api: ReplicaSplitApi, value: dict, *, minimum_version_id=None):
    p = provisioner(api)
    ruleset_id, _ = p._write_ruleset(REPOSITORY, RULESET_ID, value)
    return p._wait_for_exact_history_state(
        REPOSITORY,
        ruleset_id,
        value,
        minimum_version_id=minimum_version_id,
    )


def expect_failure(api: ReplicaSplitApi, value: dict, *, minimum_version_id=None):
    try:
        execute(api, value, minimum_version_id=minimum_version_id)
    except RulesetProvisioningError as exc:
        text = str(exc)
        require(SECRET not in text, "secret leaked through failure diagnostic")
        require(OPAQUE_SOURCE not in text, "opaque source leaked through failure diagnostic")
        return text
    raise AssertionError("unsafe fresh-marker write-response binding was accepted")


def validate_replica_opaque_current_and_history_bind_to_exact_write_response():
    value = payload()
    api = ReplicaSplitApi(
        write_response=exact_write_response(value),
        current_detail=opaque_current(value),
        history_state=opaque_history(value),
    )
    version_id, observed = execute(api, value)
    require(version_id == VERSION_ID, "wrong causal history version returned")
    require(observed == state(value), "write-response binding did not return canonical strict state")
    require(api.put_calls == 1, "positive path did not originate from one exact write")
    require(api.detail_calls == 1, "positive path did not re-read current detail")


def validate_write_response_authority_fails_closed():
    value = payload()
    cases: list[tuple[str, dict]] = []

    wrong_source = exact_write_response(value)
    wrong_source["source"] = "DREAM-XIN"
    cases.append(("wrong-write-source", wrong_source))

    missing_source = exact_write_response(value)
    missing_source.pop("source")
    cases.append(("missing-write-source", missing_source))

    wrong_type = exact_write_response(value)
    wrong_type["source_type"] = "Organization"
    cases.append(("wrong-write-source-type", wrong_type))

    wrong_marker = exact_write_response(value)
    wrong_marker["name"] = "AI-SDLC Operator Store writer [attest:11223344]"
    cases.append(("wrong-write-marker", wrong_marker))

    wrong_time = exact_write_response(value)
    wrong_time["updated_at"] = "2026-08-25T00:39:04.000+08:00"
    cases.append(("wrong-write-updated-at", wrong_time))

    permissive = exact_write_response(value)
    permissive["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    cases.append(("permissive-write-rules", permissive))

    extra_rule = exact_write_response(value)
    extra_rule["rules"].append({"type": "deletion"})
    cases.append(("extra-write-rule", extra_rule))

    drift = exact_write_response(value)
    drift["enforcement"] = "disabled"
    cases.append(("write-identity-drift", drift))

    for label, response in cases:
        api = ReplicaSplitApi(
            write_response=response,
            current_detail=opaque_current(value),
            history_state=opaque_history(value),
        )
        text = expect_failure(api, value)
        require("after 1 attempts" in text, f"{label} gained hidden retry authority")


def validate_opaque_current_boundary_fails_closed():
    value = payload()
    owner, repo = REPOSITORY.split("/", 1)
    cases: list[tuple[str, dict]] = []

    for label, source_value in (
        ("owner-only-current", owner),
        ("repo-only-current", repo),
        ("non-string-current", {"opaque": True}),
    ):
        current = opaque_current(value)
        current["source"] = source_value
        cases.append((label, current))

    stale = opaque_current(value)
    stale["updated_at"] = "2026-08-25T00:39:02.000+08:00"
    cases.append(("stale-current-updated-at", stale))

    permissive = opaque_current(value)
    permissive["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    cases.append(("permissive-current-rules", permissive))

    extra = opaque_current(value)
    extra["rules"].append({"type": "deletion"})
    cases.append(("extra-current-rule", extra))

    wrong_marker = opaque_current(value)
    wrong_marker["name"] = "AI-SDLC Operator Store writer [attest:11223344]"
    cases.append(("wrong-current-marker", wrong_marker))

    drift = opaque_current(value)
    drift["conditions"] = {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
    cases.append(("current-identity-drift", drift))

    for label, current in cases:
        api = ReplicaSplitApi(
            write_response=exact_write_response(value),
            current_detail=current,
            history_state=opaque_history(value),
        )
        text = expect_failure(api, value)
        require("after 1 attempts" in text, f"{label} gained hidden retry authority")


def validate_history_boundary_remains_closed():
    value = payload()

    permissive = opaque_history(value)
    permissive["rules"] = [
        {"type": "creation"},
        {"type": "update", "parameters": {"update_allows_fetch_and_merge": True}},
    ]
    api = ReplicaSplitApi(
        write_response=exact_write_response(value),
        current_detail=opaque_current(value),
        history_state=permissive,
    )
    expect_failure(api, value)
    require(api.detail_calls == 0, "ineligible history should fail before current-detail authority")

    stale_api = ReplicaSplitApi(
        write_response=exact_write_response(value),
        current_detail=opaque_current(value),
        history_state=opaque_history(value),
    )
    expect_failure(stale_api, value, minimum_version_id=VERSION_ID)
    require(stale_api.detail_calls == 0, "stale history consulted current-detail authority")


def validate_cross_repository_pending_write_cannot_activate():
    value = payload()
    api = CrossRepositoryApi(value)
    p = provisioner(api)
    ruleset_id, _ = p._write_ruleset(REPOSITORY, RULESET_ID, value)
    require(api.put_repositories == [REPOSITORY], "cross-wire fixture did not write repository A exactly once")
    try:
        p._wait_for_exact_history_state(
            OTHER_REPOSITORY,
            ruleset_id,
            value,
        )
    except RulesetProvisioningError as exc:
        text = str(exc)
        require(SECRET not in text, "secret leaked through cross-repository failure")
        require("after 1 attempts" in text, "repository mismatch gained hidden retry authority")
    else:
        raise AssertionError("repository A pending write activated authority for repository B")
    require(api.other_detail_calls == 0, "repository-mismatched binding reached current-detail authority")


def validate_canonical_writer_never_uses_fresh_marker_write_response_fallback():
    value = payload(marker=False)
    api = ReplicaSplitApi(
        write_response=exact_write_response(value),
        current_detail=opaque_current(value),
        history_state=opaque_history(value),
    )
    expect_failure(api, value)


def main():
    validate_replica_opaque_current_and_history_bind_to_exact_write_response()
    validate_write_response_authority_fails_closed()
    validate_opaque_current_boundary_fails_closed()
    validate_history_boundary_remains_closed()
    validate_cross_repository_pending_write_cannot_activate()
    validate_canonical_writer_never_uses_fresh_marker_write_response_fallback()
    print("v0.3 fresh-marker write-response binding validation passed")


if __name__ == "__main__":
    main()
