#!/usr/bin/env python3
"""Validate v0.3 Reviewer Worker provider readiness selection."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import yaml

from operator_v03_reviewer_worker_readiness import (
    ReviewerWorkerReadinessError,
    SELECTION_POLICY,
    V03_REVIEWER_OPTIONS,
    public_selection,
    select_v03_reviewer_worker,
    selection_from_environment,
    validate_v03_reviewer_registry,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/role-workers.yaml"
WORKFLOWS = ROOT / ".github/workflows"


def require(value, message):
    if not value:
        raise AssertionError(message)


def expect_error(code: str, fn):
    try:
        fn()
        raise AssertionError(f"expected {code}")
    except ReviewerWorkerReadinessError as exc:
        require(exc.code == code, f"expected {code}, got {exc.code}: {exc}")


def select(**presence):
    return select_v03_reviewer_worker(
        registry_path=REGISTRY,
        workflow_dir=WORKFLOWS,
        credential_presence=presence,
    )


def validate_selection_semantics():
    validate_v03_reviewer_registry(registry_path=REGISTRY, workflow_dir=WORKFLOWS)

    claude = select(ANTHROPIC_API_KEY=True, COPILOT_GITHUB_TOKEN=False)
    require(claude.worker_id == "code-review-reviewer-claude", claude)
    require(claude.workflow_file == "ai-sdlc-gh-aw-reviewer-claude.lock.yml", claude)
    require(claude.credential_env == "ANTHROPIC_API_KEY", claude)
    require(claude.selection_policy == SELECTION_POLICY, claude)

    copilot = select(ANTHROPIC_API_KEY=False, COPILOT_GITHUB_TOKEN=True)
    require(copilot.worker_id == "code-review-reviewer-copilot", copilot)
    require(copilot.workflow_file == "ai-sdlc-gh-aw-reviewer-copilot.lock.yml", copilot)
    require(copilot.credential_env == "COPILOT_GITHUB_TOKEN", copilot)

    both = select(ANTHROPIC_API_KEY=True, COPILOT_GITHUB_TOKEN=True)
    require(
        both.worker_id == V03_REVIEWER_OPTIONS[0].worker_id,
        "simultaneous credentials did not follow fixed reviewed provider order",
    )

    expect_error(
        "WORKER_PROVIDER_UNAVAILABLE",
        lambda: select(ANTHROPIC_API_KEY=False, COPILOT_GITHUB_TOKEN=False),
    )
    expect_error(
        "WORKER_PROVIDER_INPUT_INVALID",
        lambda: select(ANTHROPIC_API_KEY=False, COPILOT_GITHUB_TOKEN=False, RANDOM_TOKEN=True),
    )
    expect_error(
        "WORKER_PROVIDER_INPUT_INVALID",
        lambda: select_v03_reviewer_worker(
            registry_path=REGISTRY,
            workflow_dir=WORKFLOWS,
            credential_presence={"ANTHROPIC_API_KEY": 1, "COPILOT_GITHUB_TOKEN": False},
        ),
    )


def validate_secret_non_disclosure():
    old = {name: os.environ.get(name) for name in ("ANTHROPIC_API_KEY", "COPILOT_GITHUB_TOKEN")}
    secret_value = "super-secret-provider-value-that-must-not-leak"
    try:
        os.environ["ANTHROPIC_API_KEY"] = secret_value
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        selected = selection_from_environment(registry_path=REGISTRY, workflow_dir=WORKFLOWS)
        public = public_selection(selected)
        encoded = json.dumps(public, sort_keys=True)
        require(secret_value not in encoded, "provider secret leaked into public selection")
        require(public["credential_present"] is True, public)
        require(public["credential_env"] == "ANTHROPIC_API_KEY", public)
        require("credential_value" not in public and "token" not in public, public)

        # Whitespace-only installation values are not valid configured secrets.
        os.environ["ANTHROPIC_API_KEY"] = "   "
        expect_error(
            "WORKER_PROVIDER_UNAVAILABLE",
            lambda: selection_from_environment(registry_path=REGISTRY, workflow_dir=WORKFLOWS),
        )
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def validate_registry_drift_fails_closed():
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        workflow_dir = root / "workflows"
        workflow_dir.mkdir()
        for option in V03_REVIEWER_OPTIONS:
            (workflow_dir / option.workflow_file).write_text(
                (WORKFLOWS / option.workflow_file).read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        bad_registry = root / "role-workers.yaml"
        mutated = yaml.safe_load(yaml.safe_dump(registry))
        for row in mutated["workers"]:
            if row.get("id") == "code-review-reviewer-claude":
                row["worker_workflow"] = "ai-sdlc-gh-aw-reviewer-copilot.lock.yml"
        bad_registry.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")
        expect_error(
            "WORKER_REGISTRY_DRIFT",
            lambda: validate_v03_reviewer_registry(registry_path=bad_registry, workflow_dir=workflow_dir),
        )

        exact_registry = root / "role-workers-exact.yaml"
        exact_registry.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        copilot_path = workflow_dir / "ai-sdlc-gh-aw-reviewer-copilot.lock.yml"
        lines = copilot_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.startswith("# gh-aw-manifest: "):
                manifest = json.loads(line[len("# gh-aw-manifest: ") :])
                manifest["secrets"] = [value for value in manifest["secrets"] if value != "COPILOT_GITHUB_TOKEN"]
                lines[index] = "# gh-aw-manifest: " + json.dumps(manifest, separators=(",", ":"))
                break
        copilot_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        expect_error(
            "WORKER_WORKFLOW_INVALID",
            lambda: validate_v03_reviewer_registry(registry_path=exact_registry, workflow_dir=workflow_dir),
        )


def main():
    validate_selection_semantics()
    validate_secret_non_disclosure()
    validate_registry_drift_fails_closed()
    print("v0.3 Reviewer Worker readiness validation passed")
    print("- frozen Claude/Copilot Reviewer registry + locked workflow secret contract")
    print("- deterministic configured-provider selection; no caller-selected provider")
    print("- zero configured providers => WORKER_PROVIDER_UNAVAILABLE before external authority")
    print("- credential values never enter public selection/evidence")
    print("- registry/workflow/credential-contract drift fails closed")


if __name__ == "__main__":
    main()
