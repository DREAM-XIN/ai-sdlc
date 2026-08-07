#!/usr/bin/env python3
"""Deterministic adapter between AI-SDLC Commander decisions and gh-aw workers."""

import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from apply_feature_event import validate_event
from manual_dispatch import build_task, schema_errors
from project_adapter import load_project_adapter
from runtime_router import load_yaml

ROOT = Path(__file__).resolve().parents[1]
DISPATCH_SCHEMA = ROOT / "runtimes" / "gh-aw" / "dispatch-plan.schema.json"
RESULT_SCHEMA = ROOT / "runtimes" / "gh-aw" / "worker-result.schema.json"
TASK_SCHEMA = ROOT / "spec" / "task.schema.json"
DEFAULT_POLICY = ROOT / "dispatch" / "default.yaml"


def unique(values):
    return list(dict.fromkeys(value for value in values if value))


def validate_schema(data, schema_path: Path, label: str):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = []
    for error in Draft202012Validator(schema).iter_errors(data):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{label}:{location}: {error.message}")
    return errors


def build_runtime_payload(task, project=None, project_ref=".ai-sdlc/project.yaml"):
    payload = {
        "contract": "ai-sdlc-task-v0.1",
        "task": task,
        "worker_rules": [
            "Do not edit the authoritative Feature Manifest.",
            "Return structured runtime result data; lifecycle persistence is handled by AI-SDLC.",
            "Do not self-approve any Gate.",
            "Stay inside the assigned task and repository scope.",
        ],
    }
    if project:
        payload["project"] = {
            "adapter_ref": project_ref,
            "rules": project["context"]["rules"],
            "read": project["context"]["read"],
            "required_commands": project["defaults"]["required_commands"],
            "ownership": project.get("ownership", []),
        }
    return payload


