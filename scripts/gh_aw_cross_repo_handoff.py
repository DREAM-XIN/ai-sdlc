#!/usr/bin/env python3
"""Prepare a trusted gh-aw handoff for a target Feature repository.

This bridge keeps Commander/Runtime Router authoritative while supporting two safe
handoff cases:

* fresh dispatch: Commander says DISPATCH for a TODO/READY work unit; the runtime
  gateway must persist the normal START reservation before starting the worker.
* resume working: Commander says WAIT because exactly one Feature stage is already
  WORKING; the bridge may adopt that in-progress work unit without replaying START.

The resume path is generic and idempotency-oriented. It does not change lifecycle
state and never converts a blocked/review/gate state into executable work.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestrator_state import compute_state, load_profile
from project_adapter import load_project_adapter
from runtime_router import load_yaml, select_runtime


def invalid(*errors: str) -> dict:
    return {"outcome": "INVALID", "errors": [error for error in errors if error]}


def prepare_handoff(
    manifest: dict,
    commander_plan: dict,
    policy: dict,
    *,
    repository: str,
    target_ref: str,
    default_branch: str,
    project: dict | None,
) -> dict:
    if not repository or "/" not in repository or repository.count("/") != 1:
        return invalid("target repository must be owner/repo")
    if not target_ref:
        return invalid("target_ref is required")
    if target_ref == default_branch:
        return invalid("gh-aw autonomous handoff must target a non-default branch")
    if ".." in target_ref.split("/"):
        return invalid("target_ref parent traversal is not allowed")

    if project:
        configured = (project.get("repository") or {}).get("full_name")
        if configured != repository:
            return invalid(
                f"target repository {repository} does not match Project Adapter repository.full_name {configured}"
            )
        configured_default = (project.get("repository") or {}).get("default_branch")
        if configured_default and configured_default != default_branch:
            return invalid(
                f"target default branch {default_branch} does not match Project Adapter repository.default_branch {configured_default}"
            )

    feature_id = manifest.get("feature", {}).get("id")
    if commander_plan.get("feature_id") != feature_id:
        return invalid("Commander Plan and Feature Manifest feature ids differ")
    revision = manifest.get("revision", 0)
    if commander_plan.get("summary", {}).get("revision") != revision:
        return invalid("Commander Plan revision does not match Feature Manifest")

    profile = load_profile(manifest["workflow"]["profile"])
    state = compute_state(manifest, profile)
    if state.get("outcome") != commander_plan.get("outcome"):
        return invalid(
            f"Commander outcome {commander_plan.get('outcome')} does not match recomputed state {state.get('outcome')}"
        )

    if state["outcome"] == "DISPATCH":
        return {
            "outcome": "READY",
            "errors": [],
            "mode": "fresh",
            "reserve_required": True,
            "commander_plan": commander_plan,
        }

    if state["outcome"] != "WAIT":
        return invalid(f"Commander outcome {state['outcome']} is not executable by gh-aw handoff")

    actions = state.get("actions", [])
    if len(actions) != 1:
        return invalid("resume-working requires exactly one in-progress work unit")
    action = actions[0]
    if action.get("kind", "stage") != "stage":
        return invalid("resume-working currently supports an in-progress Feature stage, not remediation")
    stage_id = action["stage"]
    stages = {item["id"]: item for item in manifest.get("workflow", {}).get("stages", [])}
    if stages.get(stage_id, {}).get("status") != "WORKING":
        return invalid("resume-working requires the selected stage to be WORKING")
    if manifest.get("workflow", {}).get("current_stage") != stage_id:
        return invalid("resume-working stage must equal workflow.current_stage")

    route = select_runtime(action, manifest["feature"]["risk"], policy)
    if route.get("outcome") != "ROUTED":
        return invalid(*(route.get("errors") or ["Runtime Router did not resolve the in-progress work unit"]))
    if route.get("runtime") != {"id": "gh-aw", "mode": "autonomous"}:
        return invalid("in-progress work unit is not routed to gh-aw/autonomous")

    synthetic = {
        "version": commander_plan.get("version", "0.1.0"),
        "feature_id": feature_id,
        "outcome": "DISPATCH",
        "errors": [],
        "summary": commander_plan["summary"],
        "dispatches": [
            {
                "action": action,
                "route_ids": route["route_ids"],
                "runtime": route["runtime"],
            }
        ],
    }
    return {
        "outcome": "READY",
        "errors": [],
        "mode": "resume-working",
        "reserve_required": False,
        "commander_plan": synthetic,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare trusted cross-repository gh-aw handoff")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("commander_plan", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = load_yaml(args.manifest)
    commander_plan = json.loads(args.commander_plan.read_text(encoding="utf-8"))
    policy = load_yaml(args.policy)
    project = None
    if args.project:
        project_result = load_project_adapter(args.project)
        if project_result["outcome"] == "INVALID":
            result = invalid(*project_result["errors"])
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            raise SystemExit(2)
        project = project_result["adapter"]

    result = prepare_handoff(
        manifest,
        commander_plan,
        policy,
        repository=args.repository,
        target_ref=args.target_ref,
        default_branch=args.default_branch,
        project=project,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
