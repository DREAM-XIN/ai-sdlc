#!/usr/bin/env python3
"""Validate machine-readable Responses implementation-readiness semantics."""
from __future__ import annotations

import os
from unittest.mock import patch

import render_operator_openai_responses_readiness as readiness


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_runtime_error(callable_, message: str) -> None:
    try:
        callable_()
    except RuntimeError:
        return
    raise AssertionError(message)


def _env(**overrides):
    values = {
        "TRUSTED_MAIN_SYNCHRONIZED": "true",
        "TRUSTED_MAIN_SHA": "c" * 40,
        "WU1_PROTOCOL_REGISTRY_EXECUTED_AND_PASSED": "true",
        "WU2_COLLECTOR_EXECUTED_AND_PASSED": "true",
        "WU3_DURABLE_JOURNAL_RECOVERY_EXECUTED_AND_PASSED": "true",
        "WU4_PRODUCTION_BINDING_EXECUTED_AND_PASSED": "true",
        "WU5_HOST_EXECUTED_AND_PASSED": "true",
        "WU6_LANE_A_CONFORMANCE_EXECUTED_AND_PASSED": "true",
        "LANE_A_FAULT_MATRIX_EXECUTED_AND_PASSED": "true",
        "PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED": "false",
        "LANE_B_EVENT_SEAM_EXECUTED_AND_PASSED": "true",
        "LANE_B_EXECUTED_AND_PASSED": "false",
        "WU8_EXECUTED_AND_PASSED": "false",
        "PUBLIC_RUNTIME_VALIDATION_EXECUTED_AND_PASSED": "true",
        "AUTHORITATIVE_REPOSITORY_VALIDATION_EXECUTED_AND_PASSED": "true",
        "GITHUB_REPOSITORY": "DREAM-XIN/ai-sdlc",
        "GITHUB_RUN_ID": "123",
        "GITHUB_SHA": "a" * 40,
        "RESPONSES_PR_HEAD_SHA": "b" * 40,
        "GITHUB_REF": "refs/pull/233/merge",
    }
    values.update(overrides)
    return patch.dict(os.environ, values, clear=False)


def _hard(*, full: bool, stale: bool):
    return {
        "full_vertical_production_factory": full,
        "stale_recorded_callback_convergence": stale,
    }


def _roots(*, final: bool):
    roots = set(readiness.public_runtime.BASE_RUNTIME_ROOTS)
    if final:
        roots.add(readiness.public_runtime.FINAL_VERTICAL_ROOT)
    return roots


def _record(*, full: bool, stale: bool, final_root: bool, persist_baseline: bool, **env):
    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=full, stale=stale)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=persist_baseline),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=final_root)),
        _env(**env),
    ):
        return readiness.build_record()


def validate_blocked_record_is_non_promoting() -> None:
    record = _record(full=False, stale=False, final_root=False, persist_baseline=False)
    require(record["schema_version"].endswith("/v2"), "full-WU readiness schema was not versioned")
    require(record["candidate_baseline"]["pr_head_contains_current_trusted_main"] is True, "synchronized baseline was not recorded")
    require(record["candidate_baseline"]["trusted_main_sha"] == "c" * 40, "trusted main SHA was not recorded")
    require(record["mechanical_completion_candidate"] is False, "blocked baseline became mechanical completion candidate")
    require(record["wu1"]["protocol_registry_and_strict_schema_executed_and_passed"] is True, "WU1 proof missing")
    require(record["wu2"]["collector_streaming_and_terminal_normalization_executed_and_passed"] is True, "WU2 proof missing")
    require(record["wu3"]["durable_journal_replay_and_crash_recovery_executed_and_passed"] is True, "WU3 proof missing")
    require(record["wu4"]["fail_closed_production_binding_contract_executed_and_passed"] is True, "WU4 proof missing")
    require(record["wu5"]["official_responses_host_boundary_executed_and_passed"] is True, "WU5 proof missing")
    require(record["wu6"]["lane_a_conformance_executed_and_passed"] is True, "WU6 conformance proof missing")
    require(record["wu6"]["persist_deterministic_rejection_classification_executed_and_passed"] is False, "blocked WU6 classification became PASS")
    require(record["wu7"]["exact_feature_event_seam_executed_and_passed"] is True, "WU7 Event seam proof missing")
    require(record["wu7"]["lane_b_candidate_baseline_synchronized"] is True, "Lane B lost synchronized-baseline evidence")
    require(record["wu7"]["lane_b_wu8_execution_proof_present"] is False, "blocked Lane B invented WU8 proof")
    require(record["wu7"]["lane_b_executed_and_passed"] is False, "blocked Lane B became PASS")
    require(record["wu8"]["candidate_baseline_synchronized"] is True, "WU8 lost synchronized-baseline evidence")
    require(record["wu8"]["wu8_executed_and_passed"] is False, "blocked WU8 became PASS")
    require(record["wu9"]["public_runtime_validation_executed_and_passed"] is True, "WU9 Public Runtime execution proof missing")
    require(record["wu9"]["authoritative_repository_validation_executed_and_passed"] is True, "WU9 repository validation execution proof missing")
    require(record["wu9"]["final_vertical_public_runtime_included"] is False, "missing final root became packaged")
    require(not any(record["claims"].values()), "blocked readiness emitted an authority/completion claim")


