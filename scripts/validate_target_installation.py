#!/usr/bin/env python3
"""Fail-fast validation for an installed cross-repository AI-SDLC target contract."""

import argparse
import json
import re
from pathlib import Path

from project_adapter import load_project_adapter

AI_SDLC_REF = re.compile(r"DREAM-XIN/ai-sdlc/[^@\s#]+@([^\s#]+)")
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
INSTALL_PLACEHOLDER = "REPLACE_WITH_AI_SDLC_FULL_SHA"


def validate_regular_file(workspace: Path, relative_path: str, label: str):
    errors = []
    candidate = workspace / relative_path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return [f"installation:{label}: resolves outside target workspace: {relative_path}"]
    if candidate.is_symlink():
        errors.append(f"installation:{label}: must not be a symlink: {relative_path}")
    elif not candidate.exists() or not candidate.is_file():
        errors.append(f"installation:{label}: required file does not exist: {relative_path}")
    return errors


def validate_directory(workspace: Path, relative_path: str, label: str):
    candidate = workspace / relative_path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return [f"installation:{label}: resolves outside target workspace: {relative_path}"]
    if candidate.is_symlink():
        return [f"installation:{label}: must not be a symlink: {relative_path}"]
    if not candidate.exists() or not candidate.is_dir():
        return [f"installation:{label}: required directory does not exist: {relative_path}"]
    return []


def validate_caller_pins(workspace: Path):
    errors = []
    workflow_root = workspace / ".github" / "workflows"
    if not workflow_root.exists():
        return ["installation:caller-workflows: .github/workflows does not exist"]
    if workflow_root.is_symlink() or not workflow_root.is_dir():
        return ["installation:caller-workflows: .github/workflows must be a real directory"]

    found_ai_sdlc_ref = False
    for path in sorted(workflow_root.glob("ai-sdlc-*.y*ml")):
        if path.is_symlink() or not path.is_file():
            errors.append(f"installation:caller-workflows: workflow must be a regular file: {path.relative_to(workspace)}")
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(workspace).as_posix()
        if INSTALL_PLACEHOLDER in text:
            errors.append(f"installation:{relative}: unresolved AI-SDLC install SHA placeholder")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in AI_SDLC_REF.finditer(line):
                found_ai_sdlc_ref = True
                ref = match.group(1)
                if not FULL_SHA.fullmatch(ref):
                    errors.append(
                        f"installation:{relative}:{line_number}: AI-SDLC caller must use a full 40-character commit SHA, got {ref!r}"
                    )

    if not found_ai_sdlc_ref:
        errors.append(
            "installation:caller-workflows: no pinned DREAM-XIN/ai-sdlc caller reference found under .github/workflows/ai-sdlc-*.yml"
        )
    return errors


def validate_installation(
    workspace: Path,
    project_path: str = ".ai-sdlc/project.yaml",
    expected_repository: str | None = None,
    expected_default_branch: str | None = None,
):
    workspace = workspace.resolve()
    errors = []

    project_file = workspace / project_path
    errors.extend(validate_regular_file(workspace, project_path, "project-adapter"))
    if errors:
        return {"outcome": "INVALID", "errors": errors, "checked": {"workspace": str(workspace)}}

    loaded = load_project_adapter(project_file)
    if loaded["outcome"] != "VALID":
        return {
            "outcome": "INVALID",
            "errors": loaded["errors"],
            "checked": {"workspace": str(workspace), "project_path": project_path},
        }
    adapter = loaded["adapter"]

    repository = adapter.get("repository") or {}
    if expected_repository:
        configured = repository.get("full_name")
        if configured != expected_repository:
            errors.append(
                f"installation:repository.full_name mismatch: adapter={configured!r} live={expected_repository!r}"
            )
    if expected_default_branch:
        configured = repository.get("default_branch")
        if configured != expected_default_branch:
            errors.append(
                f"installation:repository.default_branch mismatch: adapter={configured!r} live={expected_default_branch!r}"
            )

    for index, relative_path in enumerate(adapter["context"]["rules"]):
        errors.extend(validate_regular_file(workspace, relative_path, f"context.rules[{index}]"))
    for index, relative_path in enumerate(adapter["context"]["read"]):
        errors.extend(validate_regular_file(workspace, relative_path, f"context.read[{index}]"))

    # AGENTS.md is part of the installed cross-repository worker/Commander contract,
    # even if a malformed/legacy adapter forgets to list it under context.rules.
    if "AGENTS.md" not in adapter["context"]["rules"]:
        errors.append("installation:context.rules must include repository-wide AGENTS.md")
    errors.extend(validate_regular_file(workspace, "AGENTS.md", "AGENTS.md"))

    for command in adapter["commands"]:
        errors.extend(
            validate_directory(workspace, command["cwd"], f"commands.{command['id']}.cwd")
        )

    errors.extend(validate_caller_pins(workspace))

    return {
        "outcome": "INVALID" if errors else "READY",
        "errors": errors,
        "checked": {
            "workspace": str(workspace),
            "project_path": project_path,
            "repository": repository.get("full_name"),
            "default_branch": repository.get("default_branch"),
            "rules": adapter["context"]["rules"],
            "read": adapter["context"]["read"],
            "required_commands": adapter["defaults"]["required_commands"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Validate an installed AI-SDLC target repository contract")
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--project", default=".ai-sdlc/project.yaml")
    parser.add_argument("--repository", default="")
    parser.add_argument("--default-branch", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = validate_installation(
        args.workspace,
        project_path=args.project,
        expected_repository=args.repository or None,
        expected_default_branch=args.default_branch or None,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if result["outcome"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
