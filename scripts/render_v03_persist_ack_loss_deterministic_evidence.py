#!/usr/bin/env python3
"""Render PENDING release-schema evidence for deterministic Persist ACK-loss recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path

from operator_store_model import digest_json
from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from validate_v03_persist_ack_loss_orchestration import main as validate_orchestration
from validate_v03_real_runtime_lost_ack_orchestration import CANDIDATE, FEATURE, REF, REPOSITORY

OUTPUT = Path("evidence/v03-persist-ack-loss-deterministic.json")


def main() -> None:
    row = validate_orchestration()
    event_id = str(row["feature_event_id"])
    identity = digest_json({"scenario": "persist-ack-loss-recovery", "feature_event_id": event_id})
    record = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": "persist-ack-loss-recovery",
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
            "operation_generation": row["operation_generation"],
        },
        "effect": {
            "semantic_effect_key": identity,
            "external_dispatch_key": f"feature-persist:{event_id}",
            "effect_kind": "feature-persist",
            "feature_event_id": event_id,
        },
        "deterministic_orchestration": {
            "phase1_external_feature_write_count": row["external_feature_write_count"],
            "phase1_local_ack_lost": True,
            "fresh_process_exact_lookup_count": row["fresh_lookup_count"],
            "fresh_process_retry_write_count": row["fresh_retry_write_count"],
            "result_revision": row["result_revision"],
            "confirmed_reconciliation_is_idempotent": True,
        },
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
        "remaining_release_proof": [
            "repeat against the real trusted Feature Event gateway and protected Operator Store on main",
            "lose the real external Persist acknowledgement after exactly one authoritative Feature Event write",
            "restart and recover the exact real receipt before any retry, proving one Feature write only",
            "satisfy every trusted-main production prerequisite before release PASS",
        ],
    }
    validate_release_evidence(record)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
