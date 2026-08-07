#!/usr/bin/env python3
"""Reference AI-SDLC Commander composed from existing deterministic primitives."""

import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from bootstrap_feature import build_manifest, load_profile as load_bootstrap_profile
from ingest_feature_event import ingest
from manual_dispatch import build_task, schema_errors
from orchestrator_state import compute_state, load_profile
from render_task_package import build_package, render_prompt
from runtime_router import load_yaml, select_runtime

ROOT = Path(__file__).resolve().parents[1]
COMMANDER_SCHEMA = ROOT / "spec" / "commander-plan.schema.json"
TASK_SCHEMA = ROOT / "spec" / "task.schema.json"
PACKAGE_SCHEMA = ROOT / "spec" / "task-package.schema.json"
DEFAULT_POLICY = ROOT / "dispatch" / "default.yaml"


def load_json_schema(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def commander_plan_errors(plan):
    errors = []
    schema = load_json_schema(COMMANDER_SCHEMA)
    for error in Draft202012Validator(schema).iter_errors(plan):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"commander-plan:{location}: {error.message}")

    dispatches = plan.get("dispatches", [])
    outcome = plan.get("outcome")
    if outcome == "DISPATCH" and not dispatches:
        errors.append("commander-plan: DISPATCH requires at least one dispatch")
    if outcome != "DISPATCH" and dispatches:
        errors.append(f"commander-plan: {outcome} must not contain dispatches")

    for dispatch in dispatches:
        runtime = dispatch.get("runtime", {})
        is_chatgpt_manual = runtime == {"id": "chatgpt-web", "mode": "manual"}
        payload_fields = [field for field in ("task", "package", "prompt") if field in dispatch]
        if is_chatgpt_manual:
            missing = [field for field in ("task", "package", "prompt") if field not in dispatch]
            if missing:
                errors.append(
                    "commander-plan: chatgpt-web/manual dispatch missing " + ", ".join(missing)
                )
        elif payload_fields:
            errors.append(
                "commander-plan: non-chatgpt runtime must remain a routing decision; unexpected payload fields: "
                + ", ".join(payload_fields)
            )
    return errors


def summary_from_manifest(manifest):
    workflow = manifest.get("workflow") if isinstance(manifest, dict) else None
    feature = manifest.get("feature") if isinstance(manifest, dict) else None
    return {
        "feature_id": feature.get("id") if isinstance(feature, dict) else None,
        "summary": {
            "workflow_status": workflow.get("status") if isinstance(workflow, dict) else None,
            "current_stage": workflow.get("current_stage") if isinstance(workflow, dict) else None,
        },
    }


def commander_bootstrap(bootstrap, profile):
    """Create the initial Feature Manifest using the existing bootstrap engine."""
    return build_manifest(bootstrap, profile)


def build_commander_plan(
    manifest,
    profile,
    policy,
    repository,
    manifest_ref="Feature Manifest",
    project_rules=None,
):
    """Compute state, route all runnable actions, and enrich only supported manual runtimes."""
    identity = summary_from_manifest(manifest)
    state = compute_state(manifest, profile)
    plan = {
        "version": "0.1.0",
        "feature_id": identity["feature_id"],
        "outcome": state["outcome"],
        "errors": list(state.get("errors", [])),
        "summary": identity["summary"],
        "dispatches": [],
    }

    if state["outcome"] != "DISPATCH":
        validation = commander_plan_errors(plan)
        if validation:
            plan["outcome"] = "INVALID"
            plan["errors"] = list(dict.fromkeys(plan["errors"] + validation))
            plan["dispatches"] = []
        return plan

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
        dispatch = {
            "action": action,
            "route_ids": route["route_ids"],
            "runtime": runtime,
        }

        # The reference implementation knows how to prepare ChatGPT Web/manual
        # work, but all other runtimes remain pure routing decisions for their adapters.
        if runtime == {"id": "chatgpt-web", "mode": "manual"}:
            template = policy.get("task_templates", {}).get(action["stage"])
            if not template:
                errors.append(f"no task template for chatgpt-web stage: {action['stage']}")
                continue

            task = build_task(manifest, action, template, runtime)
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

            dispatch.update(
                {
                    "task": task,
                    "package": package,
                    "prompt": render_prompt(package),
                }
            )

        dispatches.append(dispatch)

    if errors:
        plan["outcome"] = "INVALID"
        plan["errors"] = list(dict.fromkeys(errors))
        plan["dispatches"] = []
        return plan

    plan["dispatches"] = dispatches
    validation = commander_plan_errors(plan)
    if validation:
        plan["outcome"] = "INVALID"
        plan["errors"] = validation
        plan["dispatches"] = []
    return plan


