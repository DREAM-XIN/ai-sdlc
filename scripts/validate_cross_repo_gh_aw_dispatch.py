#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path
import json

from bootstrap_feature import build_manifest, load_profile
from commander import build_commander_plan
from gh_aw_adapter import build_dispatch_plan
from gh_aw_cross_repo_handoff import prepare_handoff
from project_adapter import load_project_adapter
from runtime_router import load_yaml
from validate_gh_aw_dispatch_idempotency import main as validate_dispatch_idempotency

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise AssertionError(message)


def make_implementation_working():
    profile = load_profile("standard-feature")
    created = build_manifest({
        "version": "0.1.0",
        "feature": {"id": "F-XREPO", "title": "Cross repo", "risk": "medium", "issue": "#12"},
        "profile": "standard-feature",
        "created_at": "2026-08-08T00:00:00Z",
    }, profile)
    manifest = created["manifest"]
    for stage in manifest["workflow"]["stages"]:
        if stage["id"] in {"requirement", "requirement-review", "design", "design-review", "plan"}:
            stage["status"] = "DONE"
        elif stage["id"] == "implementation":
            stage["status"] = "WORKING"
        else:
            stage["status"] = "TODO"
    for gate in manifest.get("gates", []):
        if gate["id"] in {"requirement-gate", "design-gate"}:
            gate["status"] = "PASS"
            gate["evidence"] = [f"EVID-{gate['id']}"]
        else:
            gate["status"] = "PENDING"
            gate.pop("evidence", None)
    manifest["evidence"] = [
        {"id": "EVID-requirement-gate", "type": "review", "status": "pass", "uri": "docs/requirement-review.md"},
        {"id": "EVID-design-gate", "type": "review", "status": "pass", "uri": "docs/design-review.md"},
    ]
    manifest["workflow"]["current_stage"] = "implementation"
    manifest["revision"] = 10
    return manifest, profile


