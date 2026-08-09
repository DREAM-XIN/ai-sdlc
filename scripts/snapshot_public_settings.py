#!/usr/bin/env python3
"""Capture a sanitized repository-settings snapshot before Public conversion.

The output is designed to be safe even if an Actions log later becomes public:
- repository variables are represented only by count + SHA-256 of sorted names;
- environments are represented only by count + SHA-256 of sorted names;
- no secret endpoint is queried;
- no variable values, tokens, reviewers, users, or team identities are emitted.

Some administration endpoints may not be readable by GITHUB_TOKEN. Those are
reported as unavailable instead of guessed so the operator knows exactly what
still requires a manual Settings-page snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://api.github.com"


def headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-sdlc-public-settings-snapshot",
    }


def get_json(repository: str, token: str, path: str) -> tuple[int, Any | None]:
    url = f"{API_ROOT}/repos/{repository}{path}"
    req = urllib.request.Request(url, headers=headers(token))
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, None


def name_fingerprint(names: list[str]) -> dict[str, Any]:
    normalized = "\n".join(sorted(names)).encode("utf-8")
    return {
        "count": len(names),
        "names_sha256": hashlib.sha256(normalized).hexdigest(),
    }


def enabled(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("enabled")
    return None


def sanitize_protection(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("required_status_checks") or {}
    reviews = payload.get("required_pull_request_reviews") or {}
    checks = status.get("checks") or []
    contexts = sorted(
        {
            item.get("context")
            for item in checks
            if isinstance(item, dict) and isinstance(item.get("context"), str)
        }
        | {
            item
            for item in (status.get("contexts") or [])
            if isinstance(item, str)
        }
    )
    return {
        "required_status_checks": {
            "strict": status.get("strict") if status else None,
            "contexts": contexts,
        },
        "enforce_admins": enabled(payload.get("enforce_admins")),
        "pull_request_reviews": {
            "dismiss_stale_reviews": reviews.get("dismiss_stale_reviews") if reviews else None,
            "require_code_owner_reviews": reviews.get("require_code_owner_reviews") if reviews else None,
            "required_approving_review_count": reviews.get("required_approving_review_count") if reviews else None,
            "require_last_push_approval": reviews.get("require_last_push_approval") if reviews else None,
        },
        "required_linear_history": enabled(payload.get("required_linear_history")),
        "allow_force_pushes": enabled(payload.get("allow_force_pushes")),
        "allow_deletions": enabled(payload.get("allow_deletions")),
        "block_creations": enabled(payload.get("block_creations")),
        "required_conversation_resolution": enabled(payload.get("required_conversation_resolution")),
        "lock_branch": enabled(payload.get("lock_branch")),
    }


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repository or repository.count("/") != 1:
        print("GITHUB_REPOSITORY=owner/repo is required", file=sys.stderr)
        return 2
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    snapshot: dict[str, Any] = {
        "repository": repository,
        "endpoints": {},
    }

    repo_status, repo = get_json(repository, token, "")
    snapshot["endpoints"]["repository"] = repo_status
    if repo_status != 200 or not isinstance(repo, dict):
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        print("SETTINGS-SNAPSHOT: PARTIAL (repository metadata unavailable)")
        return 0

    default_branch = str(repo.get("default_branch") or "main")
    snapshot["repository_settings"] = {
        "visibility": repo.get("visibility"),
        "default_branch": default_branch,
        "allow_merge_commit": repo.get("allow_merge_commit"),
        "allow_squash_merge": repo.get("allow_squash_merge"),
        "allow_rebase_merge": repo.get("allow_rebase_merge"),
        "allow_auto_merge": repo.get("allow_auto_merge"),
        "allow_update_branch": repo.get("allow_update_branch"),
        "delete_branch_on_merge": repo.get("delete_branch_on_merge"),
        "web_commit_signoff_required": repo.get("web_commit_signoff_required"),
    }

    actions_status, actions = get_json(repository, token, "/actions/permissions")
    snapshot["endpoints"]["actions_permissions"] = actions_status
    if actions_status == 200 and isinstance(actions, dict):
        snapshot["actions_permissions"] = {
            "enabled": actions.get("enabled"),
            "allowed_actions": actions.get("allowed_actions"),
            "sha_pinning_required": actions.get("sha_pinning_required"),
        }

    workflow_status, workflow = get_json(repository, token, "/actions/permissions/workflow")
    snapshot["endpoints"]["workflow_permissions"] = workflow_status
    if workflow_status == 200 and isinstance(workflow, dict):
        snapshot["workflow_permissions"] = {
            "default_workflow_permissions": workflow.get("default_workflow_permissions"),
            "can_approve_pull_request_reviews": workflow.get("can_approve_pull_request_reviews"),
        }

    branch_path = urllib.parse.quote(default_branch, safe="")
    branch_status, branch = get_json(repository, token, f"/branches/{branch_path}")
    snapshot["endpoints"]["default_branch"] = branch_status
    if branch_status == 200 and isinstance(branch, dict):
        snapshot["default_branch"] = {"protected": branch.get("protected")}

    protection_status, protection = get_json(repository, token, f"/branches/{branch_path}/protection")
    snapshot["endpoints"]["branch_protection"] = protection_status
    if protection_status == 200 and isinstance(protection, dict):
        snapshot["branch_protection"] = sanitize_protection(protection)

    rules_status, rulesets = get_json(repository, token, "/rulesets?includes_parents=true&per_page=100")
    snapshot["endpoints"]["rulesets"] = rules_status
    if rules_status == 200 and isinstance(rulesets, list):
        snapshot["rulesets"] = [
            {
                "name": item.get("name"),
                "target": item.get("target"),
                "enforcement": item.get("enforcement"),
                "source_type": item.get("source_type"),
            }
            for item in rulesets
            if isinstance(item, dict)
        ]

    env_status, environments = get_json(repository, token, "/environments?per_page=100")
    snapshot["endpoints"]["environments"] = env_status
    if env_status == 200 and isinstance(environments, dict):
        names = [
            str(item.get("name"))
            for item in environments.get("environments", [])
            if isinstance(item, dict) and item.get("name") is not None
        ]
        snapshot["environments"] = name_fingerprint(names)

    var_status, variables = get_json(repository, token, "/actions/variables?per_page=100")
    snapshot["endpoints"]["repository_variables"] = var_status
    if var_status == 200 and isinstance(variables, dict):
        names = [
            str(item.get("name"))
            for item in variables.get("variables", [])
            if isinstance(item, dict) and item.get("name") is not None
        ]
        snapshot["repository_variables"] = name_fingerprint(names)

    critical = {
        "actions_permissions",
        "workflow_permissions",
        "default_branch",
        "branch_protection",
        "rulesets",
        "environments",
        "repository_variables",
    }
    unavailable = sorted(
        key for key in critical if snapshot["endpoints"].get(key) not in {200, 404}
    )

    # 404 is a meaningful, recordable state for optional configuration such as
    # branch protection. Permission failures (401/403) remain manual gates.
    result = "COMPLETE" if not unavailable else "PARTIAL"
    snapshot["manual_follow_up"] = unavailable
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    print(f"SETTINGS-SNAPSHOT: {result}")
    if unavailable:
        print("Manual Settings-page capture still required for: " + ", ".join(unavailable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
