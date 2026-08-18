#!/usr/bin/env python3
"""Render machine-readable implementation readiness without claiming completion."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
from typing import Any

import build_public_operator_runtime as public_runtime
from operator_openai_responses_production import production_dependency_status

SCHEMA_VERSION = "ai-sdlc.openai-responses-implementation-readiness/v2"


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


def _persist_classification_baseline_present() -> bool:
    try:
        runtime = importlib.import_module("operator_vertical_reconcile_classified")
    except ImportError:
        return False
    return isinstance(
        getattr(runtime, "FailureClassifyingTrustedRecoveringVerticalExecutor", None),
        type,
    )


def build_record() -> dict[str, Any]:
    hard = production_dependency_status()
    if set(hard) != {
        "full_vertical_production_factory",
        "stale_recorded_callback_convergence",
    }:
        raise RuntimeError("Responses hard-dependency readiness key set drifted")
    if any(type(value) is not bool for value in hard.values()):
        raise RuntimeError("Responses hard-dependency readiness is not boolean")

    trusted_main_synchronized = _bool_env("TRUSTED_MAIN_SYNCHRONIZED")
    trusted_main_sha = os.environ.get("TRUSTED_MAIN_SHA", "")
    wu1_protocol_passed = _bool_env("WU1_PROTOCOL_REGISTRY_EXECUTED_AND_PASSED")
    wu2_collector_passed = _bool_env("WU2_COLLECTOR_EXECUTED_AND_PASSED")
    wu3_journal_recovery_passed = _bool_env("WU3_DURABLE_JOURNAL_RECOVERY_EXECUTED_AND_PASSED")
    wu4_production_binding_passed = _bool_env("WU4_PRODUCTION_BINDING_EXECUTED_AND_PASSED")
    wu5_host_passed = _bool_env("WU5_HOST_EXECUTED_AND_PASSED")
    lane_a_conformance_passed = _bool_env("WU6_LANE_A_CONFORMANCE_EXECUTED_AND_PASSED")
    lane_a_fault_passed = _bool_env("LANE_A_FAULT_MATRIX_EXECUTED_AND_PASSED")
    persist_classification_passed = _bool_env("PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED")
    event_seam_passed = _bool_env("LANE_B_EVENT_SEAM_EXECUTED_AND_PASSED")
    lane_b_passed = _bool_env("LANE_B_EXECUTED_AND_PASSED")
    wu8_passed = _bool_env("WU8_EXECUTED_AND_PASSED")
    public_runtime_passed = _bool_env("PUBLIC_RUNTIME_VALIDATION_EXECUTED_AND_PASSED")
    repository_validation_passed = _bool_env(
        "AUTHORITATIVE_REPOSITORY_VALIDATION_EXECUTED_AND_PASSED"
    )
    persist_classification = _persist_classification_baseline_present()
    final_public_root = public_runtime.FINAL_VERTICAL_ROOT in public_runtime.runtime_roots()

    if final_public_root != hard["full_vertical_production_factory"]:
        raise RuntimeError(
            "full-Vertical production readiness disagrees with Public Runtime root selection"
        )
    if persist_classification_passed and not persist_classification:
        raise RuntimeError("WU6 Persist classification PASS recorded without classified runtime baseline")
    if wu8_passed and hard["stale_recorded_callback_convergence"] is not True:
        raise RuntimeError("WU8 cannot be recorded PASS before stale-callback convergence exists")
    if wu8_passed and not trusted_main_synchronized:
        raise RuntimeError("WU8 cannot be recorded PASS before the PR head contains current trusted main")
    if lane_b_passed and not event_seam_passed:
        raise RuntimeError("Lane B cannot be recorded PASS without exact Feature Event seam proof")
    if lane_b_passed and not all(hard.values()):
        raise RuntimeError("Lane B cannot be recorded PASS while hard dependencies are incomplete")
    if lane_b_passed and not trusted_main_synchronized:
        raise RuntimeError("Lane B cannot be recorded PASS before the PR head contains current trusted main")
    if lane_b_passed and not wu8_passed:
        raise RuntimeError("Lane B cannot be recorded PASS before WU8 execution proof")

    mechanical_completion_candidate = all(
        (
            trusted_main_synchronized,
            wu1_protocol_passed,
            wu2_collector_passed,
            wu3_journal_recovery_passed,
            wu4_production_binding_passed,
            wu5_host_passed,
            lane_a_conformance_passed,
            lane_a_fault_passed,
            persist_classification_passed,
            event_seam_passed,
            wu8_passed,
            lane_b_passed,
            public_runtime_passed,
            repository_validation_passed,
            final_public_root,
            all(hard.values()),
        )
    )

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_url = f"{server_url}/{repository}/actions/runs/{run_id}" if repository and run_id else ""

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": "implementation-readiness",
        "feature_id": "F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001",
        "adapter_id": "ai-sdlc.openai.responses",
        "github": {
            "repository": repository,
            "run_id": run_id,
            "run_url": run_url,
            "checkout_sha": os.environ.get("GITHUB_SHA", ""),
            "pr_head_sha": os.environ.get("RESPONSES_PR_HEAD_SHA", ""),
            "ref": os.environ.get("GITHUB_REF", ""),
        },
        "candidate_baseline": {
            "trusted_main_sha": trusted_main_sha,
            "pr_head_contains_current_trusted_main": trusted_main_synchronized,
        },
        "hard_dependencies": hard,
        "wu1": {
            "protocol_registry_and_strict_schema_executed_and_passed": wu1_protocol_passed,
        },
        "wu2": {
            "collector_streaming_and_terminal_normalization_executed_and_passed": wu2_collector_passed,
        },
        "wu3": {
            "durable_journal_replay_and_crash_recovery_executed_and_passed": wu3_journal_recovery_passed,
        },
        "wu4": {
            "fail_closed_production_binding_contract_executed_and_passed": wu4_production_binding_passed,
        },
        "wu5": {
            "official_responses_host_boundary_executed_and_passed": wu5_host_passed,
        },
        "wu6": {
            "lane_a_conformance_executed_and_passed": lane_a_conformance_passed,
            "lane_a_fault_matrix_executed_and_passed": lane_a_fault_passed,
            "persist_deterministic_rejection_classification_baseline_present": persist_classification,
            "persist_deterministic_rejection_classification_executed_and_passed": persist_classification_passed,
        },
        "wu7": {
            "exact_feature_event_seam_executed_and_passed": event_seam_passed,
            "lane_b_hard_dependencies_ready": all(hard.values()),
            "lane_b_candidate_baseline_synchronized": trusted_main_synchronized,
            "lane_b_wu8_execution_proof_present": wu8_passed,
            "lane_b_executed_and_passed": lane_b_passed,
        },
        "wu8": {
            "stale_callback_ready": hard["stale_recorded_callback_convergence"],
            "candidate_baseline_synchronized": trusted_main_synchronized,
            "wu8_executed_and_passed": wu8_passed,
        },
        "wu9": {
            "public_runtime_validation_executed_and_passed": public_runtime_passed,
            "authoritative_repository_validation_executed_and_passed": repository_validation_passed,
            "final_vertical_public_runtime_included": final_public_root,
        },
        "mechanical_completion_candidate": mechanical_completion_candidate,
        "claims": {
            "lane_a_is_supported_evidence": False,
            "implementation_done_claimed": False,
            "supported_status_claimed": False,
            "code_gate_pass_claimed": False,
            "release_ready_claimed": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = build_record()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
