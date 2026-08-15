#!/usr/bin/env python3
"""Validate that legacy gh-aw effectful writers are quiesced for v0.3."""
from __future__ import annotations

from pathlib import Path

from verify_git_write_precondition import (
    LEGACY_GH_AW_EFFECTFUL_WORKFLOWS,
    LEGACY_GH_AW_QUIESCENCE_ERROR,
    legacy_gh_aw_effectful_writer_denial,
    verify_write_precondition,
)

ROOT = Path(__file__).resolve().parents[1]
SAME_REPO = ROOT / ".github/workflows/ai-sdlc-gh-aw-dispatch.yml"
CROSS_REPO = ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-dispatch.yml"
COMMAND = ROOT / ".github/workflows/ai-sdlc-gh-aw-command.yml"
PROFILE = ROOT / ".github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml"
INSTALLED_COMMAND = ROOT / "templates/github/ai-sdlc-command.yml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def validate_runtime_fence():
    require(
        LEGACY_GH_AW_EFFECTFUL_WORKFLOWS
        == {"AI-SDLC gh-aw Dispatch", "AI-SDLC gh-aw Cross-Repo Dispatch"},
        "legacy gh-aw quiescence workflow identity set drifted",
    )
    for workflow in sorted(LEGACY_GH_AW_EFFECTFUL_WORKFLOWS):
        result = verify_write_precondition(
            Path("/path/that-must-not-be-read"),
            "feature/example",
            "main",
            environment={"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW": workflow},
        )
        require(result["outcome"] == "INVALID", f"{workflow} did not fail closed before Git access")
        require(result["errors"] == [LEGACY_GH_AW_QUIESCENCE_ERROR], f"{workflow} returned wrong quiescence error")

    require(
        legacy_gh_aw_effectful_writer_denial(
            {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW": "Unrelated Workflow"}
        )
        is None,
        "quiescence fence expanded to unrelated workflows",
    )
    require(
        legacy_gh_aw_effectful_writer_denial(
            {"GITHUB_ACTIONS": "false", "GITHUB_WORKFLOW": "AI-SDLC gh-aw Dispatch"}
        )
        is None,
        "local/non-Actions execution was incorrectly classified as legacy production authority",
    )


def validate_same_repo_effect_order():
    body = text(SAME_REPO)
    require("name: AI-SDLC gh-aw Dispatch" in body, "same-repo legacy workflow identity drifted")
    require("if: ${{ !inputs.dry_run && needs.plan.outputs.outcome == 'PLANNED' }}" in body, "same-repo dry-run/effect boundary drifted")
    guard = body.find("python runtime/scripts/verify_git_write_precondition.py")
    push = body.find('git -C workspace push origin "HEAD:$TARGET_REF"')
    worker = body.find("subprocess.run(cmd, check=True)")
    require(guard >= 0 and push >= 0 and worker >= 0, "same-repo legacy effect inventory is incomplete")
    require(guard < push < worker, "same-repo legacy workflow can effect before the quiescence fence")
    require("python runtime/scripts/ingest_feature_event.py" in body, "same-repo lifecycle writer inventory changed without review")


def validate_cross_repo_effect_order():
    body = text(CROSS_REPO)
    require("name: AI-SDLC gh-aw Cross-Repo Dispatch" in body, "cross-repo legacy workflow identity drifted")
    require("if: ${{ !inputs.dry_run && steps.dedupe.outputs.should_dispatch == 'true' }}" in body, "cross-repo dry-run/effect boundary drifted")
    guard = body.find("python runtime/scripts/verify_git_write_precondition.py")
    push = body.find('git -C write-workspace push origin "HEAD:$TARGET_REF"')
    worker = body.find("subprocess.run(cmd,check=True)")
    require(guard >= 0 and push >= 0 and worker >= 0, "cross-repo legacy effect inventory is incomplete")
    require(guard < push < worker, "cross-repo legacy workflow can effect before the quiescence fence")
    require("python runtime/scripts/ingest_feature_event.py" in body, "cross-repo lifecycle writer inventory changed without review")


def validate_ingress_chains():
    command = text(COMMAND)
    require("gh workflow run ai-sdlc-gh-aw-dispatch.yml" in command, "control command no longer routes through audited same-repo gateway")
    require('--field dry_run=false' in command, "control command effectful intent changed without updating quiescence proof")

    profile = text(PROFILE)
    require("gh workflow run ai-sdlc-gh-aw-dispatch.yml" in profile, "profile same-repo route escaped audited gateway")
    require("gh workflow run ai-sdlc-gh-aw-cross-repo-dispatch.yml" in profile, "profile cross-repo route escaped audited gateway")
    require('--field dry_run="$DRY_RUN"' in profile, "profile no longer propagates dry-run state to audited gateways")

    installed = text(INSTALLED_COMMAND)
    require("'dispatch-gh-aw', 'ai-sdlc-gh-aw-dispatch-profile.yml'" in installed, "installed command escaped audited profile gateway")
    require('--field dry_run=false' in installed, "installed command effectful intent changed without updating quiescence proof")


def main():
    validate_runtime_fence()
    validate_same_repo_effect_order()
    validate_cross_repo_effect_order()
    validate_ingress_chains()
    print("v0.3 legacy gh-aw writer quiescence validation passed")


if __name__ == "__main__":
    main()
