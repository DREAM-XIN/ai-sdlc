#!/usr/bin/env python3
"""Render PENDING release-schema evidence for deterministic UNKNOWN takeover orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path

from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from validate_v03_real_runtime_lost_ack_orchestration import CANDIDATE, FEATURE, REF, REPOSITORY
from validate_v03_unknown_takeover_orchestration import main as validate_orchestration

OUTPUT = Path("evidence/v03-unknown-takeover-deterministic.json")


def main() -> None:
    row = validate_orchestration()
    record = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": "unknown-takeover",
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
            "semantic_effect_key": row["semantic_effect_key"],
            "external_dispatch_key": row["external_dispatch_key"],
        },
        "deterministic_orchestration": {
            "g0_external_launch_attempt_count": row["external_launch_attempt_count"],
            "g0_lookup_state": "UNKNOWN",
            "g1_external_access_count": row["g1_external_access_count"],
            "takeover_preserved_same_external_key": True,
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
            "induce UNKNOWN from the real trusted external runtime after durable launch authorization on main",
            "prove protected Store takeover preserves the same unresolved external key with zero second real run",
            "exercise only the accepted trusted Effect Resolution authority to resolve or remain blocked",
            "satisfy every trusted-main production prerequisite before release PASS",
        ],
    }
    validate_release_evidence(record)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
