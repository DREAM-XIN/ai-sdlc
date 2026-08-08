#!/usr/bin/env python3
import argparse
import copy
import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from validate_feature_manifest import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
EVENT_SCHEMA = ROOT / "spec" / "feature-event.schema.json"

STAGE_ALLOWED = {
    "TODO": {"READY", "WORKING", "BLOCKED", "SKIPPED"},
    "READY": {"WORKING", "BLOCKED", "SKIPPED"},
    "WORKING": {"REVIEW", "DONE", "BLOCKED", "READY"},
    "REVIEW": {"DONE", "BLOCKED", "READY", "WORKING"},
    "BLOCKED": {"READY", "WORKING", "SKIPPED"},
    "DONE": set(),
    "SKIPPED": set(),
}
TASK_ALLOWED = {
    "TODO": {"READY", "WORKING", "BLOCKED", "FAILED"},
    "READY": {"WORKING", "BLOCKED", "FAILED"},
    "WORKING": {"DONE", "BLOCKED", "FAILED", "READY"},
    "BLOCKED": {"READY", "WORKING", "FAILED"},
    "REVIEW": {"DONE", "BLOCKED", "FAILED", "READY", "WORKING"},
    "FAILED": {"READY", "WORKING"},
    "DONE": set(),
}
GATE_ALLOWED = {
    "PENDING": {"PASS", "FAIL", "WAIVED"},
    "FAIL": {"PENDING"},
    "PASS": {"PENDING"},
    "WAIVED": {"PENDING"},
}
COMPLETE_STAGE_STATES = {"DONE", "SKIPPED"}
PASSING_GATE_STATES = {"PASS", "WAIVED"}
TERMINAL_WORKFLOW_STATES = {"DONE", "CANCELLED"}


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_event(event):
    with EVENT_SCHEMA.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(event):
        location = ".".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"event:{location}: {error.message}")
    return errors


def effective_event_id(event):
    explicit = event.get("id")
    if explicit:
        return explicit
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return "legacy-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def manifest_revision(manifest):
    """Legacy v0.1 manifests without revision are treated as revision zero."""
    return manifest.get("revision", 0)


def recompute_workflow(manifest):
    stages = manifest["workflow"]["stages"]
    gates = manifest.get("gates", [])
    if any(stage["status"] == "BLOCKED" for stage in stages):
        manifest["workflow"]["status"] = "BLOCKED"
    elif all(stage["status"] in COMPLETE_STAGE_STATES for stage in stages) and all(
        gate["status"] in PASSING_GATE_STATES for gate in gates
    ):
        manifest["workflow"]["status"] = "DONE"
    else:
        manifest["workflow"]["status"] = "ACTIVE"

    unfinished = [stage for stage in stages if stage["status"] not in COMPLETE_STAGE_STATES]
    manifest["workflow"]["current_stage"] = (unfinished[0] if unfinished else stages[-1])["id"]