def validate_illegal_pass_claims_are_rejected() -> None:
    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=False, stale=False)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=False),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=False)),
        _env(PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true"),
    ):
        expect_runtime_error(readiness.build_record, "WU6 classification PASS was accepted without classified runtime baseline")

    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=True, stale=True)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=True),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=True)),
        _env(
            PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true",
            WU8_EXECUTED_AND_PASSED="true",
            LANE_B_EVENT_SEAM_EXECUTED_AND_PASSED="false",
            LANE_B_EXECUTED_AND_PASSED="true",
        ),
    ):
        expect_runtime_error(readiness.build_record, "Lane B PASS was accepted without exact Event seam proof")

    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=False, stale=False)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=False),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=False)),
        _env(LANE_B_EXECUTED_AND_PASSED="true"),
    ):
        expect_runtime_error(readiness.build_record, "Lane B PASS was accepted with missing hard dependencies")

    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=True, stale=True)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=True),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=True)),
        _env(PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true", LANE_B_EXECUTED_AND_PASSED="true"),
    ):
        expect_runtime_error(readiness.build_record, "Lane B PASS was accepted before WU8 execution proof")

    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=False, stale=False)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=False),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=False)),
        _env(WU8_EXECUTED_AND_PASSED="true"),
    ):
        expect_runtime_error(readiness.build_record, "WU8 PASS was accepted without stale-callback convergence")

    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=True, stale=True)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=True),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=True)),
        _env(TRUSTED_MAIN_SYNCHRONIZED="false", WU8_EXECUTED_AND_PASSED="true"),
    ):
        expect_runtime_error(readiness.build_record, "WU8 PASS was accepted before PR head synchronized current main")


def validate_unsynchronized_candidate_cannot_complete() -> None:
    record = _record(
        full=True,
        stale=True,
        final_root=True,
        persist_baseline=True,
        TRUSTED_MAIN_SYNCHRONIZED="false",
        PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true",
    )
    require(record["candidate_baseline"]["pr_head_contains_current_trusted_main"] is False, "unsynchronized candidate was recorded as synchronized")
    require(record["mechanical_completion_candidate"] is False, "unsynchronized PR head became mechanical completion candidate")


def validate_every_work_unit_is_required_for_candidate() -> None:
    required_envs = (
        "WU1_PROTOCOL_REGISTRY_EXECUTED_AND_PASSED",
        "WU2_COLLECTOR_EXECUTED_AND_PASSED",
        "WU3_DURABLE_JOURNAL_RECOVERY_EXECUTED_AND_PASSED",
        "WU4_PRODUCTION_BINDING_EXECUTED_AND_PASSED",
        "WU5_HOST_EXECUTED_AND_PASSED",
        "WU6_LANE_A_CONFORMANCE_EXECUTED_AND_PASSED",
        "LANE_A_FAULT_MATRIX_EXECUTED_AND_PASSED",
        "PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED",
        "LANE_B_EVENT_SEAM_EXECUTED_AND_PASSED",
        "WU8_EXECUTED_AND_PASSED",
        "LANE_B_EXECUTED_AND_PASSED",
        "PUBLIC_RUNTIME_VALIDATION_EXECUTED_AND_PASSED",
        "AUTHORITATIVE_REPOSITORY_VALIDATION_EXECUTED_AND_PASSED",
    )
    for missing in required_envs:
        overrides = {
            "PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED": "true",
            "WU8_EXECUTED_AND_PASSED": "true",
            "LANE_B_EXECUTED_AND_PASSED": "true",
            missing: "false",
        }
        with (
            patch.object(readiness, "production_dependency_status", return_value=_hard(full=True, stale=True)),
            patch.object(readiness, "_persist_classification_baseline_present", return_value=True),
            patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=True)),
            _env(**overrides),
        ):
            if missing in {"LANE_B_EVENT_SEAM_EXECUTED_AND_PASSED", "WU8_EXECUTED_AND_PASSED"}:
                expect_runtime_error(readiness.build_record, f"Lane B remained recordable without mandatory proof: {missing}")
                continue
            record = readiness.build_record()
        require(record["mechanical_completion_candidate"] is False, f"mechanical candidate ignored required proof: {missing}")


