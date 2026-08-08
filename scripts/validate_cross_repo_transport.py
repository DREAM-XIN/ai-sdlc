#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "control" / "action.yml"
RESOLVER_ACTION = ROOT / ".github" / "actions" / "resolve-event-push" / "action.yml"
PLAN = ROOT / "templates" / "github" / "ai-sdlc-plan.yml"
BOOTSTRAP = ROOT / "templates" / "github" / "ai-sdlc-bootstrap.yml"
PERSIST = ROOT / "templates" / "github" / "ai-sdlc-persist.yml"
COMMAND = ROOT / "templates" / "github" / "ai-sdlc-command.yml"
CONTROL_PLACEHOLDER = "DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA"
RESOLVER_PLACEHOLDER = "DREAM-XIN/ai-sdlc/.github/actions/resolve-event-push@REPLACE_WITH_AI_SDLC_FULL_SHA"


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

    resolver = RESOLVER_ACTION.read_text(encoding="utf-8")
    require("${{ github.action_path }}" in resolver, "push resolver action does not locate its own trusted runtime")
    require('pip install -r "$runtime_root/requirements-dev.txt"' in resolver, "push resolver action does not install trusted runtime dependencies")
    require(
        'python "$runtime_root/scripts/resolve_feature_event_push.py"' in resolver,
        "push resolver action does not execute the trusted archive-aware resolver",
    )
    require('--repo-dir "$GITHUB_WORKSPACE"' in resolver, "push resolver action does not inspect the caller checkout")
    require("before_sha" in resolver and "after_sha" in resolver, "push resolver action does not bind the pushed commit range")

    plan = PLAN.read_text(encoding="utf-8")
    require("permissions:\n  contents: read" in plan, "plan caller is not read-only")
    require("contents: write" not in plan, "plan caller unexpectedly has write permission")
    require("persist-credentials: false" in plan, "plan checkout persists credentials")
    require(CONTROL_PLACEHOLDER in plan, "plan caller does not require explicit immutable AI-SDLC installation pin")

    for path in (BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("permissions:\n  contents: write" in text, f"{path.name}: write permission is not explicit")
        require(CONTROL_PLACEHOLDER in text, f"{path.name}: immutable AI-SDLC control install placeholder missing")
        require("default_branch: ${{ github.event.repository.default_branch }}" in text, f"{path.name}: caller default branch is not passed to write protection")
        require("allow_default_branch" in text, f"{path.name}: explicit default-branch override missing")
        require("secrets." not in text and "personal_access_token" not in text.lower(), f"{path.name}: template unexpectedly requires a PAT/secret")

    persist = PERSIST.read_text(encoding="utf-8")
    require(RESOLVER_PLACEHOLDER in persist, "persist caller does not pin the trusted Feature Event push resolver")
    require("steps.request.outputs.mode != 'noop'" in persist, "persist caller does not skip writes for proven archive no-ops")
    require("steps.auto.outputs.event_count" in persist, "persist caller does not propagate archive Event count")

    command = COMMAND.read_text(encoding="utf-8")
    require("issue_comment:" in command, "command bridge is not driven by durable Issue comments")
    require("types: [created]" in command, "command bridge accepts mutable comment updates")
    require("github.event.comment.author_association == 'OWNER'" in command, "command bridge does not require trusted author association")
    require("github.event.comment.author_association == 'MEMBER'" in command, "command bridge does not allow repository members")
    require("github.event.comment.author_association == 'COLLABORATOR'" in command, "command bridge does not allow collaborators")
    require("permissions:\n  actions: write\n  contents: read\n  issues: write" in command, "command bridge permission envelope drifted")
    require("contents: write" not in command, "command bridge must not write repository contents directly")
    require("workflow = 'ai-sdlc-bootstrap.yml'" in command, "command bridge does not bind bootstrap to the installed caller")
    require("workflow = 'ai-sdlc-plan.yml'" in command, "command bridge does not bind plan to the installed caller")
    require('gh workflow run "$WORKFLOW"' in command, "command bridge does not delegate through workflow_dispatch")
    require("--field persist=true" in command, "trusted bootstrap command does not request durable persistence")
    require("--field allow_default_branch=false" in command, "trusted bootstrap command lost default-branch denial")
    require("AI-SDLC bootstrap command cannot persist to the default branch" in command, "command parser lost default-branch denial")
    require("state/bootstrap/" in command and "state/features/" in command, "command parser does not constrain durable state paths")
    require("parent traversal is not allowed" in command, "command parser lost parent-traversal denial")
    require("gh run list" in command and "--event workflow_dispatch" in command, "command bridge does not resolve the downstream run")
    require("downstream_id" in command and "downstream_url" in command, "command receipt does not identify downstream evidence")
    require("could not resolve the downstream run" in command, "command bridge does not fail closed when downstream evidence is missing")
    require("secrets." not in command and "personal_access_token" not in command.lower(), "command bridge unexpectedly requires a PAT/secret")
    for forbidden in ("engine_profile", "worker_workflow", "provider", "model", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        require(forbidden not in command, f"command bridge leaks execution-plane selector or credential: {forbidden}")

    for path in (PLAN, BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("REPLACE_WITH_AI_SDLC_FULL_SHA" in text, f"{path.name}: explicit install-time SHA placeholder missing")
        require("ai-sdlc-install-placeholder" in text, f"{path.name}: install placeholder marker missing")
        require("@main" not in text and "@v" not in text, f"{path.name}: mutable production reference remains")

    print("Cross-repository GitHub transport immutable-pinning, archive-aware resolver, and trusted comment-bridge checks passed")


if __name__ == "__main__":
    main()
