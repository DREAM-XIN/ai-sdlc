#!/usr/bin/env python3
"""Deterministic validation for the independent v0.3 real-dogfood fixture pool."""
from __future__ import annotations

from pathlib import Path
import tempfile

import yaml

from v03_dogfood_fixture_pool import (
    SLOTS,
    TASK_ARTIFACT_ID,
    activation_event,
    build_active_manifest,
    build_bootstrap_manifest,
    inventory_document,
    materialize_activation,
    materialize_bootstrap,
    require_slot,
    task_text,
    validate_active,
    validate_inventory,
    verify_active_files,
)

REPOSITORY = "dream-xin/ai-sdlc"
HEAD = "1" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main() -> None:
    validate_inventory()
    inventory = inventory_document()
    require(inventory["release_eligible"] is False, "fixture inventory claimed release evidence")
    require(len(inventory["slots"]) == 3, "fixture inventory is not exactly three slots")

    seen_paths: set[str] = set()
    for slot in SLOTS:
        require(require_slot(slot.scenario) == slot, "scenario lookup changed slot identity")
        bootstrap = build_bootstrap_manifest(slot)
        stages = {row["id"]: row["status"] for row in bootstrap["workflow"]["stages"]}
        require(stages == {
            "implementation": "READY",
            "code-review": "TODO",
            "verification": "TODO",
            "acceptance": "TODO",
        }, "bootstrap stages drifted")
        active = build_active_manifest(slot, repository=REPOSITORY)
        active_stages = {row["id"]: row["status"] for row in active["workflow"]["stages"]}
        require(active["revision"] == 1, "active fixture revision is not one")
        require(active_stages == {
            "implementation": "WORKING",
            "code-review": "TODO",
            "verification": "TODO",
            "acceptance": "TODO",
        }, "active stages drifted")
        require(active["applied_events"] == [slot.event_id], "activation Event identity drifted")
        require(active["artifacts"] == [{
            "id": TASK_ARTIFACT_ID,
            "type": "dogfood-task",
            "uri": slot.task_path,
            "status": "draft",
        }], "scenario task is not registered in authoritative Feature truth")
        validate_active(slot, active, repository=REPOSITORY, candidate_head=HEAD)
        changes = activation_event(slot)["changes"]
        require(len(changes) == 2, "activation Event authority surface drifted")
        require(changes[0] == {
            "kind": "artifact-record",
            "record": {
                "id": TASK_ARTIFACT_ID,
                "type": "dogfood-task",
                "uri": slot.task_path,
                "status": "draft",
            },
        }, "activation Event scenario task authority drifted")
        require(changes[1] == {"kind": "stage", "id": "implementation", "status": "WORKING"}, "activation Event stage authority drifted")

        text = task_text(slot)
        require(slot.feature_id in text and slot.target_ref in text, "dogfood task lost its fixed slot identity")
        require(TASK_ARTIFACT_ID in text, "dogfood task lost authoritative artifact identity")
        require("verification/v0.3-" not in text, "dogfood task reused a #221 verification ref")
        require("F-OPERATOR-V03-FI-" not in text, "dogfood task reused a #221 scenario Feature")
        require("F-OPERATOR-V03-REAL-RUNTIME-FI-0001" not in text, "dogfood task reused the #221 fixed Feature")
        if slot.scenario == "review_remediation":
            require("initial-needs-remediation" in text and "dogfood_review_state: remediated" in text, "remediation fixture lost deterministic review transition")
        if slot.scenario == "session_recovery":
            require("PENDING_USER" in text and "NEEDS_USER" in text, "session recovery fixture lost explicit user-input stop")

        slot_paths = {slot.manifest_path, slot.task_path, slot.event_path}
        require(not seen_paths.intersection(slot_paths), "dogfood fixture path reused across scenarios")
        seen_paths.update(slot_paths)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_paths = materialize_bootstrap(slot, repo_dir=root)
            require(bootstrap_paths == tuple(sorted((slot.manifest_path, slot.task_path))), "bootstrap path set drifted")
            activation_paths = materialize_activation(slot, repo_dir=root, repository=REPOSITORY)
            require(activation_paths == tuple(sorted((slot.manifest_path, slot.event_path))), "activation path set drifted")
            verify_active_files(slot, repo_dir=root, repository=REPOSITORY, candidate_head=HEAD)
            materialized = yaml.safe_load((root / slot.manifest_path).read_text(encoding="utf-8"))
            require(materialized == active, "materialized active Manifest differs from canonical fixture")

    try:
        require_slot("unknown")
    except Exception:
        pass
    else:
        raise AssertionError("unknown scenario unexpectedly resolved a dogfood fixture")

    print("v0.3 independent real-dogfood fixture pool: PASS")


if __name__ == "__main__":
    main()
