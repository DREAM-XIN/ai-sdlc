#!/usr/bin/env python3
"""Fail-closed PR authority for fixed v0.3 real-dogfood fixture branches."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlencode

from v03_dogfood_fixture_pool import DogfoodSlot
from v03_real_runtime_fixture_pr_authority import (
    FixturePrAuthorityError,
    validate_fixture_pr_binding,
)


class DogfoodFixturePrAuthorityError(RuntimeError):
    pass


def recover_or_create_dogfood_pr(
    *,
    slot: DogfoodSlot,
    call: Callable[[str, str, dict[str, Any] | None], Any],
    repository: str,
    head_sha: str,
    default_branch: str = "main",
) -> dict[str, Any]:
    owner = str(repository or "").split("/", 1)[0]
    if not owner or default_branch != "main":
        raise DogfoodFixturePrAuthorityError("dogfood PR selector authority is malformed")
    query = urlencode({
        "state": "all",
        "head": f"{owner}:{slot.target_ref}",
        "base": default_branch,
        "per_page": 100,
    })
    rows = call("GET", f"/pulls?{query}", None)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DogfoodFixturePrAuthorityError("dogfood PR history is malformed")
    if len(rows) > 1:
        raise DogfoodFixturePrAuthorityError("multiple historical PRs exist for fixed dogfood branch")
    try:
        if rows:
            return validate_fixture_pr_binding(
                rows[0],
                repository=repository,
                target_ref=slot.target_ref,
                head_sha=head_sha,
                default_branch=default_branch,
            )

        created = call("POST", "/pulls", {
            "title": f"[v0.3 dogfood] {slot.scenario} fixed release fixture",
            "head": slot.target_ref,
            "base": default_branch,
            "body": (
                f"Dedicated release-only v0.3 dogfood fixture for `{slot.scenario}`.\n\n"
                "Provisioned only after the exact-main #221 13/13 upstream gate. "
                "Do not merge as a product change, reset, force-push, or reuse for another scenario."
            ),
            "draft": False,
        })
        return validate_fixture_pr_binding(
            created,
            repository=repository,
            target_ref=slot.target_ref,
            head_sha=head_sha,
            default_branch=default_branch,
        )
    except FixturePrAuthorityError as exc:
        raise DogfoodFixturePrAuthorityError(str(exc)) from exc
