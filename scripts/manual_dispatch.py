#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from orchestrator_state import compute_state, load_profile
from render_task_package import build_package, render_prompt
from runtime_router import load_yaml, select_runtime

ROOT = Path(__file__).resolve().parents[1]
TASK_SCHEMA = ROOT / "spec" / "task.schema.json"
PACKAGE_SCHEMA = ROOT / "spec" / "task-package.schema.json"


def slug(value):
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()


def schema_errors(data, path, label):
    with path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(data):
        location = ".".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"{label}:{location}: {error.message}")
    return errors


def remediation_record(manifest, action):
    task_id = action.get("task_id")
    if action.get("kind") != "remediation" or not task_id:
        return None
    matches = [task for task in manifest.get("tasks", []) if task.get("id") == task_id]
    if len(matches) != 1 or matches[0].get("kind") != "remediation":
        raise ValueError(f"remediation action references invalid task: {task_id}")
    return matches[0]


def build_task(manifest, action, template, runtime):
    feature_id = manifest["feature"]["id"]
    stage = action["stage"]
    remediation = remediation_record(manifest, action)
    if remediation:
        inputs = list(template.get("read", []))
        if remediation.get("target_pr"):
            inputs.append(remediation["target_pr"])
        if remediation.get("issue"):
            inputs.append(remediation["issue"])
        return {
            "id": remediation["id"],
            "kind": "remediation",
            "feature_id": feature_id,
            "role": action["role"],
            "goal": "Address independent review feedback without changing lifecycle or Gate authority: "
            + remediation["feedback"],
            "inputs": list(dict.fromkeys(inputs)),
            "allowed_scope": template.get("allowed_scope", []),
            "forbidden_scope": template.get("forbidden_scope", []),
            "expected_outputs": template["expected_outputs"],
            "definition_of_done": [
                "The review feedback is addressed in a bounded corrective change.",
                "Required verification passes for the corrective change.",
                "The remediation task completes without approving review, Gate, merge, or release state.",
            ],
            "runtime": runtime["id"],
        }
    return {
        "id": f"{slug(feature_id)}-{slug(stage)}",
        "feature_id": feature_id,
        "role": action["role"],
        "goal": template["goal"],
        "inputs": template.get("read", []),
        "allowed_scope": template.get("allowed_scope", []),
        "forbidden_scope": template.get("forbidden_scope", []),
        "expected_outputs": template["expected_outputs"],
        "definition_of_done": template["definition_of_done"],
        "runtime": runtime["id"],
    }


def build_dispatches(manifest, profile, policy, repository, manifest_ref="Feature Manifest", project_rules=None):
    state = compute_state(manifest, profile)
    if state["outcome"] != "DISPATCH":
        return {"outcome": state["outcome"], "errors": state.get("errors", []), "dispatches": []}

    risk = manifest["feature"]["risk"]
    project_rules = project_rules or ["AGENTS.md"]
    dispatches = []
    errors = []

    for action in state["actions"]:
        route = select_runtime(action, risk, policy)
        if route["outcome"] == "INVALID":
            errors.extend(route["errors"])
            continue
        runtime = route["runtime"]
        if runtime != {"id": "chatgpt-web", "mode": "manual"}:
            errors.append(
                f"manual dispatcher cannot execute runtime {runtime['id']}/{runtime['mode']} for stage {action['stage']}"
            )
            continue

        template = policy.get("task_templates", {}).get(action["stage"])
        if not template:
            errors.append(f"no task template for dispatchable stage: {action['stage']}")
            continue

        try:
            task = build_task(manifest, action, template, runtime)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        task_problems = schema_errors(task, TASK_SCHEMA, task["id"])
        if task_problems:
            errors.extend(task_problems)
            continue

        read_refs = [manifest_ref] + template.get("read", [])
        package = build_package(task, repository, read_refs, project_rules)
        package["transport"] = {"runtime": runtime["id"], "mode": runtime["mode"]}
        package_problems = schema_errors(package, PACKAGE_SCHEMA, f"package:{task['id']}")
        if package_problems:
            errors.extend(package_problems)
            continue

        dispatches.append(
            {
                "action": action,
                "route_ids": route["route_ids"],
                "runtime": runtime,
                "task": task,
                "package": package,
                "prompt": render_prompt(package),
            }
        )

    if errors:
        return {"outcome": "INVALID", "errors": errors, "dispatches": []}
    return {"outcome": "DISPATCH", "errors": [], "dispatches": dispatches}


def main():
    parser = argparse.ArgumentParser(description="Build ChatGPT Web manual dispatch bundles")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--policy", type=Path, default=ROOT / "dispatch" / "default.yaml")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--manifest-ref", default="Feature Manifest")
    parser.add_argument("--format", choices=["json", "prompts"], default="json")
    args = parser.parse_args()

    manifest = load_yaml(args.manifest)
    profile = load_yaml(args.profile) if args.profile else load_profile(manifest["workflow"]["profile"])
    result = build_dispatches(
        manifest,
        profile,
        load_yaml(args.policy),
        args.repository,
        manifest_ref=args.manifest_ref,
    )

    if args.format == "prompts" and result["outcome"] == "DISPATCH":
        for index, dispatch in enumerate(result["dispatches"]):
            if index:
                print("\n---\n")
            print(dispatch["prompt"], end="")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))

    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
