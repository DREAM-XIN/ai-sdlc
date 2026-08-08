#!/usr/bin/env python3
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/control/action.yml"
RESOLVER_ACTION = ROOT / ".github/actions/resolve-event-push/action.yml"
PLAN = ROOT / "templates/github/ai-sdlc-plan.yml"
BOOTSTRAP = ROOT / "templates/github/ai-sdlc-bootstrap.yml"
PERSIST = ROOT / "templates/github/ai-sdlc-persist.yml"
COMMAND = ROOT / "templates/github/ai-sdlc-command.yml"
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
    require("validate_manifest_path true" in action and "validate_manifest_path false" in action, "Feature Manifest path validation drifted")
    require("validate_workspace_path" in action, "control action lost workspace containment checks")
    require('python "$runtime_root/scripts/verify_git_write_precondition.py"' in action, "shared Action no longer verifies remote target branch before writes")
    require('--repo-dir .' in action, "shared Action does not guard the caller checkout")
    require("python scripts/" not in action and "pip install -r requirements-dev.txt" not in action, "control action executes caller control code/dependencies")

    resolver = RESOLVER_ACTION.read_text(encoding="utf-8")
    require("${{ github.action_path }}" in resolver, "push resolver action does not locate trusted runtime")
    require('python "$runtime_root/scripts/resolve_feature_event_push.py"' in resolver, "push resolver action bypasses trusted implementation")
    require('--repo-dir "$GITHUB_WORKSPACE"' in resolver and "before_sha" in resolver and "after_sha" in resolver, "push resolver lost caller commit-range binding")

    plan = PLAN.read_text(encoding="utf-8")
    require("permissions:\n  contents: read" in plan and "contents: write" not in plan, "plan caller is not read-only")
    require("persist-credentials: false" in plan, "plan checkout persists credentials")
    require(CONTROL_PLACEHOLDER in plan, "plan caller lacks immutable control pin")

    for path in (BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("permissions:\n  contents: write" in text, f"{path.name}: write permission is not explicit")
        require(CONTROL_PLACEHOLDER in text, f"{path.name}: immutable control install placeholder missing")
        require("default_branch: ${{ github.event.repository.default_branch }}" in text, f"{path.name}: default branch protection input missing")
        require("allow_default_branch" in text, f"{path.name}: explicit default-branch override missing")
        require("secrets." not in text and "personal_access_token" not in text.lower(), f"{path.name}: local caller unexpectedly requires a PAT/secret")

    persist = PERSIST.read_text(encoding="utf-8")
    require(RESOLVER_PLACEHOLDER in persist, "persist caller does not pin trusted Event resolver")
    require("steps.request.outputs.mode != 'noop'" in persist and "steps.auto.outputs.event_count" in persist, "persist archive no-op/event-count contract drifted")

    command = COMMAND.read_text(encoding="utf-8")
    require("issue_comment:" in command and "types: [created]" in command, "command bridge is not immutable Issue-comment driven")
    for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
        require(f"github.event.comment.author_association == '{assoc}'" in command, f"command bridge does not allow trusted {assoc}")
    require("permissions:\n  actions: write\n  contents: read\n  issues: write" in command, "command bridge permission envelope drifted")
    require("contents: write" not in command, "command bridge must not write target repository contents directly")
    for workflow in ("ai-sdlc-bootstrap.yml", "ai-sdlc-plan.yml", "ai-sdlc-persist.yml"):
        require(workflow in command, f"command bridge lost installed caller binding: {workflow}")
    require("ai-sdlc-gh-aw-dispatch-profile.yml" in command, "command bridge lacks trusted autonomous profile gateway")
    require('gh workflow run "$WORKFLOW"' in command, "command bridge does not delegate through workflow_dispatch")
    require("--field persist=true" in command, "trusted bootstrap command does not request persistence")
    require("--field event_path=\"$EVENT_PATH\"" in command and "--field feature_issue=\"$FEATURE_ISSUE\"" in command, "trusted persist command lost Event binding")
    require("--field dry_run=false" in command and command.count("--field allow_default_branch=false") >= 2, "trusted write commands lost durable/default-branch controls")
    require("AI-SDLC bootstrap command cannot persist to the default branch" in command, "bootstrap parser lost default-branch denial")
    require("AI-SDLC persist command cannot write to the default branch" in command, "persist parser lost default-branch denial")
    require("AI-SDLC gh-aw command cannot target the default branch" in command, "autonomous parser lost default-branch denial")
    require("parent traversal is not allowed" in command, "command parser lost traversal denial")
    require("gh run list" in command and "--event workflow_dispatch" in command, "command bridge does not resolve downstream run")
    require("downstream_id" in command and "downstream_url" in command and "could not resolve the downstream run" in command, "durable command receipt evidence drifted")
    require("Downstream repository:" in command and "Target repository:" in command, "cross-repo receipt lacks repository identities")

    # Local bootstrap/plan/persist still use only caller GITHUB_TOKEN. The sole new secret name is a
    # control-repository Actions dispatch credential; repeated references to that same secret are fine.
    secret_names = set(re.findall(r"secrets\.([A-Z0-9_]+)", command))
    require(secret_names == {"AI_SDLC_CONTROL_DISPATCH_TOKEN"}, f"command bridge secret set drifted: {sorted(secret_names)}")
    require("personal_access_token" not in command.lower(), "command bridge prescribes a broad PAT")
    require('--field target_repository="$GITHUB_REPOSITORY"' in command, "target repository identity is user-selectable instead of caller-bound")

    gh_aw_syntax = next(line for line in command.splitlines() if "gh_aw = re.fullmatch" in line)
    for forbidden in ("engine_profile", "worker_workflow", "provider", "model", "policy", "deepseek", "openai", "gemini", "claude"):
        require(forbidden not in gh_aw_syntax.lower(), f"autonomous command syntax leaks execution-plane selector: {forbidden}")

    for path in (PLAN, BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("REPLACE_WITH_AI_SDLC_FULL_SHA" in text and "ai-sdlc-install-placeholder" in text, f"{path.name}: immutable install placeholder missing")
        require("@main" not in text and "@v" not in text, f"{path.name}: mutable production Action reference remains")

    print("Cross-repository GitHub transport immutable-pinning, durable receipts, and separated autonomous dispatch credential checks passed")


if __name__ == "__main__":
    main()