def validate_repository_validation_is_required() -> None:
    record = _record(
        full=True,
        stale=True,
        final_root=True,
        persist_baseline=True,
        PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true",
        WU8_EXECUTED_AND_PASSED="true",
        LANE_B_EXECUTED_AND_PASSED="true",
        AUTHORITATIVE_REPOSITORY_VALIDATION_EXECUTED_AND_PASSED="false",
    )
    require(record["wu9"]["authoritative_repository_validation_executed_and_passed"] is False, "missing repository validation became PASS")
    require(record["mechanical_completion_candidate"] is False, "candidate completed without python scripts/validate.py proof")


def validate_public_runtime_readiness_cannot_drift() -> None:
    with (
        patch.object(readiness, "production_dependency_status", return_value=_hard(full=True, stale=True)),
        patch.object(readiness, "_persist_classification_baseline_present", return_value=True),
        patch.object(readiness.public_runtime, "runtime_roots", return_value=_roots(final=False)),
        _env(
            PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true",
            WU8_EXECUTED_AND_PASSED="true",
            LANE_B_EXECUTED_AND_PASSED="true",
        ),
    ):
        expect_runtime_error(readiness.build_record, "factory readiness disagreed with Public Runtime root but evidence was emitted")


def validate_future_mechanical_candidate_still_claims_no_authority() -> None:
    record = _record(
        full=True,
        stale=True,
        final_root=True,
        persist_baseline=True,
        PERSIST_CLASSIFICATION_EXECUTED_AND_PASSED="true",
        WU8_EXECUTED_AND_PASSED="true",
        LANE_B_EXECUTED_AND_PASSED="true",
    )
    require(record["candidate_baseline"]["pr_head_contains_current_trusted_main"] is True, "future candidate lacks exact main synchronization")
    require(record["mechanical_completion_candidate"] is True, "fully proven mechanical fixture did not become candidate")
    require(record["wu1"]["protocol_registry_and_strict_schema_executed_and_passed"] is True, "WU1 proof missing")
    require(record["wu2"]["collector_streaming_and_terminal_normalization_executed_and_passed"] is True, "WU2 proof missing")
    require(record["wu3"]["durable_journal_replay_and_crash_recovery_executed_and_passed"] is True, "WU3 proof missing")
    require(record["wu4"]["fail_closed_production_binding_contract_executed_and_passed"] is True, "WU4 proof missing")
    require(record["wu5"]["official_responses_host_boundary_executed_and_passed"] is True, "WU5 proof missing")
    require(record["wu6"]["persist_deterministic_rejection_classification_executed_and_passed"] is True, "WU6 classification proof missing")
    require(record["wu7"]["lane_b_candidate_baseline_synchronized"] is True, "Lane B lacks synchronized-main proof")
    require(record["wu7"]["lane_b_wu8_execution_proof_present"] is True, "Lane B lacks WU8 proof")
    require(record["wu7"]["lane_b_executed_and_passed"] is True, "Lane B proof missing")
    require(record["wu8"]["wu8_executed_and_passed"] is True, "WU8 proof missing")
    require(record["wu9"]["public_runtime_validation_executed_and_passed"] is True, "Public Runtime proof missing")
    require(record["wu9"]["authoritative_repository_validation_executed_and_passed"] is True, "repository-wide validation proof missing")
    require(record["wu9"]["final_vertical_public_runtime_included"] is True, "final public runtime proof missing")
    require(not any(record["claims"].values()), "mechanical readiness improperly emitted lifecycle/Supported authority")


def main() -> None:
    validate_blocked_record_is_non_promoting()
    validate_illegal_pass_claims_are_rejected()
    validate_unsynchronized_candidate_cannot_complete()
    validate_every_work_unit_is_required_for_candidate()
    validate_repository_validation_is_required()
    validate_public_runtime_readiness_cannot_drift()
    validate_future_mechanical_candidate_still_claims_no_authority()
    print("OpenAI Responses readiness evidence contract validation passed")
    print("- readiness v2 records exact current-main synchronization plus explicit WU1-WU9 mechanical proof")
    print("- an unsynchronized PR head cannot record WU8/Lane B PASS or become a completion candidate")
    print("- WU9 requires an actually executed python scripts/validate.py repository-wide proof")
    print("- every required work-unit execution signal participates in completion candidacy")
    print("- WU6 classification requires an actually executed strict proof")
    print("- WU8 execution proof is mandatory before Lane B can be recorded PASS")
    print("- final factory readiness must agree with Public Runtime root selection")
    print("- full mechanical readiness still emits no IMPL-DONE/Supported/code-gate/release claim")


if __name__ == "__main__":
    main()
