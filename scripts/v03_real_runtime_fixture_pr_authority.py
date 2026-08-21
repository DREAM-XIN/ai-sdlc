#!/usr/bin/env python3
"""Fail-closed GitHub PR authority for the fixed #276/#221 real-runtime fixture."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlencode


class FixturePrAuthorityError(RuntimeError):
    pass


def _exact_sha(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise FixturePrAuthorityError(f"{label} is not an exact Git SHA")
    return text


def validate_fixture_pr_binding(
    row: Any,
    *,
    repository: str,
    target_ref: str,
    head_sha: str,
    default_branch: str = "main",
) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise FixturePrAuthorityError("fixture PR truth is malformed")
    head = row.get("head")
    base = row.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise FixturePrAuthorityError("fixture PR head/base authority is malformed")
    head_repo_value = head.get("repo")
    base_repo_value = base.get("repo")
    if not isinstance(head_repo_value, dict) or not isinstance(base_repo_value, dict):
        raise FixturePrAuthorityError("fixture PR repository authority is missing")
    expected_repository = str(repository or "").strip().lower()
    if not expected_repository or "/" not in expected_repository:
        raise FixturePrAuthorityError("fixture repository authority is malformed")
    head_repo = str(head_repo_value.get("full_name") or "").strip().lower()
    base_repo = str(base_repo_value.get("full_name") or "").strip().lower()
    if head_repo != expected_repository or base_repo != expected_repository:
        raise FixturePrAuthorityError("fixture PR repository authority drifted")
    if head.get("ref") != target_ref or base.get("ref") != default_branch:
        raise FixturePrAuthorityError("fixture PR ref/base authority drifted")
    expected_head = _exact_sha(head_sha, "expected fixture head")
    candidate_head = _exact_sha(head.get("sha"), "fixture PR head")
    if candidate_head != expected_head:
        raise FixturePrAuthorityError("fixture PR head drifted")
    number = row.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise FixturePrAuthorityError("fixture PR number authority is malformed")
    if row.get("state") != "open" or row.get("draft") is not False:
        raise FixturePrAuthorityError("fixture PR is not exact open non-draft authority")
    return row


def recover_or_create_fixture_pr(
    *,
    call: Callable[[str, str, dict[str, Any] | None], Any],
    repository: str,
    target_ref: str,
    head_sha: str,
    default_branch: str = "main",
) -> dict[str, Any]:
    owner = str(repository or "").split("/", 1)[0]
    if not owner or not target_ref or default_branch != "main":
        raise FixturePrAuthorityError("fixture PR selector authority is malformed")
    query = urlencode({
        "state": "all",
        "head": f"{owner}:{target_ref}",
        "base": default_branch,
        "per_page": 100,
    })
    rows = call("GET", f"/pulls?{query}", None)
    if not isinstance(rows, list):
        raise FixturePrAuthorityError("fixture PR recovery returned non-list response")
    if any(not isinstance(row, dict) for row in rows):
        raise FixturePrAuthorityError("fixture PR history is malformed")
    if len(rows) > 1:
        raise FixturePrAuthorityError("multiple historical PRs exist for fixed fixture branch")
    if rows:
        return validate_fixture_pr_binding(
            rows[0],
            repository=repository,
            target_ref=target_ref,
            head_sha=head_sha,
            default_branch=default_branch,
        )

    created = call(
        "POST",
        "/pulls",
        {
            "title": "[v0.3 fixture] real-runtime fault-injection target for #221",
            "head": target_ref,
            "base": default_branch,
            "body": (
                "Dedicated release-only Feature/PR fixture provisioned from trusted main for Issue #221.\n\n"
                "Do not merge as a product change. The authoritative Feature is intentionally ACTIVE at "
                "`code-review / WORKING` so the protected Vertical runtime can dispatch one exact-head Reviewer. "
                "Worker output is evidence only; lifecycle mutation remains Store + Feature Persist authority."
            ),
            "draft": False,
        },
    )
    return validate_fixture_pr_binding(
        created,
        repository=repository,
        target_ref=target_ref,
        head_sha=head_sha,
        default_branch=default_branch,
    )