def build_dispatch_plan(
    manifest,
    commander_plan,
    policy,
    *,
    repository: str,
    target_ref: str,
    worker_workflow: str,
    project=None,
    project_ref=".ai-sdlc/project.yaml",
):
    if commander_plan.get("outcome") != "DISPATCH":
        return {
            "outcome": "NO_DISPATCH",
            "errors": [f"Commander outcome is {commander_plan.get('outcome')}, not DISPATCH"],
        }

    if commander_plan.get("feature_id") != manifest.get("feature", {}).get("id"):
        return {"outcome": "INVALID", "errors": ["Commander Plan and Feature Manifest feature ids differ"]}

    revision = manifest.get("revision", 0)
    plan_revision = commander_plan.get("summary", {}).get("revision")
    if plan_revision is not None and plan_revision != revision:
        return {
            "outcome": "INVALID",
            "errors": [f"Commander Plan revision {plan_revision} != Manifest revision {revision}"],
        }

    dispatches = []
    errors = []
    for dispatch in commander_plan.get("dispatches", []):
        runtime = dispatch.get("runtime")
        if runtime != {"id": "gh-aw", "mode": "autonomous"}:
            continue
        action = dispatch["action"]
        template = policy.get("task_templates", {}).get(action["stage"])
        if not template:
            errors.append(f"no task template for gh-aw stage: {action['stage']}")
            continue

        task = build_task(manifest, action, template, runtime)
        if project:
            task["inputs"] = unique(
                [project_ref]
                + project["context"]["rules"]
                + project["context"]["read"]
                + task.get("inputs", [])
            )
        problems = schema_errors(task, TASK_SCHEMA, task["id"])
        if problems:
            errors.extend(problems)
            continue

        payload = build_runtime_payload(task, project=project, project_ref=project_ref)
        dispatches.append(
            {
                "stage": action["stage"],
                "role": action["role"],
                "task_id": task["id"],
                "route_ids": dispatch["route_ids"],
                "workflow": worker_workflow,
                "ref": target_ref,
                "inputs": {
                    "feature_id": manifest["feature"]["id"],
                    "expected_revision": revision,
                    "stage": action["stage"],
                    "role": action["role"],
                    "task_payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                },
                "expected_result": {
                    "contract": "gh-aw-worker-result-v0.1",
                    "event_path_prefix": f"state/events/{manifest['feature']['id']}/",
                },
            }
        )

    if errors:
        return {"outcome": "INVALID", "errors": errors}
    if not dispatches:
        return {"outcome": "NO_DISPATCH", "errors": ["Commander Plan contains no gh-aw/autonomous routes"]}

    plan = {
        "version": "0.1.0",
        "runtime": {"id": "gh-aw", "mode": "autonomous"},
        "repository": repository,
        "target_ref": target_ref,
        "feature_id": manifest["feature"]["id"],
        "revision": revision,
        "worker_workflow": worker_workflow,
        "dispatches": dispatches,
    }
    plan_errors = validate_schema(plan, DISPATCH_SCHEMA, "gh-aw-dispatch-plan")
    if plan_errors:
        return {"outcome": "INVALID", "errors": plan_errors}
    return {"outcome": "PLANNED", "errors": [], "plan": plan}


def result_to_event(result):
    errors = validate_schema(result, RESULT_SCHEMA, "gh-aw-worker-result")
    if errors:
        return {"outcome": "INVALID", "errors": errors}

    changes = []
    for evidence in result.get("evidence", []):
        changes.append({"kind": "evidence", "record": dict(evidence)})

    # A worker completing its assignment never self-approves the stage as DONE.
    # It hands work to independent review. Blocked/failed execution blocks the stage.
    if result["status"] == "COMPLETED":
        changes.append({"kind": "stage", "id": result["stage"], "status": "REVIEW"})
    else:
        changes.append(
            {
                "kind": "stage",
                "id": result["stage"],
                "status": "BLOCKED",
                "reason": result["reason"],
            }
        )

    event_id = f"EVT-{result['id']}"
    feature_event = {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": result["feature_id"],
        "expected_revision": result["expected_revision"],
        "occurred_at": result["occurred_at"],
        "changes": changes,
    }
    event_errors = validate_event(feature_event)
    if event_errors:
        return {"outcome": "INVALID", "errors": event_errors}
    return {
        "outcome": "EVENT_READY",
        "errors": [],
        "event": feature_event,
        "event_path": f"state/events/{result['feature_id']}/{event_id}.yaml",
        "artifacts": result.get("artifacts", []),
    }


def load_optional_project(path: Path | None):
    if not path:
        return None, []
    result = load_project_adapter(path)
    if result["outcome"] == "INVALID":
        return None, result["errors"]
    return result["adapter"], []


def write_output(value, path: Path | None, *, yaml_output=False):
    if yaml_output:
        text = yaml.safe_dump(value, sort_keys=False)
    else:
        text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main():
    parser = argparse.ArgumentParser(description="AI-SDLC gh-aw runtime adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Convert Commander gh-aw routes into workflow-dispatch inputs")
    plan.add_argument("manifest", type=Path)
    plan.add_argument("commander_plan", type=Path)
    plan.add_argument("--repository", required=True)
    plan.add_argument("--target-ref", required=True)
    plan.add_argument("--worker-workflow", default="ai-sdlc-gh-aw-worker.lock.yml")
    plan.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    plan.add_argument("--project", type=Path)
    plan.add_argument("--project-ref", default=".ai-sdlc/project.yaml")
    plan.add_argument("--output", type=Path)

    result_parser = subparsers.add_parser("result-to-event", help="Convert a validated gh-aw worker result into a proposed Feature Event")
    result_parser.add_argument("result", type=Path)
    result_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "plan":
        project, project_errors = load_optional_project(args.project)
        if project_errors:
            result = {"outcome": "INVALID", "errors": project_errors}
        else:
            result = build_dispatch_plan(
                load_yaml(args.manifest),
                json.loads(args.commander_plan.read_text(encoding="utf-8")),
                load_yaml(args.policy),
                repository=args.repository,
                target_ref=args.target_ref,
                worker_workflow=args.worker_workflow,
                project=project,
                project_ref=args.project_ref,
            )
        write_output(result, args.output)
        if result["outcome"] == "INVALID":
            raise SystemExit(2)
        return

    result = result_to_event(load_yaml(args.result))
    if result["outcome"] == "EVENT_READY" and args.output:
        write_output(result["event"], args.output, yaml_output=True)
    else:
        write_output(result, None)
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