def commander_ingest(
    manifest,
    event,
    *,
    event_path,
    repository,
    manifest_path,
    target_ref,
    issue=None,
):
    """Delegate event ingestion to the existing inbox + persistence pipeline."""
    return ingest(
        manifest,
        event,
        event_path=event_path,
        repository=repository,
        manifest_path=manifest_path,
        target_ref=target_ref,
        issue=issue,
    )


def invalid_cli_plan(manifest, message):
    identity = summary_from_manifest(manifest)
    return {
        "version": "0.1.0",
        "feature_id": identity["feature_id"],
        "outcome": "INVALID",
        "errors": [message],
        "summary": identity["summary"],
        "dispatches": [],
    }


def bootstrap_command(args):
    bootstrap = load_yaml(args.bootstrap)
    try:
        profile = load_yaml(args.profile) if args.profile else load_bootstrap_profile(bootstrap["profile"])
    except (KeyError, FileNotFoundError, ValueError) as exc:
        return {"outcome": "INVALID", "errors": [str(exc)]}, 2
    result = commander_bootstrap(bootstrap, profile)
    if result["outcome"] == "INVALID":
        return result, 2
    text = (
        json.dumps(result["manifest"], indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else yaml.safe_dump(result["manifest"], sort_keys=False)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        return {"outcome": "BOOTSTRAPPED", "output": str(args.output)}, 0
    return text, 0


def plan_command(args):
    manifest = load_yaml(args.manifest)
    try:
        if args.profile:
            profile = load_yaml(args.profile)
        else:
            profile_id = manifest.get("workflow", {}).get("profile")
            if not profile_id:
                return invalid_cli_plan(manifest, "manifest does not declare a workflow profile"), 2
            profile = load_profile(profile_id)
        policy = load_yaml(args.policy)
    except (FileNotFoundError, ValueError) as exc:
        return invalid_cli_plan(manifest, str(exc)), 2

    plan = build_commander_plan(
        manifest,
        profile,
        policy,
        repository=args.repository,
        manifest_ref=args.manifest_ref,
        project_rules=args.rule or ["AGENTS.md"],
    )
    if args.format == "prompts" and plan["outcome"] == "DISPATCH":
        prompts = [dispatch["prompt"] for dispatch in plan["dispatches"] if "prompt" in dispatch]
        if prompts:
            return "\n\n---\n\n".join(prompt.rstrip() for prompt in prompts) + "\n", 0
    return plan, 2 if plan["outcome"] == "INVALID" else 0


def ingest_command(args):
    result = commander_ingest(
        load_yaml(args.manifest),
        load_yaml(args.event),
        event_path=args.event_path,
        repository=args.repository,
        manifest_path=args.manifest_path,
        target_ref=args.target_ref,
        issue=args.issue,
    )
    return result, 2 if result["outcome"] == "INVALID" else 0


def print_result(result):
    if isinstance(result, str):
        print(result, end="")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="Reference AI-SDLC Commander")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="Create an initial Feature Manifest")
    bootstrap.add_argument("bootstrap", type=Path)
    bootstrap.add_argument("--profile", type=Path)
    bootstrap.add_argument("--format", choices=["yaml", "json"], default="yaml")
    bootstrap.add_argument("--output", type=Path)
    bootstrap.set_defaults(handler=bootstrap_command)

    plan = subparsers.add_parser("plan", help="Compute next state and runtime dispatch decisions")
    plan.add_argument("manifest", type=Path)
    plan.add_argument("--repository", required=True)
    plan.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    plan.add_argument("--profile", type=Path)
    plan.add_argument("--manifest-ref", default="Feature Manifest")
    plan.add_argument("--rule", action="append", default=[])
    plan.add_argument("--format", choices=["json", "prompts"], default="json")
    plan.set_defaults(handler=plan_command)

    ingest_parser = subparsers.add_parser("ingest", help="Validate an inbox event and build a persistence plan")
    ingest_parser.add_argument("manifest", type=Path)
    ingest_parser.add_argument("event", type=Path)
    ingest_parser.add_argument("--event-path", required=True)
    ingest_parser.add_argument("--repository", required=True)
    ingest_parser.add_argument("--manifest-path", required=True)
    ingest_parser.add_argument("--target-ref", required=True)
    ingest_parser.add_argument("--issue", type=int)
    ingest_parser.set_defaults(handler=ingest_command)

    args = parser.parse_args()
    result, code = args.handler(args)
    print_result(result)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
