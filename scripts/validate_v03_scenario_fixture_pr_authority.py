#!/usr/bin/env python3
"""Focused fail-closed validation for #310 scenario fixture PR authority."""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import provision_v03_scenario_fixture_pool as subject
from v03_scenario_fixture_pool import SLOTS

REPOSITORY = "DREAM-XIN/ai-sdlc"
HEAD = "a" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def _row(
    *,
    number=701,
    state="open",
    draft=False,
    head_sha=HEAD,
    head_ref=None,
    base_ref="main",
    head_repo=REPOSITORY,
    base_repo=REPOSITORY,
):
    slot = SLOTS[0]
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "html_url": "https://github.example.invalid/pull/701",
        "head": {
            "ref": head_ref or slot.target_ref,
            "sha": head_sha,
            "repo": {"full_name": head_repo},
        },
        "base": {
            "ref": base_ref,
            "repo": {"full_name": base_repo},
        },
    }


def _call_with_fake_api(responses):
    original = subject._api_request
    calls = []

    def fake_api(method, url, token, body=None):
        calls.append((method, url, token, deepcopy(body)))
        if not responses:
            raise AssertionError("unexpected extra GitHub API request")
        return responses.pop(0)

    subject._api_request = fake_api
    try:
        result = subject._recover_or_create_pr(
            SLOTS[0],
            api="https://api.example.invalid",
            repository=REPOSITORY,
            token="bounded-token",
            head_sha=HEAD,
            default_branch="main",
        )
        return result, calls
    finally:
        subject._api_request = original


def _expect_rejected(payload, contains):
    try:
        _call_with_fake_api([(200, payload)])
    except subject.FixturePoolProvisionError as exc:
        require(contains in str(exc), f"wrong failure for {contains!r}: {exc}")
        return
    raise AssertionError(f"expected fail-closed rejection containing {contains!r}")


def validate_exact_existing_pr_binding():
    result, calls = _call_with_fake_api([(200, [_row()])])
    require(result == (701, "https://github.example.invalid/pull/701"), "exact existing PR binding drifted")
    require(len(calls) == 1 and calls[0][0] == "GET", "existing PR lookup did not use one GET")
    parsed = urlparse(calls[0][1])
    query = parse_qs(parsed.query)
    require(query.get("state") == ["all"], "PR history lookup did not inspect all states")
    require(query.get("head") == ["DREAM-XIN:" + SLOTS[0].target_ref], "PR history lookup escaped fixed owner/ref")
    require(query.get("base") == ["main"], "PR history lookup escaped main base")


def validate_existing_pr_authority_fail_closed():
    wrong_head_repo = _row(head_repo="DREAM-XIN/other")
    _expect_rejected([wrong_head_repo], "repository authority drifted")

    missing_head_repo = _row()
    missing_head_repo["head"].pop("repo")
    _expect_rejected([missing_head_repo], "repository authority is missing")

    missing_head_full_name = _row()
    missing_head_full_name["head"]["repo"] = {}
    _expect_rejected([missing_head_full_name], "repository authority drifted")

    wrong_base_repo = _row(base_repo="DREAM-XIN/other")
    _expect_rejected([wrong_base_repo], "repository authority drifted")

    missing_base_repo = _row()
    missing_base_repo["base"].pop("repo")
    _expect_rejected([missing_base_repo], "repository authority is missing")

    missing_base_full_name = _row()
    missing_base_full_name["base"]["repo"] = {}
    _expect_rejected([missing_base_full_name], "repository authority drifted")

    _expect_rejected([_row(draft=None)], "open non-draft")
    _expect_rejected([_row(state="closed")], "open non-draft")
    _expect_rejected([_row(head_sha="short")], "exact Git SHA")
    _expect_rejected([_row(number=0)], "number authority is malformed")
    _expect_rejected([_row(head_ref="wrong-ref")], "ref/base authority drifted")
    _expect_rejected([_row(base_ref="wrong-base")], "ref/base authority drifted")
    _expect_rejected([_row(), _row(number=702)], "ambiguous historical PRs")
    _expect_rejected(["not-a-pr-row"], "history is malformed")


def validate_created_pr_response_is_equally_bounded():
    result, calls = _call_with_fake_api([(200, []), (201, _row())])
    require(result[0] == 701, "created exact PR binding did not return candidate number")
    require([row[0] for row in calls] == ["GET", "POST"], "create path did not perform exact GET then POST")
    post_body = calls[1][3]
    require(post_body["head"] == SLOTS[0].target_ref, "create path escaped fixed slot ref")
    require(post_body["base"] == "main", "create path escaped trusted main base")
    require(post_body["draft"] is False, "create path did not force non-draft PR")

    malformed_created = _row(head_repo="DREAM-XIN/other")
    try:
        _call_with_fake_api([(200, []), (201, malformed_created)])
    except subject.FixturePoolProvisionError as exc:
        require("repository authority drifted" in str(exc), f"wrong created-response failure: {exc}")
    else:
        raise AssertionError("created PR response accepted wrong source repository authority")


def main():
    validate_exact_existing_pr_binding()
    validate_existing_pr_authority_fail_closed()
    validate_created_pr_response_is_equally_bounded()
    print("PASS: #310 fixture PR recovery/create authority is exact-repository, exact-head and fail-closed")


if __name__ == "__main__":
    main()
