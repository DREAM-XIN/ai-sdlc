#!/usr/bin/env python3
"""Render PENDING release-schema evidence for deterministic concurrent resume orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path

from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from validate_v03_concurrent_resume_orchestration import main as validate_orchestration
from validate_v03_real_runtime_lost_ack_orchestration import CANDIDATE, FEATURE, REF, REPOSITORY

OUTPUT = Path("evidence/v03-concurrent-resume-deterministic.json")


def main() -> None:
    row = validate_orchestration()
    record = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": "concurrent-resume",
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
        "deterministic_orchestration": {
            "preselected_runner_count": 2,
            "modeled_external_post_count": row["external_post_count"],
            "reservation_count": row["reservation_count"],
            "claim_count": row["claim_count"],
            "authorization_count": row["authorization_count"],
            "launch_receipt_count": row["launch_receipt_count"],
            "final_operation_status": row["final_status"],
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
            "race two real trusted resume runners against the protected shared Operator state ref on main",
            "prove the losing/stale runner converges without a second external run or lifecycle effect",
            "capture exact protected Store CAS/ref evidence for the race",
            "satisfy every trusted-main production prerequisite before release PASS",
        ],
    }
    validate_release_evidence(record)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
