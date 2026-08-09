#!/usr/bin/env python3
"""Static security validation for autonomous Reviewer/QA worker sources and locks."""

from __future__ import annotations

from pathlib import Path

from gh_aw_role_workers import load_role_workers

ROOT = Path(__file__).resolve().parents[1]
BANNED_SOURCE_TOKENS = (
    "create-pull-request:",
    "push-to-pull-request-branch:",
    "create_pull_request",
    "push_to_pull_request_branch",
)
BANNED_LOCK_TOKENS = (
    "create-pull-request",
    "push-to-pull-request-branch",
    "create_pull_request",
    "push_to_pull_request_branch",
)


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def main():
    workers = load_role_workers()
    require(len(workers) == 4, "expected exactly four Gate-role workers")
    for worker in workers:
        source_path = ROOT / worker.worker_source
        lock_path = ROOT / ".github" / "workflows" / worker.worker_workflow
        require(source_path.is_file(), f"missing Gate worker source: {worker.worker_source}")
        require(lock_path.is_file(), f"missing Gate worker lock: {worker.worker_workflow}")
        source = source_path.read_text(encoding="utf-8")
        lock = lock_path.read_text(encoding="utf-8")

        require("permissions: read-all" in source, f"{worker.id}: source must default to read-all")
        require("  add-comment:" in source, f"{worker.id}: add-comment Safe Output is required")
        require("ref: ${{ inputs.candidate_head_sha }}" in source, f"{worker.id}: checkout must pin candidate SHA")
        require("candidate_head_sha" in source and "candidate_pr_number" in source, f"{worker.id}: candidate identity inputs are required")
        for token in BANNED_SOURCE_TOKENS:
            require(token not in source, f"{worker.id}: banned source-write capability token present: {token}")
        for token in BANNED_LOCK_TOKENS:
            require(token not in lock, f"{worker.id}: compiled lock contains banned source-write capability: {token}")
        require("AI-SDLC-GATE-RESULT" in source, f"{worker.id}: machine Gate result envelope marker is required")
        require("ai-sdlc-gh-aw-gate-result.yml" in source, f"{worker.id}: trusted Gate collector dispatch is required")

    print("gh-aw Gate-role read-only worker security validation passed")


if __name__ == "__main__":
    main()
