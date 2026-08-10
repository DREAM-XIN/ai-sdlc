#!/usr/bin/env python3
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from evaluate_gate import evaluate
from render_task_package import build_package
from validate_artifact_event_lifecycle import main as validate_artifact_event_lifecycle
from validate_gh_aw_autonomous_roles import main as validate_gh_aw_autonomous_roles
from validate_gh_aw_candidate_history import validate_manual_candidate, validate_multi_round_supersession
from validate_gh_aw_gate_worker_security import main as validate_gh_aw_gate_worker_security
from validate_gh_aw_profile_routing import main as validate_gh_aw_profile_routing
from validate_operator_api import main as validate_operator_api
from validate_operator_mcp import main as validate_operator_mcp
from validate_remediation_review_completion import main as validate_remediation_review_completion
from validate_transition import validate_schema as validate_execution_schema
from validate_transition import validate_transition

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
PROFILES = ROOT / "profiles"
GATES = ROOT / "gates" / "core-gates.yaml"
RUBRICS = ROOT / "gates" / "review-rubrics.yaml"
TASK_EXAMPLES = ROOT / "examples" / "tasks"
TASK_PACKAGE_EXAMPLES = ROOT / "examples" / "chatgpt-web"
GATE_EXAMPLES = ROOT / "examples" / "gates"
EXECUTION_EXAMPLES = ROOT / "examples" / "execution"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_with_schema(data, schema_path: Path, label: str):
    errors = []
    validator = Draft202012Validator(load_json(schema_path))
    for error in validator.iter_errors(data):
        location = ".".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"{label}:{location}: {error.message}")
    return errors


def validate_schemas():
    errors = []
    for path in sorted(SPEC.glob("*.schema.json")):
        schema = load_json(path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    for path in sorted((ROOT / "runtimes" / "gh-aw").glob("*-result.schema.json")):
        schema = load_json(path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    return errors


def validate_profiles():
    errors = []
    for path in sorted(PROFILES.glob("*.yaml")):
        errors += validate_with_schema(
            load_yaml(path), SPEC / "workflow.schema.json", str(path.relative_to(ROOT))
        )
    return errors


def validate_tasks_and_packages():
    errors = []
    task_schema = SPEC / "task.schema.json"
    package_schema = SPEC / "task-package.schema.json"

    for path in sorted(TASK_EXAMPLES.glob("*.yaml")):
        task = load_yaml(path)
        errors += validate_with_schema(task, task_schema, str(path.relative_to(ROOT)))
        package = build_package(
            task,
            repository="example-org/example-repo",
            read_refs=task.get("inputs", []),
            project_rules=["AGENTS.md"],
        )
        errors += validate_with_schema(
            package, package_schema, f"rendered:{path.relative_to(ROOT)}"
        )

    for path in sorted(TASK_PACKAGE_EXAMPLES.glob("*.yaml")):
        errors += validate_with_schema(
            load_yaml(path), package_schema, str(path.relative_to(ROOT))
        )
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


def validate_execution_examples():
    errors = []
    schema = load_json(SPEC / "task-execution.schema.json")
    ready = load_yaml(EXECUTION_EXAMPLES / "ready.yaml")
    started = load_yaml(EXECUTION_EXAMPLES / "started.yaml")
    submitted = load_yaml(EXECUTION_EXAMPLES / "submitted.yaml")
    completed = load_yaml(EXECUTION_EXAMPLES / "completed.yaml")

    for label, doc in (("ready", ready), ("started", started), ("submitted", submitted), ("completed", completed)):
        try:
            validate_execution_schema(doc, schema, label)
        except ValueError as exc:
            errors.append(str(exc))

    for before, after in ((ready, started), (started, submitted), (submitted, completed)):
        try:
            validate_transition(before, after)
        except ValueError as exc:
            errors.append(f"expected valid transition failed: {exc}")

    try:
        illegal = dict(completed)
        illegal["previous_state"] = "READY"
        validate_transition(ready, illegal)
        errors.append("execution validator: illegal READY -> COMPLETED unexpectedly passed")
    except ValueError:
        pass

    invalid_docs = [
        {**started, "state": "BLOCKED", "previous_state": "STARTED"},
        {**started, "state": "FAILED", "previous_state": "STARTED"},
        {**submitted, "state": "COMPLETED", "previous_state": "SUBMITTED"},
    ]
    for index, doc in enumerate(invalid_docs):
        try:
            validate_execution_schema(doc, schema, f"invalid-{index}")
            errors.append(f"execution schema: invalid state-specific fixture {index} unexpectedly passed")
        except ValueError:
            pass

    identity_mutation = dict(started)
    identity_mutation["task_id"] = "TASK-OTHER"
    try:
        validate_transition(ready, identity_mutation)
        errors.append("execution validator: identity mutation unexpectedly passed")
    except ValueError:
        pass

    return errors


def main():
    validate_artifact_event_lifecycle()
    validate_remediation_review_completion()
    validate_gh_aw_profile_routing()
    validate_gh_aw_autonomous_roles()
    validate_manual_candidate()
    validate_multi_round_supersession()
    validate_gh_aw_gate_worker_security()
    validate_operator_api()
    validate_operator_mcp()
    errors = (
        validate_schemas()
        + validate_profiles()
        + validate_tasks_and_packages()
        + validate_gate_file()
        + validate_rubrics()
        + validate_gate_examples()
        + validate_execution_examples()
    )
    if errors:
        print("AI-SDLC validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("AI-SDLC validation passed")


if __name__ == "__main__":
    main()
