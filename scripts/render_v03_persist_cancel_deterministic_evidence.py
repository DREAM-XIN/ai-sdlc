#!/usr/bin/env python3
"""Render PENDING release-schema records for deterministic Persist/cancel orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path

from operator_store_model import digest_json
from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from validate_v03_persist_cancel_orchestration import CANDIDATE, FEATURE, REF, REPOSITORY, main as validate_orchestration

OUTPUT = Path("evidence/v03-persist-cancel-deterministic.json")


def _record(row: dict) -> dict:
    scenario = row["scenario_id"]
    event_id = str(row["feature_event_id"])
    identity = digest_json({"scenario": scenario, "feature_event_id": event_id})
    if scenario == "cancel-before-persist-linearization":
        remaining = [
            "repeat against the protected durable Operator Store and trusted Feature Event gateway on main",
            "prove zero real Feature writes when cancellation wins before Persist linearization",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        orchestration = {
            "durable_order": "persist.requested -> operation.cancelled; Persist linearization rejected",
            "external_feature_write_count": 0,
            "final_operation_status": "CANCELLED",
        }
    elif scenario == "persist-linearized-before-cancel":
        remaining = [
            "repeat against the real trusted Feature Event gateway and protected Store on main",
            "prove exactly one external Feature Event write for the exact pre-cancel linearized event",
            "prove post-cancel exact confirmation does not authorize later lifecycle progression",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        orchestration = {
            "durable_order": "persist.requested -> persist.linearized -> operation.cancelled -> persist.confirmed",
            "external_feature_write_count": 1,
            "modeled_persist_receipt_id": row["persist_receipt_id"],
            "result_revision": row["result_revision"],
            "final_operation_status": "CANCELLED",
        }
    else:
        raise AssertionError(f"unexpected Persist/cancel scenario: {scenario}")

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
            "semantic_effect_key": identity,
            "external_dispatch_key": f"feature-persist:{event_id}",
            "effect_kind": "feature-persist",
            "feature_event_id": event_id,
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
        "cancel-before-persist-linearization",
        "persist-linearized-before-cancel",
    )]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(records, sort_keys=True))


if __name__ == "__main__":
    main()
