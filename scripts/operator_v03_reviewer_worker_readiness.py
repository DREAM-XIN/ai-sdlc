#!/usr/bin/env python3
"""Trusted v0.3 Reviewer Worker provider readiness selection.

This module is deliberately side-effect free. It does not dispatch a Worker,
read GitHub APIs, create Store state, or expose credential values. The final
trusted-main #221 driver supplies only credential *presence* after Actions has
mapped installation secrets into its process environment.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import yaml


class ReviewerWorkerReadinessError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReviewerWorkerOption:
    worker_id: str
    role: str
    stage: str
    profile: str
    workflow_file: str
    credential_env: str


@dataclass(frozen=True)
class ReviewerWorkerSelection:
    worker_id: str
    role: str
    stage: str
    profile: str
    workflow_file: str
    credential_env: str
    credential_present: bool
    selection_policy: str


V03_REVIEWER_OPTIONS = (
    ReviewerWorkerOption(
        worker_id="code-review-reviewer-claude",
        role="reviewer",
        stage="code-review",
        profile="claude",
        workflow_file="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
        credential_env="ANTHROPIC_API_KEY",
    ),
    ReviewerWorkerOption(
        worker_id="code-review-reviewer-copilot",
        role="reviewer",
        stage="code-review",
        profile="copilot",
        workflow_file="ai-sdlc-gh-aw-reviewer-copilot.lock.yml",
        credential_env="COPILOT_GITHUB_TOKEN",
    ),
)
SELECTION_POLICY = "v03-frozen-reviewer-provider-order/v1"


def _load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReviewerWorkerReadinessError("WORKER_REGISTRY_INVALID", f"cannot parse {path}") from exc
    if not isinstance(value, dict):
        raise ReviewerWorkerReadinessError("WORKER_REGISTRY_INVALID", f"{path} is not a mapping")
    return value


def _registry_reviewers(path: Path) -> tuple[dict, ...]:
    registry = _load_yaml(path)
    if registry.get("version") != "0.1.0" or not isinstance(registry.get("workers"), list):
        raise ReviewerWorkerReadinessError("WORKER_REGISTRY_INVALID", "gh-aw role registry version/workers are invalid")
    rows = tuple(
        row
        for row in registry["workers"]
        if isinstance(row, dict) and row.get("role") == "reviewer" and row.get("stage") == "code-review"
    )
    if len(rows) != len(V03_REVIEWER_OPTIONS):
        raise ReviewerWorkerReadinessError(
            "WORKER_REGISTRY_DRIFT",
            "v0.3 Reviewer registry must contain exactly the reviewed Claude/Copilot options",
        )
    return rows


def _manifest_declared_secrets(workflow_path: Path) -> frozenset[str]:
    try:
        first_lines = workflow_path.read_text(encoding="utf-8").splitlines()[:8]
    except Exception as exc:
        raise ReviewerWorkerReadinessError(
            "WORKER_WORKFLOW_INVALID", f"cannot read locked workflow {workflow_path}"
        ) from exc
    prefix = "# gh-aw-manifest: "
    manifest_line = next((line for line in first_lines if line.startswith(prefix)), None)
    if manifest_line is None:
        raise ReviewerWorkerReadinessError(
            "WORKER_WORKFLOW_INVALID",
            f"locked workflow {workflow_path.name} lacks gh-aw manifest metadata",
        )
    try:
        manifest = json.loads(manifest_line[len(prefix) :])
    except Exception as exc:
        raise ReviewerWorkerReadinessError(
            "WORKER_WORKFLOW_INVALID", f"locked workflow {workflow_path.name} manifest is invalid JSON"
        ) from exc
    secrets = manifest.get("secrets") if isinstance(manifest, dict) else None
    if not isinstance(secrets, list) or any(not isinstance(value, str) or not value for value in secrets):
        raise ReviewerWorkerReadinessError(
            "WORKER_WORKFLOW_INVALID", f"locked workflow {workflow_path.name} secret inventory is invalid"
        )
    return frozenset(secrets)


def validate_v03_reviewer_registry(*, registry_path: Path, workflow_dir: Path) -> None:
    rows = _registry_reviewers(registry_path)
    by_id = {str(row.get("id") or ""): row for row in rows}
    if len(by_id) != len(rows):
        raise ReviewerWorkerReadinessError("WORKER_REGISTRY_DRIFT", "duplicate Reviewer worker id")

    for option in V03_REVIEWER_OPTIONS:
        row = by_id.get(option.worker_id)
        expected = {
            "id": option.worker_id,
            "role": option.role,
            "stage": option.stage,
            "profile": option.profile,
            "worker_workflow": option.workflow_file,
        }
        if row is None or any(row.get(key) != value for key, value in expected.items()):
            raise ReviewerWorkerReadinessError(
                "WORKER_REGISTRY_DRIFT",
                f"Reviewer worker {option.worker_id} differs from the frozen v0.3 binding",
            )
        source = str(row.get("worker_source") or "")
        if source != f".github/workflows/{option.workflow_file.removesuffix('.lock.yml')}.md":
            raise ReviewerWorkerReadinessError(
                "WORKER_REGISTRY_DRIFT",
                f"Reviewer worker {option.worker_id} source/workflow binding drifted",
            )
        workflow_path = workflow_dir / option.workflow_file
        declared = _manifest_declared_secrets(workflow_path)
        if option.credential_env not in declared:
            raise ReviewerWorkerReadinessError(
                "WORKER_WORKFLOW_INVALID",
                f"locked workflow {option.workflow_file} no longer declares its reviewed provider credential",
            )
        text = workflow_path.read_text(encoding="utf-8")
        if "workflow_dispatch:" not in text or "run-name: AI-SDLC gh-aw" not in text:
            raise ReviewerWorkerReadinessError(
                "WORKER_WORKFLOW_INVALID",
                f"locked workflow {option.workflow_file} lost reviewed dispatch/run-name authority",
            )


def select_v03_reviewer_worker(
    *,
    registry_path: Path,
    workflow_dir: Path,
    credential_presence: Mapping[str, bool],
) -> ReviewerWorkerSelection:
    """Return one frozen configured Reviewer or fail before any external effect."""
    validate_v03_reviewer_registry(registry_path=registry_path, workflow_dir=workflow_dir)
    allowed_keys = {option.credential_env for option in V03_REVIEWER_OPTIONS}
    unknown = set(credential_presence) - allowed_keys
    if unknown:
        raise ReviewerWorkerReadinessError(
            "WORKER_PROVIDER_INPUT_INVALID",
            "Reviewer readiness received non-frozen credential selectors",
        )
    for key, value in credential_presence.items():
        if type(value) is not bool:
            raise ReviewerWorkerReadinessError(
                "WORKER_PROVIDER_INPUT_INVALID",
                f"credential presence for {key} must be an exact boolean",
            )

    for option in V03_REVIEWER_OPTIONS:
        if credential_presence.get(option.credential_env, False):
            return ReviewerWorkerSelection(
                worker_id=option.worker_id,
                role=option.role,
                stage=option.stage,
                profile=option.profile,
                workflow_file=option.workflow_file,
                credential_env=option.credential_env,
                credential_present=True,
                selection_policy=SELECTION_POLICY,
            )
    raise ReviewerWorkerReadinessError(
        "WORKER_PROVIDER_UNAVAILABLE",
        "no frozen v0.3 Reviewer provider credential is configured; external launch is not authorized",
    )


def selection_from_environment(*, registry_path: Path, workflow_dir: Path) -> ReviewerWorkerSelection:
    """Convert secret values to presence bits without returning or logging them."""
    presence = {
        option.credential_env: bool(os.environ.get(option.credential_env, "").strip())
        for option in V03_REVIEWER_OPTIONS
    }
    return select_v03_reviewer_worker(
        registry_path=registry_path,
        workflow_dir=workflow_dir,
        credential_presence=presence,
    )


def public_selection(selection: ReviewerWorkerSelection) -> dict:
    result = asdict(selection)
    return result
