#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

import yaml

from orchestrator_state import compute_state

ROOT = Path(__file__).resolve().parents[1]


def load_profile(name):
    with (ROOT / "profiles" / f"{name}.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def base_manifest():
    return {
        "protocol_version": "0.1.0",
        "feature": {"id": "F-0030", "title": "Orchestrator state engine", "risk": "medium"},
        "workflow": {
            "profile": "standard-feature",
            "status": "ACTIVE",
            "current_stage": "design",
            "stages": [
                {"id": "requirement", "status": "DONE"},
                {"id": "requirement-review", "status": "DONE", "gate": "requirement-gate"},
                {"id": "design", "status": "READY"},
                {"id": "design-review", "status": "TODO", "gate": "design-gate"},
                {"id": "plan", "status": "TODO"},
                {"id": "implementation", "status": "TODO"},
                {"id": "code-review", "status": "TODO", "gate": "code-gate"},
                {"id": "verification", "status": "TODO", "gate": "verification-gate"},
                {"id": "acceptance", "status": "TODO", "gate": "release-gate"},
            ],
        },
        "gates": [
            {"id": "requirement-gate", "status": "PASS"},
            {"id": "design-gate", "status": "PENDING"},
            {"id": "code-gate", "status": "PENDING"},
            {"id": "verification-gate", "status": "PENDING"},
            {"id": "release-gate", "status": "PENDING"},
        ],
        "updated_at": "2026-08-07T11:01:00Z",
    }


def assert_outcome(result, outcome):
    if result["outcome"] != outcome:
        raise AssertionError(f"expected {outcome}, got {result}")


def main():
    profile = load_profile("standard-feature")

    dispatch = compute_state(base_manifest(), profile)
    assert_outcome(dispatch, "DISPATCH")
    if [action["stage"] for action in dispatch["actions"]] != ["design"]:
        raise AssertionError(f"unexpected dispatch actions: {dispatch}")
    if dispatch["actions"][0]["role"] != "architect":
        raise AssertionError(f"unexpected dispatch role: {dispatch}")

    blocked_manifest = base_manifest()
    blocked_manifest["workflow"]["status"] = "BLOCKED"
    blocked_manifest["workflow"]["stages"][2]["status"] = "BLOCKED"
    blocked = compute_state(blocked_manifest, profile)
    assert_outcome(blocked, "BLOCKED")

    complete_manifest = base_manifest()
    complete_manifest["workflow"]["status"] = "DONE"
    for stage in complete_manifest["workflow"]["stages"]:
        stage["status"] = "DONE"
    for gate in complete_manifest["gates"]:
        gate["status"] = "PASS"
    complete = compute_state(complete_manifest, profile)
    assert_outcome(complete, "COMPLETE")

    invalid_manifest = base_manifest()
    invalid_manifest["workflow"]["stages"] = invalid_manifest["workflow"]["stages"][:-1]
    invalid = compute_state(invalid_manifest, profile)
    assert_outcome(invalid, "INVALID")
    if "manifest missing profile stages" not in "\n".join(invalid["errors"]):
        raise AssertionError(f"missing mismatch detail: {invalid}")

    parallel_profile = {
        "id": "parallel-test",
        "version": "0.1.0",
        "risk_profile": "medium",
        "stages": [
            {"id": "root", "role": "orchestrator"},
            {"id": "backend", "role": "developer", "depends_on": ["root"]},
            {"id": "frontend", "role": "developer", "depends_on": ["root"]},
        ],
    }
    parallel_manifest = {
        "protocol_version": "0.1.0",
        "feature": {"id": "F-P", "title": "Parallel", "risk": "medium"},
        "workflow": {
            "profile": "parallel-test",
            "status": "ACTIVE",
            "current_stage": "backend",
            "stages": [
                {"id": "root", "status": "DONE"},
                {"id": "backend", "status": "READY"},
                {"id": "frontend", "status": "READY"},
            ],
        },
        "updated_at": "2026-08-07T11:01:00Z",
    }
    parallel = compute_state(parallel_manifest, parallel_profile)
    assert_outcome(parallel, "DISPATCH")
    if {action["stage"] for action in parallel["actions"]} != {"backend", "frontend"}:
        raise AssertionError(f"parallel dispatch failed: {parallel}")

    print("Orchestrator state engine scenarios passed")


if __name__ == "__main__":
    main()
