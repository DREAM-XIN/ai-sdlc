#!/usr/bin/env python3
"""Adversarial pagination validation for Operator Store ruleset protection/provisioning."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from operator_store_github_ruleset_provision import (
    GitHubOperatorStoreRulesetProvisioner,
    RULESET_INTEGRITY_NAME,
    RULESET_WRITER_NAME,
    RulesetProvisioningError,
)
from operator_store_protection import UNKNOWN, UNPROTECTED

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
APP_ID = 9001


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _detail(ruleset_id: int):
    if ruleset_id == 1:
        return {
            "id": 1,
            "target": "branch",
            "enforcement": "active",
            "source_type": "Repository",
            "source": REPOSITORY,
            "bypass_actors": [
                {"actor_type": "Integration", "actor_id": APP_ID, "bypass_mode": "always"}
            ],
            "rules": [
                {"type": "creation"},
                {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
            ],
        }
    if ruleset_id == 2:
        return {
            "id": 2,
            "target": "branch",
            "enforcement": "active",
            "source_type": "Repository",
            "source": REPOSITORY,
            "bypass_actors": [],
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        }
    if ruleset_id == 3:
        return {
            "id": 3,
            "target": "branch",
            "enforcement": "active",
            "source_type": "Repository",
            "source": REPOSITORY,
            "bypass_actors": [
                {"actor_type": "Integration", "actor_id": 7777, "bypass_mode": "always"}
            ],
            "rules": [
                {"type": "creation"},
                {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
            ],
        }
    raise AssertionError(f"unexpected ruleset detail id {ruleset_id}")


def _page_one():
    rows = [
        {"type": "creation", "ruleset_id": 1},
        {"type": "update", "ruleset_id": 1},
        {"type": "deletion", "ruleset_id": 2},
        {"type": "non_fast_forward", "ruleset_id": 2},
    ]
    rows.extend({"type": "deletion", "ruleset_id": 2} for _ in range(96))
    require(len(rows) == 100, "page-one fixture must force pagination")
    return rows


def make_get(*, fail_page_two: bool = False):
    def get(url: str, headers: dict[str, str]):
        parsed = urlparse(url)
        if "/rules/branches/" in parsed.path:
            query = parse_qs(parsed.query)
            require(query.get("per_page") == ["100"], "verifier did not request maximum page size")
            page = query.get("page")
            if page == ["1"]:
                return 200, _page_one()
            if page == ["2"]:
                if fail_page_two:
                    return 503, {"message": "simulated later-page failure"}
                return 200, [
                    {"type": "creation", "ruleset_id": 3},
                    {"type": "update", "ruleset_id": 3},
                ]
            raise AssertionError(f"unexpected branch-rules page {page}")

        marker = "/rulesets/"
        if marker in parsed.path:
            ruleset_id = int(parsed.path.rsplit("/", 1)[1])
            return 200, _detail(ruleset_id)
        raise AssertionError(f"unexpected URL {url}")

    return get


def validate_page_two_writer_is_not_hidden():
    verifier = GitHubRulesetProtectionVerifier(
        token="trusted-token",
        operator_app_id=APP_ID,
        http_get=make_get(),
    )
    receipt = verifier.verify(REPOSITORY, STATE_REF)
    require(
        receipt.status == UNPROTECTED,
        f"foreign page-two writer was hidden by pagination: {receipt.status}",
    )


def validate_later_page_failure_is_unknown():
    verifier = GitHubRulesetProtectionVerifier(
        token="trusted-token",
        operator_app_id=APP_ID,
        http_get=make_get(fail_page_two=True),
    )
    receipt = verifier.verify(REPOSITORY, STATE_REF)
    require(
        receipt.status == UNKNOWN,
        f"later-page retrieval failure did not fail closed UNKNOWN: {receipt.status}",
    )


def _summary(ruleset_id: int, name: str) -> dict:
    return {
        "id": ruleset_id,
        "name": name,
        "source_type": "Repository",
        "source": REPOSITORY,
        "target": "branch",
        "enforcement": "active",
    }


def make_installer_request(*, fail_page_two: bool = False, malformed_page_two: bool = False):
    writes: list[tuple[str, str, dict | None]] = []
    writer_id = 501
    integrity_id = 502

    def request(method: str, url: str, headers: dict[str, str], body: dict | None = None):
        parsed = urlparse(url)
        rulesets_path = f"/repos/{REPOSITORY}/rulesets"
        if parsed.path == rulesets_path and method == "GET":
            query = parse_qs(parsed.query)
            require(query.get("targets") == ["branch"], "installer did not scope discovery to branch rulesets")
            require(query.get("per_page") == ["100"], "installer did not request maximum page size")
            page = query.get("page")
            if page == ["1"]:
                return 200, [_summary(1000 + index, f"filler-{index}") for index in range(100)]
            if page == ["2"]:
                if fail_page_two:
                    return 503, {"message": "simulated installer later-page failure"}
                if malformed_page_two:
                    return 200, [_summary(writer_id, RULESET_WRITER_NAME), "malformed-row"]
                return 200, [
                    _summary(writer_id, RULESET_WRITER_NAME),
                    _summary(integrity_id, RULESET_INTEGRITY_NAME),
                ]
            raise AssertionError(f"unexpected installer ruleset page {page}")

        if parsed.path == rulesets_path and method == "POST":
            raise AssertionError("installer created a duplicate ruleset instead of discovering page two")

        prefix = f"{rulesets_path}/"
        if parsed.path.startswith(prefix) and method == "PUT":
            ruleset_id = int(parsed.path[len(prefix):])
            require(ruleset_id in {writer_id, integrity_id}, f"installer updated unexpected ruleset id {ruleset_id}")
            writes.append((method, url, body))
            return 200, {"id": ruleset_id}

        raise AssertionError(f"unexpected installer request {method} {url}")

    return request, writes, writer_id, integrity_id


def _make_installer(request):
    return GitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=request,
        sleeper=lambda _: None,
    )


def validate_installer_discovers_page_two_before_upsert():
    request, writes, writer_id, integrity_id = make_installer_request()
    provisioner = _make_installer(request)
    actual = provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    require(actual == (writer_id, integrity_id), f"installer did not reconcile page-two rulesets: {actual}")
    require(len(writes) == 2, f"installer expected exactly two updates, observed {len(writes)} writes")
    require(all(method == "PUT" for method, _, _ in writes), "installer performed a non-update write")


def validate_installer_later_page_failure_aborts_before_write():
    request, writes, _, _ = make_installer_request(fail_page_two=True)
    provisioner = _make_installer(request)
    try:
        provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    except RulesetProvisioningError:
        pass
    else:
        raise AssertionError("installer accepted an incomplete ruleset listing after page-two failure")
    require(writes == [], "installer wrote rulesets after an incomplete discovery failure")


def validate_installer_malformed_later_page_aborts_before_write():
    request, writes, _, _ = make_installer_request(malformed_page_two=True)
    provisioner = _make_installer(request)
    try:
        provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    except RulesetProvisioningError:
        pass
    else:
        raise AssertionError("installer accepted malformed ruleset discovery rows")
    require(writes == [], "installer wrote rulesets after malformed discovery")


def main():
    validate_page_two_writer_is_not_hidden()
    validate_later_page_failure_is_unknown()
    validate_installer_discovers_page_two_before_upsert()
    validate_installer_later_page_failure_aborts_before_write()
    validate_installer_malformed_later_page_aborts_before_write()
    print("Operator Store ruleset pagination validation passed")


if __name__ == "__main__":
    main()
