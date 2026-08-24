#!/usr/bin/env python3
"""Render PENDING release-schema records for deterministic callback orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path

from run_v03_callback_orchestration_support import run_callback_support
from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from validate_v03_callback_orchestration import CANDIDATE, FEATURE, REF, REPOSITORY

OUTPUT = Path("evidence/v03-callback-deterministic.json")


def _record(row: dict) -> dict:
    scenario = row["scenario_id"]
    support_status = str(row["support_status"])
    if scenario == "duplicate-callback":
        details = {
            "support_status": support_status,
            "exact_duplicate_deliveries": 2,
            "durable_callback_count": row["durable_callback_count"],
            "durable_validated_count": row["durable_validated_count"],
            "feature_event_translation_count": 0,
            "persist_authority_count": 0,
            "final_operation_status": row["final_status"],
        }
        remaining = [
            "deliver an exact duplicate through the real trusted collector/callback transport on main",
            "prove one durable callback and at-most-one lifecycle/Persist effect against the protected Store",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        generation = 0
    elif scenario == "out-of-order-callback":
        details = {
            "support_status": support_status,
            "takeover_generation": row["operation_generation"],
            "stale_g0_callback_durable_count": row["stale_callback_durable_count"],
            "feature_event_translation_count": 0,
            "persist_authority_count": 0,
            "final_operation_status": row["final_status"],
        }
        remaining = [
            "deliver a late real G0 collector callback after trusted G1 takeover on main",
            "prove the protected Store records zero stale callback/translation/Persist authority",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        generation = row["operation_generation"]
    elif scenario == "stale-candidate-result" and support_status == "PASS":
        details = {
            "support_status": support_status,
            "callback_envelope_recorded_count": row["stale_callback_durable_count"],
            "stale_result_rejected_count": row["stale_result_rejected_count"],
            "old_candidate_head_sha": row["old_candidate_head_sha"],
            "current_candidate_head_sha": row["current_candidate_head_sha"],
            "fresh_candidate_semantic_effect_key": row["fresh_candidate_semantic_effect_key"],
            "fresh_candidate_external_reservation_count": row["fresh_candidate_external_reservation_count"],
            "feature_event_translation_count": 0,
            "persist_authority_count": 0,
            "final_operation_status": row["final_status"],
        }
        remaining = [
            "change a real candidate head after external work launch and deliver the stale real result",
            "prove the protected Store records STALE_REVISION rejection with zero stale translation/Persist",
            "prove fresh candidate work remains exact-bound behind unresolved predecessor lineage",
            "satisfy every trusted-main production prerequisite before release PASS",
        ]
        generation = 0
    elif scenario == "stale-candidate-result" and support_status == "PENDING_RUNTIME_REMEDIATION":
        details = {
            "support_status": support_status,
            "runtime_remediation_issue": row["runtime_remediation_issue"],
            "runtime_remediation_pr": row["runtime_remediation_pr"],
            "callback_envelope_recorded_count": row["durable_callback_count"],
            "stale_result_rejected_count": row["durable_rejection_count"],
            "old_candidate_head_sha": row["old_candidate_head_sha"],
            "current_candidate_head_sha": row["current_candidate_head_sha"],
            "feature_event_translation_count": row["translation_count"],
            "persist_authority_count": row["persist_authority_count"],
            "final_operation_status": row["final_status"],
        }
        remaining = [
            "independently review and land Issue #254 / PR #255 stale-callback durable convergence on trusted main",
            "rerun the coordinator stale-candidate scenario so the durable callback produces exactly one STALE_REVISION rejection and BLOCKED stop",
            "then prove candidate-B work remains an exact Effect Lineage proposal with zero external reservation while candidate-A predecessor is unresolved",
            "satisfy every other trusted-main production prerequisite before release PASS",
        ]
        generation = 0
    else:
        raise AssertionError(f"unexpected callback support row: {scenario}/{support_status}")

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
            "operation_generation": generation,
        },
        "effect": {
            "semantic_effect_key": row["semantic_effect_key"],
            "external_dispatch_key": row["external_dispatch_key"],
        },
        "deterministic_orchestration": details,
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
    support = run_callback_support()
    order = ("duplicate-callback", "out-of-order-callback", "stale-candidate-result")
    records = [_record(support["scenarios"][name]) for name in order]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"support": support, "records": records}, sort_keys=True))


if __name__ == "__main__":
    main()
