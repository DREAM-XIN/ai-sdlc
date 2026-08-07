#!/usr/bin/env python3
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from evaluate_gate import evaluate

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
PROFILES = ROOT / "profiles"
GATES = ROOT / "gates" / "core-gates.yaml"
RUBRICS = ROOT / "gates" / "review-rubrics.yaml"
TASK_PACKAGE_EXAMPLES = ROOT / "examples" / "chatgpt-web"
GATE_EXAMPLES = ROOT / "examples" / "gates"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
        data = load_yaml(path)
        for error in validator.iter_errors(data):
            location = ".".join(str(p) for p in error.absolute_path) or "<root>"
            errors.append(f"{path.relative_to(ROOT)}:{location}: {error.message}")
    return errors


def validate_task_packages():
    errors = []
    schema = load_json(SPEC / "task-package.schema.json")
    validator = Draft202012Validator(schema)
    for path in sorted(TASK_PACKAGE_EXAMPLES.glob("*.yaml")):
        data = load_yaml(path)
        for error in validator.iter_errors(data):
            location = ".".join(str(p) for p in error.absolute_path) or "<root>"
            errors.append(f"{path.relative_to(ROOT)}:{location}: {error.message}")
    return errors


def validate_gate_file():
    errors = []
    data = load_yaml(GATES)
    if not isinstance(data, dict) or "gates" not in data or not isinstance(data["gates"], dict):
        return ["gates/core-gates.yaml: expected top-level 'gates' mapping"]
    for gate_id, gate in data["gates"].items():
        if "checks" not in gate or not gate["checks"]:
            errors.append(f"gates/core-gates.yaml:{gate_id}: at least one check is required")
        if gate.get("pass_policy") not in {"all-required", "all"}:
            errors.append(f"gates/core-gates.yaml:{gate_id}: unsupported v0.1 pass_policy")
        for index, check in enumerate(gate.get("checks", [])):
            if not check.get("target"):
                errors.append(f"gates/core-gates.yaml:{gate_id}.checks[{index}]: target is required")
    return errors


def validate_rubrics():
    errors = []
    data = load_yaml(RUBRICS)
    required_severities = {"BLOCKER", "MAJOR", "MINOR", "SUGGESTION"}
    severities = set((data or {}).get("severities", {}))
    missing = required_severities - severities
    if missing:
        errors.append(f"gates/review-rubrics.yaml: missing severities {sorted(missing)}")
    rubrics = (data or {}).get("rubrics")
    if not isinstance(rubrics, dict) or not rubrics:
        return errors + ["gates/review-rubrics.yaml: at least one rubric is required"]
    for rubric_id, rubric in rubrics.items():
        if not rubric.get("dimensions"):
            errors.append(f"gates/review-rubrics.yaml:{rubric_id}: dimensions are required")
        if not rubric.get("blocker_rules"):
            errors.append(f"gates/review-rubrics.yaml:{rubric_id}: blocker_rules are required")
    return errors


def validate_gate_examples():
    errors = []
    passing_fixture = GATE_EXAMPLES / "code-gate-pass.yaml"
    result = evaluate("code-gate", load_yaml(passing_fixture))
    if not result["pass"]:
        errors.append(f"{passing_fixture.relative_to(ROOT)}: expected code-gate to pass")

    incomplete_state = {"satisfied": ["code:blockers=0"]}
    result = evaluate("code-gate", incomplete_state)
    if result["pass"]:
        errors.append("gate evaluator: incomplete code-gate state unexpectedly passed")
    return errors


def main():
    errors = (
        validate_schemas()
        + validate_profiles()
        + validate_task_packages()
        + validate_gate_file()
        + validate_rubrics()
        + validate_gate_examples()
    )
    if errors:
        print("AI-SDLC validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("AI-SDLC validation passed")


if __name__ == "__main__":
    main()
