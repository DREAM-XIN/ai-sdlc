#!/usr/bin/env python3
"""Render PENDING release-schema records for deterministic launch/cancel orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path

from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from validate_v03_launch_cancel_orchestration import CANDIDATE, FEATURE, REF, REPOSITORY, main as validate_orchestration

OUTPUT = Path("evidence/v03-launch-cancel-deterministic.json")


def _record(row: dict) -> dict:
    scenario = row["scenario_id"]
    if scenario == "cancel-before-launch-authorization":
        remaining = [
            "repeat against the protected durable Operator Store on trusted main",
            "prove zero matching real supported-runtime executions for the exact external key",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        orchestration = {
            "durable_order": "dispatch.claimed -> operation.cancelled; launch authorization rejected",
            "modeled_external_post_count": 0,
            "final_operation_status": "CANCELLED",
        }
    elif scenario == "launch-authorized-before-cancel":
        remaining = [
            "repeat with one real supported-runtime execution on trusted main",
            "prove exactly one exact-key external run and the same receipt after cancellation",
            "prove the completed external effect gains no automatic Feature Persist authority",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        orchestration = {
            "durable_order": "dispatch.launch.authorized -> operation.cancelled -> dispatch.launch.lookup-recorded",
            "modeled_external_post_count": 1,
            "modeled_runtime_receipt_identity": row["runtime_receipt_identity"],
            "final_operation_status": "CANCELLED",
        }
    else:
        raise AssertionError(f"unexpected launch/cancel scenario: {scenario}")

    record = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": scenario,
        "status": "PENDING",
        "release_eligible": False,
        "evidence_kind": "deterministic-orchestration",
        "subject": {
            "repository": REPOSITORY,
            "feature_id": FEATURE,
            "target_ref": REF,
            "feature_revision": 11,
            "candidate_pr_number": 230,
            "candidate_head_sha": CANDIDATE,
            "operation_id": row["operation_id"],
            "operation_generation": 0,
        },
        "effect": {
            "semantic_effect_key": row["semantic_effect_key"],
            "external_dispatch_key": row["external_dispatch_key"],
        },
        "deterministic_orchestration": orchestration,
        "observations": {
            "duplicate_external_effect_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
            "stale_evidence_accepted_count": 0,
            "speculative_retry_under_unknown_count": 0,
        },
        "github_run": {
            "id": int(os.environ.get("GITHUB_RUN_ID", "0") or 0),
            "head_sha": os.environ.get("GITHUB_SHA", ""),
            "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        },
        "remaining_release_proof": remaining,
    }
    return validate_release_evidence(record)


def main() -> None:
    results = validate_orchestration()
    records = [_record(results[name]) for name in (
        "cancel-before-launch-authorization",
        "launch-authorized-before-cancel",
    )]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(records, sort_keys=True))


if __name__ == "__main__":
    main()
