#!/usr/bin/env python3
"""Static security validation for autonomous Reviewer/QA worker sources and locks."""

from __future__ import annotations

from pathlib import Path

from gh_aw_role_workers import load_role_workers
from validate_gh_aw_gate_provenance import main as validate_gate_provenance

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
        require("fromJSON(inputs.task_payload).task.id" in source, f"{worker.id}: run/task provenance must derive task id from trusted task payload")
        require("SOURCE_RUN_ID: ${{ github.run_id }}" in source, f"{worker.id}: trusted source run id is required")
        require("SOURCE_WORKFLOW_REF: ${{ github.workflow_ref }}" in source, f"{worker.id}: trusted source workflow ref is required")
        require("--field task_id=\"$TRUSTED_TASK_ID\"" in source, f"{worker.id}: collector transport must include trusted task id")
        require("--field source_run_id=\"$SOURCE_RUN_ID\"" in source, f"{worker.id}: collector transport must include source run id")
        require("--field source_workflow_ref=\"$SOURCE_WORKFLOW_REF\"" in source, f"{worker.id}: collector transport must include source workflow ref")
        for token in BANNED_SOURCE_TOKENS:
            require(token not in source, f"{worker.id}: banned source-write capability token present: {token}")
        for token in BANNED_LOCK_TOKENS:
            require(token not in lock, f"{worker.id}: compiled lock contains banned source-write capability: {token}")
        require("AI-SDLC-GATE-RESULT" in source, f"{worker.id}: machine Gate result envelope marker is required")
        require("ai-sdlc-gh-aw-gate-result.yml" in source, f"{worker.id}: trusted Gate collector dispatch is required")

    validate_gate_provenance()
    print("gh-aw Gate-role read-only worker security validation passed")


if __name__ == "__main__":
    main()
