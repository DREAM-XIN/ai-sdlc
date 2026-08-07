#!/usr/bin/env python3
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
PROFILES = ROOT / "profiles"
GATES = ROOT / "gates" / "core-gates.yaml"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_schemas():
    errors = []
    for path in sorted(SPEC.glob("*.schema.json")):
        schema = load_json(path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    return errors


def validate_profiles():
    errors = []
    schema = load_json(SPEC / "workflow.schema.json")
    validator = Draft202012Validator(schema)
    for path in sorted(PROFILES.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for error in validator.iter_errors(data):
            location = ".".join(str(p) for p in error.absolute_path) or "<root>"
            errors.append(f"{path.relative_to(ROOT)}:{location}: {error.message}")
    return errors


def validate_gate_file():
    errors = []
    with GATES.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "gates" not in data or not isinstance(data["gates"], dict):
        return ["gates/core-gates.yaml: expected top-level 'gates' mapping"]
    for gate_id, gate in data["gates"].items():
        if "checks" not in gate or not gate["checks"]:
            errors.append(f"gates/core-gates.yaml:{gate_id}: at least one check is required")
        if gate.get("pass_policy") not in {"all-required", "all", "custom"}:
            errors.append(f"gates/core-gates.yaml:{gate_id}: invalid pass_policy")
    return errors


def main():
    errors = validate_schemas() + validate_profiles() + validate_gate_file()
    if errors:
        print("AI-SDLC validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("AI-SDLC validation passed")


if __name__ == "__main__":
    main()
