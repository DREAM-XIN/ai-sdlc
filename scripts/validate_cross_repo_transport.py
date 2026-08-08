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
CROSS_REPO_LIFECYCLE = ROOT / ".github/workflows/ai-sdlc-cross-repo-lifecycle.yml"
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
    require("workspace_path:" in action and "AI_SDLC_WORKSPACE_PATH" in action, "control action cannot target an explicit checked-out workspace")
    require("workspace_path resolves outside GITHUB_WORKSPACE" in action, "control action explicit workspace is not containment checked")
    require("Path(os.environ['AI_SDLC_WORKSPACE']).resolve()" in action, "control action path validation is not rooted at the explicit target workspace")
    require('python "$runtime_root/scripts/verify_git_write_precondition.py"' in action, "shared Action no longer verifies remote target branch before writes")
    require('--repo-dir .' in action, "shared Action does not guard the active target checkout")
    require("python scripts/" not in action and "pip install -r requirements-dev.txt" not in action, "control action executes target control code/dependencies")

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

    cross_repo = CROSS_REPO_LIFECYCLE.read_text(encoding="utf-8")
    require("workflow_dispatch:" in cross_repo, "cross-repo lifecycle transport is not dispatchable from a target caller")
    require("target_repository:" in cross_repo and "target_ref:" in cross_repo and "operation:" in cross_repo, "cross-repo lifecycle request is not explicitly target-bound")
    require("AI_SDLC_RUNTIME_APP_CLIENT_ID" in cross_repo and "AI_SDLC_RUNTIME_APP_PRIVATE_KEY" in cross_repo, "cross-repo lifecycle transport does not use the trusted Runtime App")
    require("actions/create-github-app-token@" in cross_repo, "cross-repo lifecycle transport does not mint installation tokens")
    require("repositories: ${{ steps.target.outputs.repo_name }}" in cross_repo, "cross-repo lifecycle token is not restricted to the exact target repository")
    require("permission-contents: read" in cross_repo and "permission-contents: write" in cross_repo, "cross-repo lifecycle read/write capability separation is missing")
    require("if: ${{ inputs.persist && inputs.operation != 'plan' }}" in cross_repo, "write token is not limited to durable mutating operations")
    require("repository: ${{ inputs.target_repository }}" in cross_repo and "ref: ${{ inputs.target_ref }}" in cross_repo, "target checkout is not bound to exact repository/ref inputs")
    require("uses: ./runtime/.github/actions/control" in cross_repo and "workspace_path: workspace" in cross_repo, "cross-repo transport does not reuse the trusted lifecycle action")
    require("allow_default_branch: false" in cross_repo and "cross-repository lifecycle writes must target a non-default branch" in cross_repo, "cross-repo lifecycle default-branch guard drifted")
    require("concurrency:" in cross_repo and "inputs.target_repository" in cross_repo and "inputs.target_ref" in cross_repo, "cross-repo lifecycle transport lost branch serialization")
    require("personal_access_token" not in cross_repo.lower(), "cross-repo lifecycle transport prescribes a broad PAT")

    command = COMMAND.read_text(encoding="utf-8")
    require("issue_comment:" in command and "types: [created]" in command, "command bridge is not immutable Issue-comment driven")
    for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
        require(f"github.event.comment.author_association == '{assoc}'" in command, f"command bridge does not allow trusted {assoc}")
    require("permissions:\n  actions: write\n  contents: read\n  issues: write" in command, "command bridge permission envelope drifted")
    require("contents: write" not in command, "command bridge must not write target repository contents directly")
    for workflow in ("ai-sdlc-bootstrap.yml", "ai-sdlc-plan.yml", "ai-sdlc-persist.yml"):
        require(workflow in command, f"command bridge lost private installed caller binding: {workflow}")
    require("ai-sdlc-cross-repo-lifecycle.yml" in command, "command bridge lacks public-target lifecycle control transport")
    require("github.event.repository.private" in command and "public_repository" in command, "command bridge does not select transport from repository visibility")
    require("public_repository and operation in {'bootstrap', 'plan', 'persist'}" in command, "public lifecycle operations are not routed to the control repository")
    require("ai-sdlc-gh-aw-dispatch-profile.yml" in command, "command bridge lacks trusted autonomous profile gateway")
    require('gh workflow run "$WORKFLOW"' in command, "command bridge does not delegate through workflow_dispatch")
    require("--field target_repository=\"$GITHUB_REPOSITORY\"" in command, "target repository identity is user-selectable instead of caller-bound")
    require("--field operation=\"$OPERATION\"" in command, "public lifecycle dispatch does not bind the requested operation")
    require("--field persist=\"$persist\"" in command, "public lifecycle dispatch loses durable-vs-read-only intent")
    require("--field persist=true" in command, "trusted private bootstrap command does not request persistence")
    require("--field event_path=\"$EVENT_PATH\"" in command and "--field feature_issue=\"$FEATURE_ISSUE\"" in command, "trusted persist command lost Event binding")
    require("--field dry_run=false" in command and command.count("--field allow_default_branch=false") >= 2, "trusted private write commands lost durable/default-branch controls")
    require("AI-SDLC bootstrap command cannot persist to the default branch" in command, "bootstrap parser lost default-branch denial")
    require("AI-SDLC persist command cannot write to the default branch" in command, "persist parser lost default-branch denial")
    require("AI-SDLC gh-aw command cannot target the default branch" in command, "autonomous parser lost default-branch denial")
    require("parent traversal is not allowed" in command, "command parser lost traversal denial")
    require("gh run list" in command and "--event workflow_dispatch" in command, "command bridge does not resolve installed downstream run")
    require("AI-SDLC lifecycle" in command and "downstream-runs.json" in command, "command bridge cannot resolve control-repository lifecycle receipts")
    require("downstream_id" in command and "downstream_url" in command and "could not resolve the downstream run" in command, "durable command receipt evidence drifted")
    require("Downstream repository:" in command and "Target repository:" in command, "cross-repo receipt lacks repository identities")
    require("continue-on-error: true" in command, "command bridge cannot report dispatch failures before the job aborts")
    require("error_kind=missing-control-token" in command, "missing control credential is not classified")
    require("steps.dispatch.outcome == 'failure'" in command and "AI-SDLC command failed before downstream execution" in command, "dispatch failures are not durably reported to the Feature Issue")
    require("steps.dispatch.outcome == 'success'" in command, "downstream receipt resolution is not gated on successful dispatch")

    secret_names = set(re.findall(r"secrets\.([A-Z0-9_]+)", command))
    require(secret_names == {"AI_SDLC_CONTROL_DISPATCH_TOKEN"}, f"command bridge secret set drifted: {sorted(secret_names)}")
    require("personal_access_token" not in command.lower(), "command bridge prescribes a broad PAT")

    gh_aw_syntax = next(line for line in command.splitlines() if "gh_aw = re.fullmatch" in line)
    for forbidden in ("engine_profile", "worker_workflow", "provider", "model", "policy", "deepseek", "openai", "gemini", "claude"):
        require(forbidden not in gh_aw_syntax.lower(), f"autonomous command syntax leaks execution-plane selector: {forbidden}")

    for path in (PLAN, BOOTSTRAP, PERSIST):
        text = path.read_text(encoding="utf-8")
        require("REPLACE_WITH_AI_SDLC_FULL_SHA" in text and "ai-sdlc-install-placeholder" in text, f"{path.name}: immutable install placeholder missing")
        require("@main" not in text and "@v" not in text, f"{path.name}: mutable production Action reference remains")

    print("Cross-repository GitHub transport private installed-action, public control-initiated lifecycle, and command failure receipt checks passed")


if __name__ == "__main__":
    main()
