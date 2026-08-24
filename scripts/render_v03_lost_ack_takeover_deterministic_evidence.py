#!/usr/bin/env python3
"""Render non-release deterministic evidence for the lost-ACK takeover harness.

The record intentionally uses the real-runtime release-evidence schema in PENDING
mode. It proves the two-process orchestration contract deterministically, while
remaining ineligible for release until the same scenario runs against trusted
main, the protected durable Store, and a real external runtime receipt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from v03_real_runtime_lost_ack_orchestration import derive_lost_ack_dispatch_binding
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    IDEMPOTENCY,
    REF,
    REPOSITORY,
    main as validate_orchestration,
    manifest,
)

OUTPUT = Path("evidence/v03-lost-ack-takeover-deterministic.json")


def build_record() -> dict:
    # Execute the full G0 crash -> fresh G1 same-key adoption validator first.
    # Any invariant failure aborts artifact generation.
    validate_orchestration()

    fixture_manifest = manifest()
    binding = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        manifest=fixture_manifest,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
        idempotency_key=IDEMPOTENCY,
        occurred_at="2026-08-11T00:00:00Z",
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": "lost-ack-crash-takeover",
        "status": "PENDING",
        "release_eligible": False,
        "evidence_kind": "deterministic-orchestration",
        "subject": {
            "repository": REPOSITORY,
            "feature_id": FEATURE,
            "target_ref": REF,
            "feature_revision": int(fixture_manifest["revision"]),
            "candidate_pr_number": 230,
            "candidate_head_sha": CANDIDATE,
            "operation_id": binding.operation_id,
            "operation_generation": 1,
        },
        "effect": {
            "semantic_effect_key": binding.semantic_effect_key,
            "external_dispatch_key": binding.external_dispatch_key,
        },
        "deterministic_orchestration": {
            "phase1": "G0 launch authorization -> one modeled external run -> injected crash before local lookup evidence",
            "phase2": "fresh runtime -> trusted plan_vertical_takeover -> G1 same-key launch path -> lookup-first adoption",
            "modeled_external_post_count": 1,
            "adopted_runtime_receipt_identity": "run-1",
            "final_operation_generation": 1,
            "final_operation_status": "WAITING_EXTERNAL",
            "effect_lineage_required": True,
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
            "execute the same crash/takeover path against the protected durable Operator Store on trusted main",
            "use a real GitHub Actions external runtime receipt and prove exactly one matching external run",
            "prove durable Store generation takeover and exact Feature Persist at-most-once in the real runtime",
            "satisfy every trusted-main production prerequisite before any release PASS claim",
        ],
    }
    return validate_release_evidence(record)


def main() -> None:
    record = build_record()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