def apply_event(manifest, event):
    errors = validate_event(event)
    if errors:
        return {"outcome": "INVALID", "errors": errors}
    if event["feature_id"] != manifest["feature"]["id"]:
        return {
            "outcome": "INVALID",
            "errors": [f"feature id mismatch: manifest={manifest['feature']['id']} event={event['feature_id']}"],
        }

    event_id = effective_event_id(event)
    applied_events = set(manifest.get("applied_events", []))
    if event_id in applied_events:
        return {
            "outcome": "INVALID",
            "errors": [f"event already applied: {event_id}"],
        }

    current_revision = manifest_revision(manifest)
    expected_revision = event.get("expected_revision")
    if expected_revision is not None and expected_revision != current_revision:
        return {
            "outcome": "INVALID",
            "errors": [
                f"stale event revision: manifest={current_revision} event_expected={expected_revision}"
            ],
        }

    workflow_status = manifest["workflow"]["status"]
    if workflow_status in TERMINAL_WORKFLOW_STATES:
        return {
            "outcome": "INVALID",
            "errors": [f"cannot apply event to terminal workflow: {workflow_status}"],
        }

    result = copy.deepcopy(manifest)
    stage_by_id = {stage["id"]: stage for stage in result["workflow"]["stages"]}
    tasks = result.setdefault("tasks", [])
    task_by_id = {task["id"]: task for task in tasks}
    gate_by_id = {gate["id"]: gate for gate in result.setdefault("gates", [])}
    evidence = result.setdefault("evidence", [])
    evidence_ids = {item["id"] for item in evidence}

    # Durable records are appended first so status/gate changes in the same event may reference them.
    for change in event["changes"]:
        if change["kind"] == "evidence":
            record = change["record"]
            if record["id"] in evidence_ids:
                return {"outcome": "INVALID", "errors": [f"duplicate evidence id: {record['id']}"]}
            evidence.append(copy.deepcopy(record))
            evidence_ids.add(record["id"])
        elif change["kind"] == "task-record":
            record = change["record"]
            if record["id"] in task_by_id:
                return {"outcome": "INVALID", "errors": [f"duplicate task id: {record['id']}"]}
            tasks.append(copy.deepcopy(record))
            task_by_id[record["id"]] = tasks[-1]

    for change in event["changes"]:
        kind = change["kind"]
        if kind in {"evidence", "task-record"}:
            continue
        if kind == "stage":
            stage = stage_by_id.get(change["id"])
            if not stage:
                return {"outcome": "INVALID", "errors": [f"unknown stage: {change['id']}"]}
            source = stage["status"]
            target = change["status"]
            if target not in STAGE_ALLOWED[source]:
                return {"outcome": "INVALID", "errors": [f"illegal stage transition: {change['id']} {source} -> {target}"]}
            stage["status"] = target
        elif kind == "task":
            task = task_by_id.get(change["id"])
            if not task:
                return {"outcome": "INVALID", "errors": [f"unknown task: {change['id']}"]}
            source = task["status"]
            target = change["status"]
            allowed = TASK_ALLOWED.get(source, set())
            if target not in allowed:
                return {"outcome": "INVALID", "errors": [f"illegal task transition: {change['id']} {source} -> {target}"]}
            task["status"] = target
        elif kind == "gate":
            gate = gate_by_id.get(change["id"])
            if not gate:
                return {"outcome": "INVALID", "errors": [f"unknown gate: {change['id']}"]}
            source = gate["status"]
            target = change["status"]
            if target not in GATE_ALLOWED[source]:
                return {"outcome": "INVALID", "errors": [f"illegal gate transition: {change['id']} {source} -> {target}"]}
            refs = change.get("evidence", [])
            if target in {"PASS", "FAIL", "WAIVED"} and not refs:
                return {"outcome": "INVALID", "errors": [f"gate {change['id']} {target} requires evidence"]}
            unknown = sorted(set(refs) - evidence_ids)
            if unknown:
                return {"outcome": "INVALID", "errors": [f"gate {change['id']} references unknown evidence: {', '.join(unknown)}"]}
            gate["status"] = target
            if refs:
                gate["evidence"] = sorted(set(gate.get("evidence", [])) | set(refs))

    result.setdefault("applied_events", []).append(event_id)
    result["revision"] = current_revision + 1
    result["updated_at"] = event["occurred_at"]
    recompute_workflow(result)
    manifest_errors = validate_manifest(result)
    if manifest_errors:
        return {"outcome": "INVALID", "errors": manifest_errors}
    return {
        "outcome": "APPLIED",
        "errors": [],
        "manifest": result,
        "event_id": event_id,
        "source_revision": current_revision,
        "result_revision": result["revision"],
    }


def main():
    parser = argparse.ArgumentParser(description="Apply an AI-SDLC Feature Event to a Feature Manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("event", type=Path)
    args = parser.parse_args()

    result = apply_event(load_yaml(args.manifest), load_yaml(args.event))
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
