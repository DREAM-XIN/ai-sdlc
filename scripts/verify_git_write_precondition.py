#!/usr/bin/env python3
"""Fail closed when a write workspace is stale relative to its remote target branch."""

import argparse
import json
import subprocess
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed with {result.returncode}")
    return result


def verify_write_precondition(
    repo: Path,
    target_ref: str,
    default_branch: str,
    allow_default_branch: bool = False,
):
    repo = repo.resolve()
    if not default_branch:
        return {"outcome": "INVALID", "errors": ["default_branch is required for write operations"]}

    ref_check = git(repo, "check-ref-format", "--branch", target_ref, check=False)
    if ref_check.returncode != 0:
        return {"outcome": "INVALID", "errors": [f"invalid target branch: {target_ref}"]}

    if target_ref == default_branch and not allow_default_branch:
        return {
            "outcome": "INVALID",
            "errors": ["Refusing direct persistence to default branch without allow_default_branch=true"],
        }

    try:
        base_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        remote = git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{target_ref}").stdout.strip()
    except RuntimeError as exc:
        return {"outcome": "INVALID", "errors": [str(exc)]}

    if not remote:
        return {
            "outcome": "INVALID",
            "errors": [f"target branch does not exist on origin: {target_ref}"],
        }
    remote_sha = remote.split()[0]
    if remote_sha != base_sha:
        return {
            "outcome": "STALE",
            "errors": [
                f"stale workspace for {target_ref}: checkout={base_sha} remote={remote_sha}; refresh state before persisting"
            ],
            "base_sha": base_sha,
            "remote_sha": remote_sha,
        }

    return {
        "outcome": "READY",
        "errors": [],
        "base_sha": base_sha,
        "remote_sha": remote_sha,
        "target_ref": target_ref,
    }


def main():
    parser = argparse.ArgumentParser(description="Verify an optimistic Git branch write precondition")
    parser.add_argument("--repo-dir", type=Path, default=Path("."))
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--allow-default-branch", action="store_true")
    args = parser.parse_args()

    result = verify_write_precondition(
        args.repo_dir,
        args.target_ref,
        args.default_branch,
        allow_default_branch=args.allow_default_branch,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["outcome"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
