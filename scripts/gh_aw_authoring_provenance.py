#!/usr/bin/env python3
"""Validate autonomous authoring provenance against one trusted Actions run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from gh_aw_role_workers import AUTHORING_ROLE_STAGES, RoleWorkerError, require_role_worker_workflow


class AuthoringProvenanceError(ValueError):
    pass


def dispatch_key(feature_id: str, task_id: str, revision: int) -> str:
    if not feature_id or not task_id:
        raise AuthoringProvenanceError("feature_id and task_id are required")
    if not isinstance(revision, int) or revision < 0:
        raise AuthoringProvenanceError("revision must be a non-negative integer")
    return f"{feature_id}:{task_id}:r{revision}"


def validate_run(run: dict, *, source_run_id: int, source_workflow_ref: str, control_repository: str,
                 default_branch: str, role: str, stage: str, feature_id: str, task_id: str,
                 expected_revision: int):
    if (role,stage) not in AUTHORING_ROLE_STAGES:
        raise AuthoringProvenanceError("role/stage is not an autonomous authoring identity")
    if not isinstance(run,dict) or run.get("id") != source_run_id:
        raise AuthoringProvenanceError("source run id does not match trusted Actions metadata")
    if run.get("repository",{}).get("full_name") != control_repository:
        raise AuthoringProvenanceError("source run repository is not the trusted control repository")
    if run.get("event") != "workflow_dispatch":
        raise AuthoringProvenanceError("source run was not created by workflow_dispatch")
    if run.get("head_branch") != default_branch:
        raise AuthoringProvenanceError("source authoring run is not pinned to the trusted default branch")
    path=run.get("path")
    if not isinstance(path,str) or not path.startswith(".github/workflows/"):
        raise AuthoringProvenanceError("source run workflow path is not canonical")
    workflow=PurePosixPath(path).name
    if PurePosixPath(path) != PurePosixPath(".github/workflows")/workflow:
        raise AuthoringProvenanceError("source run workflow path escapes the trusted workflow directory")
    try: worker=require_role_worker_workflow(role,stage,workflow)
    except RoleWorkerError as exc:
        raise AuthoringProvenanceError("source run workflow is not registered for the trusted authoring role/stage") from exc
    expected_ref=f"{control_repository}/{path}@refs/heads/{default_branch}"
    if source_workflow_ref != expected_ref:
        raise AuthoringProvenanceError("source workflow_ref does not match trusted role-worker/default-branch identity")
    expected_title=f"AI-SDLC gh-aw authoring {role} {dispatch_key(feature_id,task_id,expected_revision)}"
    if run.get("display_title") != expected_title:
        raise AuthoringProvenanceError("source run title is not bound to the trusted Feature/task/revision identity")
    return worker


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("run_json",type=Path)
    parser.add_argument("--source-run-id",required=True,type=int)
    parser.add_argument("--source-workflow-ref",required=True)
    parser.add_argument("--control-repository",required=True)
    parser.add_argument("--default-branch",required=True)
    parser.add_argument("--role",required=True)
    parser.add_argument("--stage",required=True)
    parser.add_argument("--feature-id",required=True)
    parser.add_argument("--task-id",required=True)
    parser.add_argument("--expected-revision",required=True,type=int)
    args=parser.parse_args()
    try:
        worker=validate_run(json.loads(args.run_json.read_text(encoding="utf-8")),source_run_id=args.source_run_id,
            source_workflow_ref=args.source_workflow_ref,control_repository=args.control_repository,
            default_branch=args.default_branch,role=args.role,stage=args.stage,feature_id=args.feature_id,
            task_id=args.task_id,expected_revision=args.expected_revision)
    except AuthoringProvenanceError as exc:
        print(json.dumps({"outcome":"INVALID","errors":[str(exc)]},indent=2)); raise SystemExit(2)
    print(json.dumps({"outcome":"VALID","role_worker_id":worker.id},sort_keys=True))


if __name__=="__main__": main()
