#!/usr/bin/env python3
"""Regression coverage for Responses stale-callback dependency gating.

This file does not implement callback recovery. It only proves that the
Responses dependency probe cannot accept the historical half-remediated shape
and that WU8 consumes the reviewed baseline crash/lineage validators once the
real dependency exists.
"""
from __future__ import annotations

from unittest.mock import patch

from operator_openai_responses_production import (
    _durable_rejection_repair_present,
    _stale_callback_rejection_boundary_present,
)
import validate_operator_openai_responses_stale_callback as wu8


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def process_recorded_callback(executor, *, operation_id=None):
    try:
        executor.feature_gateway.read_feature(operation_id=operation_id)
    except VerticalInvariantError as exc:  # noqa: F821 - source-shape fixture only
        return exc
    return None


class HalfRemediatedExecutor:
    def _reconcile_callback(self, operation_id):
        rejected = {"callback-1"}
        for callback_id in ("callback-1",):
            if callback_id in rejected:
                continue
            process_recorded_callback(self, operation_id=operation_id)
        return None


class FullyConvergingExecutor:
    def _stable_stop(self, operation_id, *, status, reason):
        return status, reason

    def _reconcile_callback(self, operation_id):
        rejected = {
            "callback-1": {
                "code": "STALE_REVISION",
                "reason": "durable rejection",
            }
        }
        for callback_id in ("callback-1",):
            rejection = rejected.get(callback_id)
            if rejection is not None:
                code = str(rejection.get("code") or "")
                reason = str(rejection.get("reason") or "durable callback result rejection")
                if code == "NEEDS_USER":
                    self._stable_stop(operation_id, status="NEEDS_USER", reason=reason)
                    return True
                if code in {"BLOCKED", "POLICY_DENIED", "STALE_REVISION"}:
                    self._stable_stop(operation_id, status="BLOCKED", reason=reason)
                    return True
                return None
            process_recorded_callback(self, operation_id=operation_id)
        return None


def validate_probe_rejects_half_remediation() -> None:
    require(
        _stale_callback_rejection_boundary_present(process_recorded_callback),
        "fresh Feature read fixture was not recognized inside VerticalInvariantError boundary",
    )
    require(
        not _durable_rejection_repair_present(HalfRemediatedExecutor._reconcile_callback),
        "historical skip-only durable rejection shape was incorrectly accepted as converged",
    )
    require(
        _durable_rejection_repair_present(FullyConvergingExecutor._reconcile_callback),
        "mapped stable-stop repair shape was not recognized",
    )


def validate_wu8_consumes_full_baseline_validator_surface() -> None:
    requested: list[tuple[str, str]] = []

    def required(module_name: str, function_name: str):
        requested.append((module_name, function_name))
        return lambda: None

    with (
        patch.object(wu8, "stale_recorded_callback_convergence_available", return_value=True),
        patch.object(wu8, "_required_callable", side_effect=required),
    ):
        result = wu8.run_wu8()

    expected = {
        ("validate_operator_stale_callback_reconciliation", "validate_stale_candidate_converges"),
        ("validate_operator_stale_callback_reconciliation", "validate_rejection_crash_windows"),
        ("validate_operator_stale_callback_reconciliation", "validate_transient_read_is_not_reclassified"),
        ("validate_operator_stale_callback_reconciliation", "validate_lineage_successor_stays_fenced"),
        ("validate_operator_effect_lineage_v2", "validate_candidate_block_and_safe_never_authorized_resolution"),
    }
    require(set(requested) == expected, f"WU8 baseline validator surface drifted: {requested}")
    require(result["status"] == "PASS" and result["wu8_passed"] is True, result)
    assertions = result["assertions"]
    require(
        assertions["durable_rejection_crash_window_repairs_mapped_stable_stop"] is True,
        "WU8 omitted crash-window repair assertion",
    )
    require(
        assertions["later_candidate_remains_lineage_blocked_while_predecessor_unresolved"] is True,
        "WU8 omitted lineage-successor fencing assertion",
    )


def main() -> None:
    validate_probe_rejects_half_remediation()
    validate_wu8_consumes_full_baseline_validator_surface()
    print("OpenAI Responses stale dependency gating validation passed")
    print("- catch-only / skip-only historical remediation cannot unlock Supported production")
    print("- durable rejection must repair BLOCKED and NEEDS_USER before callback reprocessing")
    print("- WU8 consumes baseline stale, crash-window, transient and lineage-successor validators")
    print("- no #255 runtime recovery authority is copied into the Responses Feature")


if __name__ == "__main__":
    main()
