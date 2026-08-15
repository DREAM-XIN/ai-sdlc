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


def _artifact_backed_code_review_remediation(doc, task, stage_by_id):
    """Allow Code-Review-first profiles to remediate their draft implementation artifact.

    The normal contract targets a completed implementation lifecycle stage. A
    deliberately Code-Review-first profile has no such stage; its one draft
    implementation artifact is the reviewed object. In that exact shape the
    existing `stage: implementation` remediation identity is artifact-backed,
    while lifecycle remains at code-review / WORKING.
    """
    if (
        task.get("stage") != "implementation"
        or task.get("source_stage") != "code-review"
        or "implementation" in stage_by_id
        or doc["workflow"].get("current_stage") != "code-review"
        or (stage_by_id.get("code-review") or {}).get("status") != "WORKING"
    ):
        return False
    drafts = [
        artifact
        for artifact in doc.get("artifacts", [])
        if artifact.get("type") == "implementation"
        and artifact.get("status", "draft") == "draft"
    ]
    return len(drafts) == 1


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
    for duplicate in duplicates(stage_ids):
        errors.append(f"semantic: duplicate stage id: {duplicate}")
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

    for task in doc.get("tasks", []):
        if task.get("kind") != "remediation":
            continue
        task_id = task["id"]
        required = ["stage", "role", "source_stage", "feedback"]
        missing = [field for field in required if not task.get(field)]
        if missing:
            errors.append(
                f"semantic: remediation task {task_id} missing fields: {', '.join(missing)}"
            )
            continue
        target_stage = task["stage"]
        source_stage = task["source_stage"]
        artifact_backed = _artifact_backed_code_review_remediation(doc, task, stage_by_id)
        if target_stage not in stage_by_id:
            if not artifact_backed:
                errors.append(f"semantic: remediation task {task_id} references unknown stage {target_stage}")
        elif stage_by_id[target_stage]["status"] != "DONE":
            errors.append(
                f"semantic: remediation task {task_id} targets stage {target_stage} which is not DONE"
            )
        if source_stage not in stage_by_id:
            errors.append(
                f"semantic: remediation task {task_id} references unknown source_stage {source_stage}"
            )
        elif (
            task.get("status") != "DONE"
            and stage_by_id[source_stage]["status"] in {"DONE", "SKIPPED"}
        ):
            errors.append(
                f"semantic: unfinished remediation task {task_id} source_stage {source_stage} is already complete"
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
        unfinished_remediations = [
            task["id"]
            for task in doc.get("tasks", [])
            if task.get("kind") == "remediation" and task["status"] != "DONE"
        ]
        if unfinished_remediations:
            errors.append(
                "semantic: workflow is DONE with unfinished remediation tasks: "
                + ", ".join(unfinished_remediations)
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
