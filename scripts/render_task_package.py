#!/usr/bin/env python3
"""Render a canonical AI-SDLC Task into a portable ChatGPT Web task package."""

import argparse
from pathlib import Path

import yaml


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_package(task: dict, repository: str, read_refs: list[str], project_rules: list[str]):
    role = task["role"]
    return {
        "version": "0.1.0",
        "task": {
            "id": task["id"],
            **({"feature_id": task["feature_id"]} if task.get("feature_id") else {}),
            "role": role,
            "goal": task["goal"],
            "allowed_scope": task.get("allowed_scope", []),
            "forbidden_scope": task.get("forbidden_scope", []),
            "definition_of_done": task["definition_of_done"],
        },
        "context": {
            "system_of_record": "github",
            "repository": repository,
            "read": read_refs or task.get("inputs", []),
            "project_rules": project_rules,
        },
        "instructions": {
            "role_contract": f"roles/{role}.md",
            "execution": [
                "Read all required durable context before doing work.",
                "Stay within allowed scope and do not silently expand the task.",
                "Follow approved requirements/design and repository rules.",
                "Produce the expected durable outputs and deterministic evidence.",
            ],
            "escalation": [
                "If required context conflicts or is missing, stop and record BLOCKED.",
                "If a decision is outside role authority, escalate instead of guessing.",
            ],
        },
        "handoff": {
            "write_back": task.get("expected_outputs", ["task artifact/evidence"]),
            "completion_detection": [
                "Expected durable outputs exist in the system of record.",
                "Required deterministic checks/evidence satisfy the task Definition of Done.",
                "No unresolved blocker remains.",
            ],
            "blocked_format": "BLOCKED: <reason> | Evidence: <links> | Decision required: <question>",
        },
        "transport": {"runtime": "chatgpt-web", "mode": "manual"},
    }


def render_prompt(package: dict) -> str:
    task = package["task"]
    context = package["context"]
    instructions = package["instructions"]
    handoff = package["handoff"]

    lines = [
        f"You are the AI-SDLC {task['role']} worker for task {task['id']}.",
        "",
        "## Goal",
        task["goal"],
        "",
        "## System of record",
        f"Repository: {context['repository']}",
        "",
        "## Read before acting",
    ]
    lines.extend(f"- {item}" for item in context["read"])
    if context.get("project_rules"):
        lines.extend(["", "## Project rules"])
        lines.extend(f"- {item}" for item in context["project_rules"])

    lines.extend(["", "## Allowed scope"])
    lines.extend(f"- {item}" for item in task.get("allowed_scope", []) or ["Only the scope explicitly required by this task."])
    lines.extend(["", "## Forbidden scope"])
    lines.extend(f"- {item}" for item in task.get("forbidden_scope", []) or ["Do not expand scope without durable approval."])

    lines.extend(["", "## Definition of Done"])
    lines.extend(f"- {item}" for item in task["definition_of_done"])
    lines.extend(["", "## Execution rules"])
    lines.extend(f"- {item}" for item in instructions["execution"])
    lines.extend(["", "## Escalation"])
    lines.extend(f"- {item}" for item in instructions.get("escalation", []))
    lines.extend(["", "## Write back"])
    lines.extend(f"- {item}" for item in handoff["write_back"])
    lines.extend(["", "Do not treat this chat as project state. GitHub/durable artifacts are authoritative."])
    return "\n".join(lines).strip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--read", action="append", default=[])
    parser.add_argument("--rule", action="append", default=[])
    parser.add_argument("--format", choices=["yaml", "prompt"], default="yaml")
    args = parser.parse_args()

    package = build_package(load_yaml(args.task), args.repository, args.read, args.rule)
    if args.format == "prompt":
        print(render_prompt(package), end="")
    else:
        print(yaml.safe_dump(package, sort_keys=False).strip())


if __name__ == "__main__":
    main()
