#!/usr/bin/env python3
"""Deterministic fail-closed validation for the original #276/#221 fixture PR authority."""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlparse

from provision_v03_real_runtime_fixture import TARGET_REF
import v03_real_runtime_fixture_pr_authority as subject

REPOSITORY = "DREAM-XIN/ai-sdlc"
HEAD = "a" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def _row(
    *,
    number=901,
    state="open",
    draft=False,
    head_sha=HEAD,
    head_ref=TARGET_REF,
    base_ref="main",
    head_repo=REPOSITORY,
    base_repo=REPOSITORY,
):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "html_url": "https://github.example.invalid/pull/901",
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"full_name": head_repo},
        },
        "base": {
            "ref": base_ref,
            "repo": {"full_name": base_repo},
        },
    }


class FakeCall:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, suffix, payload=None):
        self.calls.append((method, suffix, deepcopy(payload)))
        if not self.responses:
            raise AssertionError("unexpected extra PR API call")
        return self.responses.pop(0)


def _recover(responses):
    call = FakeCall(responses)
    row = subject.recover_or_create_fixture_pr(
        call=call,
        repository=REPOSITORY,
        target_ref=TARGET_REF,
        head_sha=HEAD,
        default_branch="main",
    )
    return row, call.calls


def _expect_rejected(rows, contains):
    try:
        _recover([rows])
    except subject.FixturePrAuthorityError as exc:
        require(contains in str(exc), f"wrong failure for {contains!r}: {exc}")
        return
    raise AssertionError(f"expected rejection containing {contains!r}")


def validate_exact_existing_binding():
    row, calls = _recover([[_row()]])
    require(row["number"] == 901, "exact existing fixture PR number drifted")
    require(len(calls) == 1 and calls[0][0] == "GET", "existing fixture PR did not use one GET")
    query = parse_qs(urlparse(calls[0][1]).query)
    require(query.get("state") == ["all"], "fixture PR history lookup did not inspect all states")
    require(query.get("head") == ["DREAM-XIN:" + TARGET_REF], "fixture PR query escaped fixed owner/ref")
    require(query.get("base") == ["main"], "fixture PR query escaped trusted main")


def validate_existing_binding_fails_closed():
    _expect_rejected([_row(head_repo="DREAM-XIN/other")], "repository authority drifted")
    missing_head_repo = _row()
    missing_head_repo["head"].pop("repo")
    _expect_rejected([missing_head_repo], "repository authority is missing")
    missing_head_name = _row()
    missing_head_name["head"]["repo"] = {}
    _expect_rejected([missing_head_name], "repository authority drifted")

    _expect_rejected([_row(base_repo="DREAM-XIN/other")], "repository authority drifted")
    missing_base_repo = _row()
    missing_base_repo["base"].pop("repo")
    _expect_rejected([missing_base_repo], "repository authority is missing")
    missing_base_name = _row()
    missing_base_name["base"]["repo"] = {}
    _expect_rejected([missing_base_name], "repository authority drifted")

    malformed_head = _row()
    malformed_head["head"] = "bad"
    _expect_rejected([malformed_head], "head/base authority is malformed")
    malformed_base = _row()
    malformed_base["base"] = None
    _expect_rejected([malformed_base], "head/base authority is malformed")

    _expect_rejected([_row(head_ref="other")], "ref/base authority drifted")
    _expect_rejected([_row(base_ref="other")], "ref/base authority drifted")
    _expect_rejected([_row(head_sha="short")], "exact Git SHA")
    _expect_rejected([_row(number=0)], "number authority is malformed")
    _expect_rejected([_row(number=True)], "number authority is malformed")
    _expect_rejected([_row(state="closed")], "open non-draft")
    _expect_rejected([_row(draft=None)], "open non-draft")
    _expect_rejected([_row(), _row(number=902)], "multiple historical PRs")
    _expect_rejected(["not-a-row"], "history is malformed")


def validate_created_binding_is_equally_bounded():
    created, calls = _recover([[], _row()])
    require(created["number"] == 901, "created fixture PR was not retained")
    require([row[0] for row in calls] == ["GET", "POST"], "fixture create path did not perform GET then POST")
    body = calls[1][2]
    require(body["head"] == TARGET_REF, "fixture create escaped fixed ref")
    require(body["base"] == "main", "fixture create escaped main")
    require(body["draft"] is False, "fixture create did not force non-draft")

    bad = _row(head_repo="DREAM-XIN/other")
    try:
        _recover([[], bad])
    except subject.FixturePrAuthorityError as exc:
        require("repository authority drifted" in str(exc), f"wrong create-response rejection: {exc}")
    else:
        raise AssertionError("created fixture PR accepted wrong source repository")


def validate_selector_is_bounded():
    call = FakeCall([])
    try:
        subject.recover_or_create_fixture_pr(
            call=call,
            repository=REPOSITORY,
            target_ref=TARGET_REF,
            head_sha=HEAD,
            default_branch="other",
        )
    except subject.FixturePrAuthorityError:
        require(call.calls == [], "invalid selector reached GitHub API")
    else:
        raise AssertionError("fixture PR authority accepted non-main base selector")


def main():
    validate_exact_existing_binding()
    validate_existing_binding_fails_closed()
    validate_created_binding_is_equally_bounded()
    validate_selector_is_bounded()
    print("PASS: original #276/#221 fixture PR authority is exact-repository, exact-head and fail-closed")


if __name__ == "__main__":
    main()
