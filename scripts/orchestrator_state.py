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
