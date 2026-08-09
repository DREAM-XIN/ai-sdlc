#!/usr/bin/env python3
"""Plan or delete stale publication branches using GitHub PR metadata.

This is the second-stage companion to prune_merged_branches.sh. The ancestry
helper intentionally keeps squash/rebase and intermediate-base PR heads. This
script may remove those refs only when GitHub records the same-repository PR as
merged, while protecting every ref used by an open PR.

Closed-but-unmerged branches are never selected except for the small explicit
allowlist below, whose PRs were manually classified as superseded/temporary
while preparing this repository for publication.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from typing import Any, Iterable

ELIGIBLE_PREFIXES = (
    "agent/",
    "chore/",
    "docs/",
    "dogfood/",
    "feat/",
    "feature/",
    "fix/",
    "gh-aw/",
    "recovery/",
    "test/",
)
PROTECTED_PREFIXES = ("release/", "bootstrap/")
EXPLICIT_CLOSED_UNMERGED_ALLOWLIST = {
    "fix/deepseek-github-tools-token",          # PR #132, superseded by #133
    "gh-aw/compile-gemini-0.52.0",              # PR #81, superseded by #170
    "gh-aw/compile-multi-engine-v0.83.4",        # PR #64, superseded by #66
    "feature/v0.1-release-readiness",            # PR #58, superseded by #60
    "test/persist-workflow-smoke-20260807",      # PR #47, temporary smoke only
}
MAX_AUTOMATIC_DELETIONS = 125


def is_eligible(branch: str) -> bool:
    return branch != "main" and branch.startswith(ELIGIBLE_PREFIXES) and not branch.startswith(PROTECTED_PREFIXES)


def same_repo_head(pr: dict[str, Any], repository: str) -> str | None:
    head = pr.get("head") or {}
    repo = head.get("repo") or {}
    ref = head.get("ref")
    if repo.get("full_name") != repository or not isinstance(ref, str):
        return None
    return ref


def select_candidates(
    branches: Iterable[str],
    prs: Iterable[dict[str, Any]],
    repository: str,
) -> tuple[list[str], set[str]]:
    branch_set = set(branches)
    protected = {"main"}
    merged_heads: set[str] = set()

    for pr in prs:
        head_ref = same_repo_head(pr, repository)
        base = pr.get("base") or {}
        base_ref = base.get("ref")

        if pr.get("state") == "open":
            if head_ref:
                protected.add(head_ref)
            if isinstance(base_ref, str):
                protected.add(base_ref)
            continue

        if pr.get("merged_at") and head_ref and is_eligible(head_ref):
            merged_heads.add(head_ref)

    selected = (merged_heads | EXPLICIT_CLOSED_UNMERGED_ALLOWLIST) & branch_set
    selected = {branch for branch in selected if is_eligible(branch) and branch not in protected}
    return sorted(selected), protected


def github_get_json(url: str, token: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-publication-branch-pruner",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def list_pull_requests(repository: str, token: str) -> list[dict[str, Any]]:
    owner, repo = repository.split("/", 1)
    prs: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls"
            f"?state=all&sort=updated&direction=desc&per_page=100&page={page}"
        )
        batch = github_get_json(url, token)
        if not isinstance(batch, list):
            raise RuntimeError("unexpected GitHub pull-request payload")
        prs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return prs


def list_remote_branches(remote: str) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-remote", "--heads", remote],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    branches: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        _sha, ref = line.split("\t", 1)
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.append(ref[len(prefix) :])
    return branches


def delete_branch(remote: str, branch: str) -> bool:
    completed = subprocess.run(["git", "push", remote, "--delete", branch], check=False)
    return completed.returncode == 0


def self_test() -> None:
    repository = "example/control"
    branches = {
        "main",
        "feature/merged-via-squash",
        "fix/open-head",
        "dogfood/open-base",
        "feature/ordinary-closed-unmerged",
        "fix/deepseek-github-tools-token",
        "release/v0.1-rebased",
        "bootstrap/protocol-v0.1",
    }
    prs = [
        {
            "state": "closed",
            "merged_at": "2026-08-09T00:00:00Z",
            "head": {"ref": "feature/merged-via-squash", "repo": {"full_name": repository}},
            "base": {"ref": "main"},
        },
        {
            "state": "open",
            "merged_at": None,
            "head": {"ref": "fix/open-head", "repo": {"full_name": repository}},
            "base": {"ref": "dogfood/open-base"},
        },
        {
            "state": "closed",
            "merged_at": None,
            "head": {"ref": "feature/ordinary-closed-unmerged", "repo": {"full_name": repository}},
            "base": {"ref": "main"},
        },
        {
            "state": "closed",
            "merged_at": "2026-08-09T00:00:00Z",
            "head": {"ref": "release/v0.1-rebased", "repo": {"full_name": repository}},
            "base": {"ref": "main"},
        },
        {
            "state": "closed",
            "merged_at": "2026-08-09T00:00:00Z",
            "head": {"ref": "bootstrap/protocol-v0.1", "repo": {"full_name": repository}},
            "base": {"ref": "main"},
        },
    ]
    selected, protected = select_candidates(branches, prs, repository)
    expected = ["feature/merged-via-squash", "fix/deepseek-github-tools-token"]
    if selected != expected:
        raise AssertionError(f"unexpected candidates: {selected!r} != {expected!r}")
    if "fix/open-head" not in protected or "dogfood/open-base" not in protected:
        raise AssertionError("open PR head/base refs were not protected")
    if "feature/ordinary-closed-unmerged" in selected:
        raise AssertionError("ordinary closed-unmerged branch became deletable")
    if "release/v0.1-rebased" in selected or "bootstrap/protocol-v0.1" in selected:
        raise AssertionError("long-lived protected prefix became deletable")
    print("Publication PR-branch pruning selection self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete selected branches")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repository or repository.count("/") != 1:
        print("GITHUB_REPOSITORY=owner/repo is required", file=sys.stderr)
        return 2
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    branches = list_remote_branches(args.remote)
    prs = list_pull_requests(repository, token)
    candidates, protected = select_candidates(branches, prs, repository)

    print(f"Remote branches observed: {len(branches)}")
    print(f"Open-PR protected refs: {len(protected - {'main'})}")
    print(f"PR-metadata branches eligible for deletion: {len(candidates)}")
    for branch in candidates:
        print(f"  {branch}")

    if len(candidates) > MAX_AUTOMATIC_DELETIONS:
        print(
            f"refusing deletion: {len(candidates)} candidates exceed safety cap {MAX_AUTOMATIC_DELETIONS}",
            file=sys.stderr,
        )
        return 2

    if not args.apply:
        print("Dry run only. Use --apply with CONFIRM_DELETE_MERGED_PR_BRANCHES=yes after review.")
        return 0

    if os.environ.get("CONFIRM_DELETE_MERGED_PR_BRANCHES") != "yes":
        print("refusing deletion: set CONFIRM_DELETE_MERGED_PR_BRANCHES=yes", file=sys.stderr)
        return 2

    failed: list[str] = []
    deleted = 0
    for branch in candidates:
        print(f"Deleting {args.remote}/{branch}")
        if delete_branch(args.remote, branch):
            deleted += 1
        else:
            failed.append(branch)
            print(f"FAILED to delete {args.remote}/{branch}", file=sys.stderr)

    print(f"Deleted {deleted} PR-metadata publication branches.")
    if failed:
        print(f"Failed deletions ({len(failed)}):", file=sys.stderr)
        for branch in failed:
            print(f"  {branch}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
