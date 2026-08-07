#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "spec" / "task-execution.schema.json"

ALLOWED = {
    "READY": {"STARTED", "FAILED"},
    "STARTED": {"BLOCKED", "SUBMITTED", "FAILED"},
    "BLOCKED": {"STARTED", "FAILED"},
    "SUBMITTED": {"STARTED", "COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


def load_document(path: Path):
    with path.open("r", encoding="utf-8") as f:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(f)
        return json.load(f)


def validate_schema(doc, schema, label):
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for error in errors:
            location = ".".join(str(p) for p in error.absolute_path) or "<root>"
            lines.append(f"{label}:{location}: {error.message}")
        raise ValueError("\n".join(lines))


def validate_transition(before, after):
    for field in ("id", "task_id", "runtime_id"):
        if before[field] != after[field]:
            raise ValueError(f"identity field changed: {field}")
    if before.get("feature_id") != after.get("feature_id"):
        raise ValueError("identity field changed: feature_id")

    source = before["state"]
    target = after["state"]
    if target not in ALLOWED[source]:
        raise ValueError(f"illegal transition: {source} -> {target}")
    if after.get("previous_state") != source:
        raise ValueError(f"previous_state must equal {source}")


def main():
    parser = argparse.ArgumentParser(description="Validate an AI-SDLC TaskExecution transition")
    parser.add_argument("before")
    parser.add_argument("after")
    args = parser.parse_args()

    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    before = load_document(Path(args.before))
    after = load_document(Path(args.after))
    validate_schema(before, schema, "before")
    validate_schema(after, schema, "after")
    validate_transition(before, after)
    print(f"valid transition: {before['state']} -> {after['state']}")


if __name__ == "__main__":
    main()
