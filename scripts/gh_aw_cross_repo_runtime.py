#!/usr/bin/env python3
"""Trusted cross-repository gh-aw runtime installation helpers."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from gh_aw_provider_registry import (
    DEFAULT_REGISTRY,
    RegistryValidationError,
    load_registry,
)

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")


def parse_repository(repository: str) -> dict:
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("target_repository must be owner/repo")
    owner, repo_name = repository.split("/", 1)
    return {"repository": repository, "owner": owner, "repo_name": repo_name}


def trusted_worker_workflows(registry: Path = DEFAULT_REGISTRY) -> set[str]:
    try:
        validated = load_registry(registry)
    except RegistryValidationError as exc:
        raise ValueError(str(exc)) from None
    return set(validated.trusted_worker_workflows())


def validate_worker_workflow(worker_workflow: str, registry: Path = DEFAULT_REGISTRY) -> dict:
    if not WORKFLOW_RE.fullmatch(worker_workflow):
        raise ValueError("worker_workflow must be a workflow filename")
    try:
        profile = load_registry(registry).require_worker_workflow(worker_workflow)
    except RegistryValidationError as exc:
        raise ValueError(str(exc)) from None
    return {
        "worker_workflow": worker_workflow,
        "trusted": True,
        "profile": profile.profile_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate trusted cross-repository gh-aw runtime identity"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    target = subparsers.add_parser("target")
    target.add_argument("repository")
    worker = subparsers.add_parser("worker")
    worker.add_argument("worker_workflow")
    args = parser.parse_args()

    try:
        result = (
            parse_repository(args.repository)
            if args.command == "target"
            else validate_worker_workflow(args.worker_workflow)
        )
    except ValueError as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, separators=(",", ":")))
        return 2
    print(json.dumps({"status": "VALID", **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
