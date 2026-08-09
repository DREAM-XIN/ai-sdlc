#!/usr/bin/env python3
"""Validate autonomous Gate-result provenance against one trusted Actions run."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath

from gh_aw_role_workers import RoleWorkerError, require_role_worker_workflow


class GateProvenanceError(ValueError):
    pass


def dispatch_key(feature_id: str, task_id: str, revision: int, head_sha: str) -> str:
    if not feature_id or not task_id:
        raise GateProvenanceError("feature_id and task_id are required")
    if not isinstance(revision, int) or revision < 0:
        raise GateProvenanceError("revision must be a non-negative integer")
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise GateProvenanceError("candidate head must be a lowercase 40-character SHA")
    return f"{feature_id}:{task_id}:r{revision}:{head_sha}"


def validate_run(
    run: dict,
    *,
    source_run_id: int,
    source_workflow_ref: str,
    control_repository: str,
    default_branch: str,
    role: str,
    stage: str,
    feature_id: str,
    task_id: str,
    expected_revision: int,
    candidate_head_sha: str,
):
    if not isinstance(run, dict):
        raise GateProvenanceError("trusted Actions run metadata is required")
    if run.get("id") != source_run_id:
        raise GateProvenanceError("source run id does not match trusted Actions metadata")
    if run.get("repository", {}).get("full_name") != control_repository:
        raise GateProvenanceError("source run repository is not the trusted control repository")
    if run.get("event") != "workflow_dispatch":
        raise GateProvenanceError("source run was not created by workflow_dispatch")
    if run.get("head_branch") != default_branch:
        raise GateProvenanceError("source role-worker run is not pinned to the trusted default branch")

    path = run.get("path")
    if not isinstance(path, str) or not path.startswith(".github/workflows/"):
        raise GateProvenanceError("source run workflow path is not canonical")
    workflow = PurePosixPath(path).name
    if PurePosixPath(path) != PurePosixPath(".github/workflows") / workflow:
        raise GateProvenanceError("source run workflow path escapes the trusted workflow directory")
    try:
        worker = require_role_worker_workflow(role, stage, workflow)
    except RoleWorkerError as exc:
        raise GateProvenanceError("source run workflow is not registered for the trusted Gate role/stage") from exc

    expected_ref = f"{control_repository}/{path}@refs/heads/{default_branch}"
    if source_workflow_ref != expected_ref:
        raise GateProvenanceError("source workflow_ref does not match the trusted role-worker/default-branch identity")

    key = dispatch_key(feature_id, task_id, expected_revision, candidate_head_sha)
    label = "reviewer" if role == "reviewer" else "qa"
    expected_title = f"AI-SDLC gh-aw gate {label} {key}"
    if run.get("display_title") != expected_title:
        raise GateProvenanceError("source run title is not bound to the trusted Feature/task/revision/candidate identity")
    return worker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_json", type=Path)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--source-workflow-ref", required=True)
    parser.add_argument("--control-repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--feature-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--candidate-head-sha", required=True)
    args = parser.parse_args()
    try:
        worker = validate_run(
            json.loads(args.run_json.read_text(encoding="utf-8")),
            source_run_id=args.source_run_id,
            source_workflow_ref=args.source_workflow_ref,
            control_repository=args.control_repository,
            default_branch=args.default_branch,
            role=args.role,
            stage=args.stage,
            feature_id=args.feature_id,
            task_id=args.task_id,
            expected_revision=args.expected_revision,
            candidate_head_sha=args.candidate_head_sha,
        )
    except GateProvenanceError as exc:
        print(json.dumps({"outcome": "INVALID", "errors": [str(exc)]}, indent=2))
        raise SystemExit(2)
    print(json.dumps({"outcome": "VALID", "role_worker_id": worker.id}, sort_keys=True))


if __name__ == "__main__":
    main()
