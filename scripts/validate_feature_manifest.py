#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "spec" / "feature-manifest.schema.json"


def load_document(path: Path):
    with path.open("r", encoding="utf-8") as f:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(f)
        return json.load(f)


def duplicates(items):
    seen = set()
    dupes = set()
    for item in items:
        if item in seen:
            dupes.add(item)
        seen.add(item)
    return sorted(dupes)


def validate_manifest(doc):
    errors = []

    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    for error in Draft202012Validator(schema).iter_errors(doc):
        location = ".".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"schema:{location}: {error.message}")
    if errors:
        return errors

    stages = doc["workflow"]["stages"]
    stage_ids = [stage["id"] for stage in stages]
    stage_by_id = {stage["id"]: stage for stage in stages}
    current_stage = doc["workflow"]["current_stage"]
    if current_stage not in stage_by_id:
        errors.append(f"semantic: unknown current_stage: {current_stage}")

    for kind in ("tasks", "artifacts", "gates", "evidence"):
        ids = [item["id"] for item in doc.get(kind, [])]
        for duplicate in duplicates(ids):
            errors.append(f"semantic: duplicate {kind[:-1]} id: {duplicate}")

    gate_by_id = {gate["id"]: gate for gate in doc.get("gates", [])}
    evidence_ids = {item["id"] for item in doc.get("evidence", [])}

    for stage in stages:
        gate_id = stage.get("gate")
        if gate_id and gate_id not in gate_by_id:
            errors.append(f"semantic: stage {stage['id']} references unknown gate {gate_id}")
        if stage["status"] == "DONE" and gate_id:
            gate = gate_by_id.get(gate_id)
            if gate and gate["status"] not in {"PASS", "WAIVED"}:
                errors.append(
                    f"semantic: stage {stage['id']} is DONE but gate {gate_id} is {gate['status']}"
                )

    for gate in doc.get("gates", []):
        for evidence_id in gate.get("evidence", []):
            if evidence_id not in evidence_ids:
                errors.append(
                    f"semantic: gate {gate['id']} references unknown evidence {evidence_id}"
                )

    if doc["workflow"]["status"] == "DONE":
        unfinished = [
            stage["id"]
            for stage in stages
            if stage["status"] not in {"DONE", "SKIPPED"}
        ]
        if unfinished:
            errors.append(
                "semantic: workflow is DONE with unfinished stages: " + ", ".join(unfinished)
            )
        failing_gates = [
            gate["id"]
            for gate in doc.get("gates", [])
            if gate["status"] in {"FAIL", "PENDING"}
        ]
        if failing_gates:
            errors.append(
                "semantic: workflow is DONE with non-passing gates: " + ", ".join(failing_gates)
            )

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate an AI-SDLC Feature Manifest")
    parser.add_argument("manifest")
    args = parser.parse_args()
    doc = load_document(Path(args.manifest))
    errors = validate_manifest(doc)
    if errors:
        print("Feature Manifest validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("Feature Manifest validation passed")


if __name__ == "__main__":
    main()
