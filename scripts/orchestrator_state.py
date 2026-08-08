#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import yaml

from validate_feature_manifest import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
COMPLETE_STAGE_STATES = {"DONE", "SKIPPED"}


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def remediation_action(task):
    return {
        "stage": task["stage"],
        "role": task["role"],
        "gate": None,
        "parallel": False,
        "kind": "remediation",
        "task_id": task["id"],
        "source_stage": task["source_stage"],
    }


def compute_state(manifest, profile):
    errors = validate_manifest(manifest)
    if errors:
        return {"outcome": "INVALID", "errors": errors, "actions": []}

    expected_profile = manifest["workflow"]["profile"]
    if profile.get("id") != expected_profile:
        return {
            "outcome": "INVALID",
            "errors": [f"profile mismatch: manifest={expected_profile} profile={profile.get('id')}"],
            "actions": [],
        }

    manifest_stages = {stage["id"]: stage for stage in manifest["workflow"]["stages"]}
    profile_stages = {stage["id"]: stage for stage in profile["stages"]}
    missing = sorted(set(profile_stages) - set(manifest_stages))
    extra = sorted(set(manifest_stages) - set(profile_stages))
    if missing or extra:
        problems = []
        if missing:
            problems.append("manifest missing profile stages: " + ", ".join(missing))
        if extra:
            problems.append("manifest has unknown profile stages: " + ", ".join(extra))
        return {"outcome": "INVALID", "errors": problems, "actions": []}

    workflow_status = manifest["workflow"]["status"]
    if workflow_status in {"DONE", "CANCELLED"}:
        return {"outcome": "COMPLETE", "errors": [], "actions": []}

    blocked = [stage["id"] for stage in manifest["workflow"]["stages"] if stage["status"] == "BLOCKED"]
    if workflow_status == "BLOCKED" or blocked:
        return {
            "outcome": "BLOCKED",
            "errors": [],
            "actions": [],
            "blocked_stages": blocked,
        }

    # Review remediation is first-class work but does not reopen a completed stage.
    # Serialize it ahead of further review dispatch so review/Gate authority remains independent.
    remediations = [task for task in manifest.get("tasks", []) if task.get("kind") == "remediation"]
    for task in remediations:
        state = task["status"]
        if state == "DONE":
            continue
        action = remediation_action(task)
        if state in {"TODO", "READY"}:
            return {"outcome": "DISPATCH", "errors": [], "actions": [action]}
        if state == "WORKING":
            return {"outcome": "WAIT", "errors": [], "actions": [action]}
        if state in {"BLOCKED", "FAILED"}:
            return {
                "outcome": "BLOCKED",
                "errors": [],
                "actions": [],
                "blocked_tasks": [task["id"]],
            }
        return {
            "outcome": "INVALID",
            "errors": [f"unsupported remediation task state: {task['id']}={state}"],
            "actions": [],
        }

    dispatch = []
    waiting = []
    dependency_blocks = []

    for stage in profile["stages"]:
        stage_id = stage["id"]
        state = manifest_stages[stage_id]["status"]
        if state in COMPLETE_STAGE_STATES:
            continue

        dependencies = stage.get("depends_on", [])
        incomplete_dependencies = [
            dep
            for dep in dependencies
            if manifest_stages[dep]["status"] not in COMPLETE_STAGE_STATES
        ]
        if incomplete_dependencies:
            dependency_blocks.append({"stage": stage_id, "waiting_on": incomplete_dependencies})
            continue

        action = {
            "stage": stage_id,
            "role": stage["role"],
            "gate": stage.get("gate"),
            "parallel": bool(stage.get("parallel", False)),
            "kind": "stage",
        }
        if state in {"TODO", "READY"}:
            dispatch.append(action)
        elif state in {"WORKING", "REVIEW"}:
            waiting.append(action)

    if dispatch:
        return {"outcome": "DISPATCH", "errors": [], "actions": dispatch}
    if waiting:
        return {"outcome": "WAIT", "errors": [], "actions": waiting}

    return {
        "outcome": "INVALID",
        "errors": ["active workflow has no dispatchable or in-progress stage"],
        "actions": [],
        "dependency_blocks": dependency_blocks,
    }


def load_profile(profile_id: str):
    path = PROFILES / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"workflow profile not found: {profile_id}")
    return load_yaml(path)


def main():
    parser = argparse.ArgumentParser(description="Compute deterministic AI-SDLC orchestrator state")
    parser.add_argument("manifest")
    parser.add_argument("--profile", help="Optional explicit profile YAML path")
    args = parser.parse_args()

    manifest = load_yaml(Path(args.manifest))
    if args.profile:
        profile = load_yaml(Path(args.profile))
    else:
        profile = load_profile(manifest["workflow"]["profile"])

    result = compute_state(manifest, profile)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
