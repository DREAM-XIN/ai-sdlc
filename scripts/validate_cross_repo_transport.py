#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "control" / "action.yml"
PLAN = ROOT / "templates" / "github" / "ai-sdlc-plan.yml"
BOOTSTRAP = ROOT / "templates" / "github" / "ai-sdlc-bootstrap.yml"
PERSIST = ROOT / "templates" / "github" / "ai-sdlc-persist.yml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    action = ACTION.read_text(encoding="utf-8")
    require("${{ github.action_path }}" in action, "control action does not locate its own trusted runtime")
    require('pip install -r "$runtime_root/requirements-dev.txt"' in action, "control action installs dependencies from caller workspace")
    require('python "$runtime_root/scripts/commander.py"' in action, "control action does not execute trusted Commander")
    require('python "$runtime_root/scripts/ingest_feature_event.py"' in action, "control action does not execute trusted inbox implementation")
    require("plan)" in action and "bootstrap)" in action and "persist)" in action, "control action is missing an operation")
    require("validate_manifest_path true" in action, "read/write operations no longer validate existing Feature Manifest paths")
    require("validate_manifest_path false" in action, "bootstrap no longer validates target Feature Manifest path")
    require("validate_workspace_path" in action, "control action lost workspace containment checks")
    require("git check-ref-format --branch" in action, "write refs are not validated as branch names")
    require("Refusing direct persistence to default branch" in action, "default-branch write protection missing")
    require("python scripts/" not in action, "control action executes caller-repository control code")
    require("pip install -r requirements-dev.txt" not in action, "control action installs caller-repository dependencies")

    plan = PLAN.read_text(encoding="utf-8")
    require("permissions:\n  contents: read" in plan, "plan caller is not read-only")
    require("contents: write" not in plan, "plan caller unexpectedly has write permission")
    require("persist-credentials: false" in plan, "plan checkout persists credentials")
    require("DREAM-XIN/ai-sdlc/.github/actions/control@main" in plan, "plan caller does not use shared control action")

    for path in (BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("permissions:\n  contents: write" in text, f"{path.name}: write permission is not explicit")
        require("DREAM-XIN/ai-sdlc/.github/actions/control@main" in text, f"{path.name}: shared control action missing")
        require("default_branch: ${{ github.event.repository.default_branch }}" in text, f"{path.name}: caller default branch is not passed to write protection")
        require("allow_default_branch" in text, f"{path.name}: explicit default-branch override missing")
        require("secrets." not in text and "personal_access_token" not in text.lower(), f"{path.name}: template unexpectedly requires a PAT/secret")

    for path in (PLAN, BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("Pin this to an AI-SDLC release tag or full commit SHA" in text, f"{path.name}: production pinning guidance missing")

    print("Cross-repository GitHub transport static checks passed")


if __name__ == "__main__":
    main()
