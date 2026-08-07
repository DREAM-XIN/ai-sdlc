#!/usr/bin/env python3
"""Validate and normalize an AI-SDLC project adapter."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spec" / "project-adapter.schema.json"
ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GITHUB_FULL_NAME_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def schema_errors(adapter):
    with SCHEMA.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(adapter):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"project-adapter:{location}: {error.message}")
    return errors


def normalize_repo_path(value: str):
    if not isinstance(value, str) or not value:
        return None, "path must be a non-empty string"
    if "\x00" in value or "\n" in value or "\r" in value:
        return None, "path contains a control character"
    if "\\" in value:
        return None, "path must use POSIX '/' separators"
    if value.startswith("/") or value.startswith("~"):
        return None, "path must be repository-relative"
    path = PurePosixPath(value)
    if any(part == ".." for part in path.parts):
        return None, "path traversal is not allowed"
    normalized = path.as_posix()
    if normalized in {"", "/"}:
        return None, "path must not be empty"
    return normalized, None


def roots_overlap(left: str, right: str) -> bool:
    if left == "." or right == ".":
        return True
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def validate_adapter(adapter):
    errors = schema_errors(adapter)
    if errors:
        return errors

    project_id = adapter["project"]["id"]
    if not ID_RE.fullmatch(project_id):
        errors.append("project-adapter:project.id must contain only letters, digits, '.', '_' or '-'")

    for key in ("workflow_profile", "runtime_policy"):
        value = adapter["defaults"][key]
        if not ID_RE.fullmatch(value):
            errors.append(f"project-adapter:defaults.{key} must be a portable identifier")

    repository = adapter.get("repository") or {}
    if repository.get("provider") == "github" and repository.get("full_name"):
        if not GITHUB_FULL_NAME_RE.fullmatch(repository["full_name"]):
            errors.append("project-adapter:repository.full_name must use owner/name form for GitHub")

    path_fields = []
    for value in adapter["context"]["rules"]:
        path_fields.append(("context.rules", value))
    for value in adapter["context"]["read"]:
        path_fields.append(("context.read", value))
    for command in adapter["commands"]:
        path_fields.append((f"commands.{command['id']}.cwd", command["cwd"]))
    for owner in adapter["ownership"]:
        for value in owner["roots"]:
            path_fields.append((f"ownership.{owner['id']}.roots", value))

    normalized_paths = {}
    for label, value in path_fields:
        normalized, problem = normalize_repo_path(value)
        if problem:
            errors.append(f"project-adapter:{label}: {problem}: {value!r}")
        else:
            normalized_paths[(label, value)] = normalized

    command_ids = [command["id"] for command in adapter["commands"]]
    duplicate_commands = sorted({item for item in command_ids if command_ids.count(item) > 1})
    errors.extend(f"project-adapter: duplicate command id: {item}" for item in duplicate_commands)

    command_id_set = set(command_ids)
    for command_id in adapter["defaults"]["required_commands"]:
        if command_id not in command_id_set:
            errors.append(f"project-adapter: required command does not exist: {command_id}")

    for command in adapter["commands"]:
        if not ID_RE.fullmatch(command["id"]):
            errors.append(f"project-adapter: command id is not portable: {command['id']}")
        for index, arg in enumerate(command["argv"]):
            if any(ch in arg for ch in ("\x00", "\n", "\r")):
                errors.append(
                    f"project-adapter: command {command['id']} argv[{index}] contains a control character"
                )

    owner_ids = [owner["id"] for owner in adapter["ownership"]]
    duplicate_owners = sorted({item for item in owner_ids if owner_ids.count(item) > 1})
    errors.extend(f"project-adapter: duplicate ownership id: {item}" for item in duplicate_owners)

    ownership_roots = []
    for owner in adapter["ownership"]:
        if not ID_RE.fullmatch(owner["id"]):
            errors.append(f"project-adapter: ownership id is not portable: {owner['id']}")
        for root in owner["roots"]:
            normalized = normalized_paths.get((f"ownership.{owner['id']}.roots", root))
            if normalized is not None:
                ownership_roots.append((owner["id"], bool(owner.get("shared", False)), normalized))

    for index, (left_id, left_shared, left_root) in enumerate(ownership_roots):
        for right_id, right_shared, right_root in ownership_roots[index + 1 :]:
            if left_id == right_id:
                continue
            if roots_overlap(left_root, right_root) and not (left_shared or right_shared):
                errors.append(
                    "project-adapter: ambiguous ownership overlap: "
                    f"{left_id}:{left_root} <-> {right_id}:{right_root}; mark a boundary shared or make roots disjoint"
                )

    return errors


def normalize_adapter(adapter):
    """Return a deterministic copy after validation; callers must validate first."""
    normalized = json.loads(json.dumps(adapter))
    normalized["context"]["rules"] = [normalize_repo_path(value)[0] for value in adapter["context"]["rules"]]
    normalized["context"]["read"] = [normalize_repo_path(value)[0] for value in adapter["context"]["read"]]
    for command in normalized["commands"]:
        command["cwd"] = normalize_repo_path(command["cwd"])[0]
    for owner in normalized["ownership"]:
        owner["roots"] = [normalize_repo_path(value)[0] for value in owner["roots"]]
    return normalized


def load_project_adapter(path: Path):
    adapter = load_yaml(path)
    errors = validate_adapter(adapter)
    if errors:
        return {"outcome": "INVALID", "errors": errors}
    return {"outcome": "VALID", "errors": [], "adapter": normalize_adapter(adapter)}


def main():
    parser = argparse.ArgumentParser(description="Validate an AI-SDLC project adapter")
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    result = load_project_adapter(args.project)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
