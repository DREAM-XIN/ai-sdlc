#!/usr/bin/env python3
"""Static security validation for autonomous Reviewer/QA worker sources and locks."""

from __future__ import annotations

from pathlib import Path

from gh_aw_role_workers import GATE_ROLE_STAGES, load_role_workers
from validate_gh_aw_gate_provenance import main as validate_gate_provenance

ROOT = Path(__file__).resolve().parents[1]
BANNED_SOURCE_TOKENS = ("create-pull-request:","push-to-pull-request-branch:","create_pull_request","push_to_pull_request_branch")
BANNED_LOCK_TOKENS = ("create-pull-request","push-to-pull-request-branch","create_pull_request","push_to_pull_request_branch")


def require(condition: bool, message: str):
    if not condition: raise AssertionError(message)


def main():
    workers=[w for w in load_role_workers() if (w.role,w.stage) in GATE_ROLE_STAGES]
    require(len(workers)==4,"expected exactly four Gate-role workers")
    for worker in workers:
        source_path=ROOT/worker.worker_source; lock_path=ROOT/".github"/"workflows"/worker.worker_workflow
        require(source_path.is_file(),f"missing Gate worker source: {worker.worker_source}"); require(lock_path.is_file(),f"missing Gate worker lock: {worker.worker_workflow}")
        source=source_path.read_text(encoding="utf-8"); lock=lock_path.read_text(encoding="utf-8")
        require("permissions: read-all" in source,f"{worker.id}: source must default to read-all"); require("  add-comment:" in source,f"{worker.id}: add-comment Safe Output required"); require("ref: ${{ inputs.candidate_head_sha }}" in source,f"{worker.id}: checkout must pin candidate SHA")
        require("fromJSON(inputs.task_payload).task.id" in source,f"{worker.id}: trusted task id required"); require("SOURCE_RUN_ID: ${{ github.run_id }}" in source,f"{worker.id}: source run id required"); require("SOURCE_WORKFLOW_REF: ${{ github.workflow_ref }}" in source,f"{worker.id}: workflow ref required")
        for token in BANNED_SOURCE_TOKENS: require(token not in source,f"{worker.id}: banned source-write token: {token}")
        for token in BANNED_LOCK_TOKENS: require(token not in lock,f"{worker.id}: compiled lock banned source-write token: {token}")
        require("AI-SDLC-GATE-RESULT" in source,f"{worker.id}: Gate result marker required"); require("ai-sdlc-gh-aw-gate-result.yml" in source,f"{worker.id}: Gate collector required")
    validate_gate_provenance(); print("gh-aw Gate-role read-only worker security validation passed")


if __name__=="__main__": main()
