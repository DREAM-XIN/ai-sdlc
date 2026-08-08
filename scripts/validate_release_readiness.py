#!/usr/bin/env python3
"""Validate the declared AI-SDLC release/conformance baseline."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
RELEASE_MANIFEST = ROOT / "release" / "v0.1.0.yaml"
VALIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "validate.yml"
CHANGELOG = ROOT / "CHANGELOG.md"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
JSON_SCHEMA_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def require(condition, message, errors):
    if not condition:
        errors.append(message)


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main():
    errors = []

    require(VERSION_FILE.exists(), "VERSION file is missing", errors)
    require(RELEASE_MANIFEST.exists(), "release/v0.1.0.yaml is missing", errors)
    require(CHANGELOG.exists(), "CHANGELOG.md is missing", errors)
    require(VALIDATE_WORKFLOW.exists(), "validation workflow is missing", errors)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(2)

    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    release = load_yaml(RELEASE_MANIFEST)
    validate_workflow = VALIDATE_WORKFLOW.read_text(encoding="utf-8")
    changelog = CHANGELOG.read_text(encoding="utf-8")

    require(bool(SEMVER.fullmatch(version)), f"VERSION is not MAJOR.MINOR.PATCH: {version!r}", errors)
    require(release.get("version") == version, f"release manifest version {release.get('version')!r} != VERSION {version!r}", errors)
    require(release.get("status") == "release-candidate", "v0.1 baseline must remain release-candidate until tag publication", errors)
    require(f"## {version}" in changelog, f"CHANGELOG.md does not contain a {version} section", errors)

    declared_schemas = release.get("normative_schemas", [])
    require(len(declared_schemas) == len(set(declared_schemas)), "release manifest contains duplicate normative schemas", errors)
    actual_schemas = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "spec").glob("*.schema.json"))
    require(sorted(declared_schemas) == actual_schemas, "normative schema manifest does not exactly match spec/*.schema.json", errors)

    for relative in declared_schemas:
        path = ROOT / relative
        require(path.is_file(), f"declared schema is missing: {relative}", errors)
        if not path.is_file():
            continue
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON schema {relative}: {exc}")
            continue
        require(schema.get("$schema") == JSON_SCHEMA_2020_12, f"{relative}: not JSON Schema 2020-12", errors)
        schema_id = schema.get("$id")
        require(isinstance(schema_id, str) and schema_id.endswith("/" + path.name), f"{relative}: unexpected or missing $id", errors)

    declared_profiles = release.get("workflow_profiles", [])
    require(len(declared_profiles) == len(set(declared_profiles)), "release manifest contains duplicate workflow profiles", errors)
    actual_profiles = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "profiles").glob("*.yaml"))
    require(sorted(declared_profiles) == actual_profiles, "workflow profile manifest does not exactly match profiles/*.yaml", errors)
    for relative in declared_profiles:
        path = ROOT / relative
        require(path.is_file(), f"declared workflow profile is missing: {relative}", errors)
        if path.is_file():
            profile = load_yaml(path)
            require(profile.get("id") == path.stem, f"{relative}: profile id does not match filename", errors)

    entry_points = release.get("reference_entry_points", {})
    require(bool(entry_points), "release manifest has no reference entry points", errors)
    for name, relative in entry_points.items():
        require((ROOT / relative).is_file(), f"reference entry point {name} is missing: {relative}", errors)

    for relative in release.get("required_docs", []):
        require((ROOT / relative).is_file(), f"required release document is missing: {relative}", errors)

    validators = release.get("required_ci_validators", [])
    require(len(validators) == len(set(validators)), "release manifest contains duplicate CI validators", errors)
    for relative in validators:
        path = ROOT / relative
        require(path.is_file(), f"required CI validator is missing: {relative}", errors)
        require(f"python {relative}" in validate_workflow, f"required CI validator is not wired into validate.yml: {relative}", errors)

    blockers = release.get("known_release_blockers", [])
    require(isinstance(blockers, list), "known_release_blockers must be a list", errors)
    blocker_ids = []
    if isinstance(blockers, list):
        for item in blockers:
            require(isinstance(item, dict), "each release blocker must be an object", errors)
            if not isinstance(item, dict):
                continue
            blocker_id = item.get("id")
            require(isinstance(blocker_id, str) and blocker_id, "release blocker id is required", errors)
            require(isinstance(item.get("issue"), str) and item.get("issue"), f"release blocker {blocker_id!r} must reference an issue", errors)
            require(isinstance(item.get("description"), str) and item.get("description"), f"release blocker {blocker_id!r} must have a description", errors)
            if isinstance(blocker_id, str) and blocker_id:
                blocker_ids.append(blocker_id)
    require(len(blocker_ids) == len(set(blocker_ids)), "release manifest contains duplicate blocker ids", errors)

    require(release.get("release_policy", {}).get("publish_tag_after_blockers_clear") is True, "release policy must keep tag publication behind declared blockers", errors)
    require(release.get("release_policy", {}).get("production_caller_pin") == "full-commit-sha", "release policy must require full-commit-sha production caller pins", errors)

    for critical in (
        "scripts/validate_action_security.py",
        "scripts/validate_github_workflow_security.py",
        "scripts/validate_git_write_precondition.py",
        "scripts/validate_gh_aw_adapter.py",
        "scripts/validate_gh_aw_workflow_security.py",
    ):
        require(critical in validators, f"critical validator omitted from release baseline: {critical}", errors)

    if errors:
        for error in errors:
            print(error)
        raise SystemExit(2)

    print(
        f"AI-SDLC {version} release readiness baseline passed: "
        f"{len(declared_schemas)} schemas, {len(declared_profiles)} profiles, "
        f"{len(validators)} CI validators, {len(blockers)} blockers"
    )


if __name__ == "__main__":
    main()
