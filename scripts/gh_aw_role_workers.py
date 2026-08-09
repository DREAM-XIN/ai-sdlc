#!/usr/bin/env python3
"""Validated registry for specialized autonomous gh-aw role-worker variants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from gh_aw_provider_registry import RegistryValidationError, load_registry
from gh_aw_profile_routing import load_routing_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "runtimes" / "gh-aw" / "role-workers.yaml"
AUTHORING_ROLE_STAGES = {("product", "requirement"), ("architect", "design"), ("orchestrator", "plan")}
GATE_ROLE_STAGES = {("reviewer", "code-review"), ("qa", "verification")}
ALLOWED_ROLE_STAGES = AUTHORING_ROLE_STAGES | GATE_ROLE_STAGES


class RoleWorkerError(ValueError):
    pass


@dataclass(frozen=True)
class RoleWorker:
    id: str
    role: str
    stage: str
    profile: str
    worker_source: str
    worker_workflow: str


def _safe_source(value: str) -> bool:
    path = PurePosixPath(value)
    return value.startswith(".github/workflows/") and path.suffix == ".md" and not path.is_absolute() and ".." not in path.parts


def _safe_workflow(value: str) -> bool:
    path = PurePosixPath(value)
    return "/" not in value and value.endswith(".lock.yml") and not path.is_absolute() and ".." not in path.parts


def load_role_workers(path: Path = DEFAULT_PATH):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != "0.1.0":
        raise RoleWorkerError("role-worker registry version must be 0.1.0")
    rows = data.get("workers")
    if not isinstance(rows, list) or not rows:
        raise RoleWorkerError("role-worker registry must contain workers")

    profiles = load_registry()
    routing = load_routing_policy(registry=profiles)
    allowed_profiles = {(rule.role, rule.stage): tuple(rule.candidates) for rule in routing.rules if (rule.role, rule.stage) in ALLOWED_ROLE_STAGES}

    workers = []
    seen_ids, seen_keys, seen_sources, seen_workflows = set(), set(), set(), set()
    required = {"id", "role", "stage", "profile", "worker_source", "worker_workflow"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise RoleWorkerError("each role-worker entry must contain only the required fields")
        key = (row["role"], row["stage"])
        triple = (row["role"], row["stage"], row["profile"])
        if key not in ALLOWED_ROLE_STAGES:
            raise RoleWorkerError(f"unsupported autonomous specialized role/stage: {key[0]}/{key[1]}")
        if row["id"] in seen_ids or triple in seen_keys:
            raise RoleWorkerError("duplicate role-worker identity")
        try:
            profiles.require_profile(row["profile"])
        except RegistryValidationError as exc:
            raise RoleWorkerError(f"unknown profile: {row['profile']}") from exc
        if row["profile"] not in allowed_profiles.get(key, ()):
            raise RoleWorkerError(f"profile {row['profile']} is not allowed by trusted routing for {key[0]}/{key[1]}")
        if not _safe_source(row["worker_source"]) or not _safe_workflow(row["worker_workflow"]):
            raise RoleWorkerError("role-worker source/workflow identity is not canonical")
        if row["worker_source"] in seen_sources or row["worker_workflow"] in seen_workflows:
            raise RoleWorkerError("role-worker source/workflow identities must be unique")
        workers.append(RoleWorker(**row))
        seen_ids.add(row["id"]); seen_keys.add(triple); seen_sources.add(row["worker_source"]); seen_workflows.add(row["worker_workflow"])

    expected = {
        ("product", "requirement", "claude"), ("product", "requirement", "copilot"),
        ("architect", "design", "claude"), ("architect", "design", "copilot"),
        ("orchestrator", "plan", "codex"), ("orchestrator", "plan", "copilot"),
        ("reviewer", "code-review", "claude"), ("reviewer", "code-review", "copilot"),
        ("qa", "verification", "gemini"), ("qa", "verification", "copilot"),
    }
    if seen_keys != expected:
        raise RoleWorkerError(f"unexpected autonomous specialized worker set: {sorted(seen_keys)}")
    return tuple(workers)


def resolve_role_worker(role: str, stage: str, profile: str, path: Path = DEFAULT_PATH) -> RoleWorker:
    matches = [w for w in load_role_workers(path) if (w.role, w.stage, w.profile) == (role, stage, profile)]
    if len(matches) != 1:
        raise RoleWorkerError(f"no unique role-worker for {role}/{stage}/{profile}")
    return matches[0]


def require_role_worker_workflow(role: str, stage: str, workflow: str, path: Path = DEFAULT_PATH) -> RoleWorker:
    matches = [w for w in load_role_workers(path) if (w.role, w.stage, w.worker_workflow) == (role, stage, workflow)]
    if len(matches) != 1:
        raise RoleWorkerError(f"workflow {workflow!r} is not a trusted worker for {role}/{stage}")
    return matches[0]


if __name__ == "__main__":
    for worker in load_role_workers():
        print(f"{worker.role}/{worker.stage}/{worker.profile} -> {worker.worker_workflow}")
