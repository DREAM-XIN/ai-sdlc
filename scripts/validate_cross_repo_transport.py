#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "control" / "action.yml"
PLAN = ROOT / "templates" / "github" / "ai-sdlc-plan.yml"
BOOTSTRAP = ROOT / "templates" / "github" / "ai-sdlc-bootstrap.yml"
PERSIST = ROOT / "templates" / "github" / "ai-sdlc-persist.yml"
CONTROL_PLACEHOLDER = "DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA"


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
    require(
        'python "$runtime_root/scripts/verify_git_write_precondition.py"' in action,
        "shared Action no longer verifies remote target branch before writes",
    )
    require('--repo-dir .' in action, "shared Action does not guard the caller checkout")
    require("python scripts/" not in action, "control action executes caller-repository control code")
    require("pip install -r requirements-dev.txt" not in action, "control action installs caller-repository dependencies")

    plan = PLAN.read_text(encoding="utf-8")
    require("permissions:\n  contents: read" in plan, "plan caller is not read-only")
    require("contents: write" not in plan, "plan caller unexpectedly has write permission")
    require("persist-credentials: false" in plan, "plan checkout persists credentials")
    require(CONTROL_PLACEHOLDER in plan, "plan caller does not require explicit immutable AI-SDLC installation pin")

    for path in (BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("permissions:\n  contents: write" in text, f"{path.name}: write permission is not explicit")
        require(CONTROL_PLACEHOLDER in text, f"{path.name}: immutable AI-SDLC install placeholder missing")
        require("default_branch: ${{ github.event.repository.default_branch }}" in text, f"{path.name}: caller default branch is not passed to write protection")
        require("allow_default_branch" in text, f"{path.name}: explicit default-branch override missing")
        require("secrets." not in text and "personal_access_token" not in text.lower(), f"{path.name}: template unexpectedly requires a PAT/secret")

    for path in (PLAN, BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("REPLACE_WITH_AI_SDLC_FULL_SHA" in text, f"{path.name}: explicit install-time SHA placeholder missing")
        require("ai-sdlc-install-placeholder" in text, f"{path.name}: install placeholder marker missing")
        require("@main" not in text and "@v" not in text, f"{path.name}: mutable production reference remains")

    print("Cross-repository GitHub transport immutable-pinning checks passed")


if __name__ == "__main__":
    main()
