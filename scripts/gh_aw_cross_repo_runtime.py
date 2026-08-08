#!/usr/bin/env python3
"""Trusted cross-repository gh-aw runtime installation helpers."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")


def parse_repository(repository: str) -> dict:
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("target_repository must be owner/repo")
    owner, repo_name = repository.split("/", 1)
    return {"repository": repository, "owner": owner, "repo_name": repo_name}


def trusted_worker_workflows(registry: Path = REGISTRY) -> set[str]:
    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles") or {}
    workers = {
        cfg.get("worker_workflow")
        for cfg in profiles.values()
        if isinstance(cfg, dict) and cfg.get("worker_workflow")
    }
    if not workers or any(not WORKFLOW_RE.fullmatch(worker) for worker in workers):
        raise ValueError("trusted engine profile registry contains an invalid worker_workflow")
    return workers


def validate_worker_workflow(worker_workflow: str, registry: Path = REGISTRY) -> dict:
    if not WORKFLOW_RE.fullmatch(worker_workflow):
        raise ValueError("worker_workflow must be a workflow filename")
    allowed = trusted_worker_workflows(registry)
    if worker_workflow not in allowed:
        raise ValueError("worker_workflow is not registered in trusted gh-aw engine profiles")
    return {"worker_workflow": worker_workflow, "trusted": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate trusted cross-repository gh-aw runtime identity")
    subparsers = parser.add_subparsers(dest="command", required=True)
    target = subparsers.add_parser("target")
    target.add_argument("repository")
    worker = subparsers.add_parser("worker")
    worker.add_argument("worker_workflow")
    args = parser.parse_args()

    try:
        result = parse_repository(args.repository) if args.command == "target" else validate_worker_workflow(args.worker_workflow)
    except ValueError as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, separators=(",", ":")))
        return 2
    print(json.dumps({"status": "VALID", **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