def main():
    manifest, profile = make_implementation_working()
    policy = load_yaml(ROOT / "dispatch/gh-aw-developer.yaml")
    project_result = load_project_adapter(ROOT / "examples/project-adapters/generic.yaml")
    require(project_result["outcome"] == "VALID", "generic Project Adapter fixture must be valid")
    project = deepcopy(project_result["adapter"])
    project["repository"]["full_name"] = "DREAM-XIN/example-target"
    project["repository"]["default_branch"] = "main"

    commander = build_commander_plan(manifest, profile, policy, repository="DREAM-XIN/example-target", project_adapter=project)
    require(commander["outcome"] == "WAIT", f"WORKING stage must remain Commander WAIT, got {commander['outcome']}: {commander.get('errors')}")

    handoff = prepare_handoff(manifest, commander, policy, repository="DREAM-XIN/example-target", target_ref="feature/F-XREPO", default_branch="main", project=project)
    require(handoff["outcome"] == "READY", f"resume handoff failed: {handoff}")
    require(handoff["mode"] == "resume-working" and handoff["reserve_required"] is False, "WORKING handoff must not replay START")
    routed = handoff["commander_plan"]
    require(routed["dispatches"][0]["runtime"] == {"id": "gh-aw", "mode": "autonomous"}, "Runtime Router decision was not preserved")

    planned = build_dispatch_plan(manifest, routed, policy, repository="DREAM-XIN/example-target", target_ref="feature/F-XREPO", worker_workflow="ai-sdlc-gh-aw-worker.lock.yml", project=project, reserve_required=False)
    require(planned["outcome"] == "PLANNED", f"resume adapter failed: {planned}")
    dispatch = planned["plan"]["dispatches"][0]
    require(dispatch["inputs"]["expected_revision"] == 10, "resume worker result must target current revision")
    require(dispatch["inputs"]["target_repository"] == "DREAM-XIN/example-target", "target repository identity missing")
    payload = json.loads(dispatch["inputs"]["task_payload"])
    require(payload["feature_context"]["repository"] == "DREAM-XIN/example-target", "feature_context repository binding missing")
    require("Do not self-approve any Gate." in payload["worker_rules"], "Gate prohibition missing")

    rejected = prepare_handoff(manifest, commander, policy, repository="DREAM-XIN/example-target", target_ref="main", default_branch="main", project=project)
    require(rejected["outcome"] == "INVALID", "default target branch must be rejected")
    rejected = prepare_handoff(manifest, commander, policy, repository="DREAM-XIN/example-target", target_ref="feature/../main", default_branch="main", project=project)
    require(rejected["outcome"] == "INVALID", "target ref traversal must be rejected")
    mismatch = deepcopy(project)
    mismatch["repository"]["full_name"] = "DREAM-XIN/other-target"
    rejected = prepare_handoff(manifest, commander, policy, repository="DREAM-XIN/example-target", target_ref="feature/F-XREPO", default_branch="main", project=mismatch)
    require(rejected["outcome"] == "INVALID", "Project Adapter/target repository mismatch must fail closed")

    command = (ROOT / "templates/github/ai-sdlc-command.yml").read_text(encoding="utf-8")
    gateway = (ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-dispatch.yml").read_text(encoding="utf-8")
    worker = (ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md").read_text(encoding="utf-8")
    result = (ROOT / ".github/workflows/ai-sdlc-gh-aw-result.yml").read_text(encoding="utf-8")

    require('/ai-sdlc dispatch-gh-aw target_ref=' in command, "target command surface missing autonomous dispatch")
    gh_aw_syntax = next(line for line in command.splitlines() if "gh_aw = re.fullmatch" in line)
    for forbidden in ("provider=", "model=", "engine_profile=", "worker_workflow=", "policy="):
        require(forbidden not in gh_aw_syntax, f"provider/worker selector leaked into target command: {forbidden}")
    require('--field target_repository="$GITHUB_REPOSITORY"' in command, "caller repository identity must be implicit, not user-selected")
    require("Downstream repository:" in command and "Target repository:" in command, "durable receipt must record downstream and target repositories")
    require("AI_SDLC_CONTROL_DISPATCH_TOKEN" in command, "cross-repo transport credential is not explicit")

    require("runtime/dispatch/gh-aw-developer.yaml" in gateway, "cross-repo runtime policy must come from trusted control plane")
    require("permission-contents: read" in gateway and "permission-contents: write" in gateway, "read/write target token phases are missing")
    require("permission-pull-requests: write" not in gateway and "permission-actions: write" not in gateway, "gateway target token is over-permissioned")
    require("actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1" in gateway, "GitHub App token action must be SHA-pinned")
    require("target Feature branch advanced after trusted planning" in gateway, "plan/execute target SHA binding is missing")
    require("target repository identity drift before worker dispatch" in gateway, "final repository binding does not fail closed")

    require("target-repo: ${{ inputs.target_repository }}" in worker, "Safe Output target repository is not fixed")
    require("base-branch: ${{ inputs.target_ref }}" in worker, "Safe Output Feature base is not fixed")
    require("protected-files: blocked" in worker, "Safe Output protected-files boundary missing")
    require("Do not edit the Feature Manifest directly" in worker, "worker Manifest prohibition missing")
    require("Do not pass or waive any Gate" in worker, "worker Gate prohibition missing")
    require("Do not merge or release" in worker, "worker merge/release prohibition missing")
    require("state/features/**" in worker and "state/events/**" in worker, "worker state paths are not explicitly forbidden")
    require("contents: write" not in worker.split("---", 2)[1], "agent workflow must not receive contents:write permission")
    require("confirm its `revision` equals `${{ inputs.expected_revision }}`" in worker, "worker does not reject stale Feature revision")

    require("target_repository:" in result, "trusted result collector lacks target repository identity")
    require("permission-contents: write" in result, "result collector target token must be explicitly contents:write")
    require("permission-pull-requests: write" not in result and "permission-actions: write" not in result, "result collector target token is over-permissioned")

    validate_dispatch_idempotency()
    print("cross-repo gh-aw handoff, resume-WORKING, repository binding, command neutrality, Safe Output, Manifest/Gate, receipt, least-privilege, and dispatch idempotency checks passed")


if __name__ == "__main__":
    main()