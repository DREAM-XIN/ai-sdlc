#!/usr/bin/env python3
"""WU8 hard prerequisite validation for the OpenAI Responses adapter.

`--probe` reports only whether the reviewed stale-recorded-callback semantic
shape exists on the implementation baseline. Normal execution is forbidden
until that prerequisite is real; then this harness runs the baseline remediation
and Effect-Lineage adversarial validators without copying their authority logic.
"""
from __future__ import annotations

import argparse
import importlib
import json

from operator_openai_responses_production import stale_recorded_callback_convergence_available


def probe() -> dict[str, object]:
    ready = stale_recorded_callback_convergence_available()
    return {
        "status": "READY" if ready else "BLOCKED",
        "evidence_kind": "stale-recorded-callback-readiness-only",
        "wu8_passed": False,
        "stale_recorded_callback_convergence": ready,
    }


def _required_callable(module_name: str, function_name: str):
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            f"reviewed WU8 validator is absent from implementation baseline: {module_name}"
        ) from exc
    value = getattr(module, function_name, None)
    if not callable(value):
        raise RuntimeError(
            f"reviewed WU8 validator function is absent: {module_name}.{function_name}"
        )
    return value


def run_wu8() -> dict[str, object]:
    if not stale_recorded_callback_convergence_available():
        raise RuntimeError("stale-recorded-callback convergence is not on the implementation baseline")

    # Consume the reviewed baseline remediation validator, not a local copy of
    # callback recovery authority. WU8 must prove both the normal stale path and
    # the exact crash window where worker.result.rejected is durable but the
    # mapped stable stop was not yet committed.
    stale = _required_callable(
        "validate_operator_stale_callback_reconciliation",
        "validate_stale_candidate_converges",
    )
    crash_windows = _required_callable(
        "validate_operator_stale_callback_reconciliation",
        "validate_rejection_crash_windows",
    )
    transient = _required_callable(
        "validate_operator_stale_callback_reconciliation",
        "validate_transient_read_is_not_reclassified",
    )
    lineage_successor = _required_callable(
        "validate_operator_stale_callback_reconciliation",
        "validate_lineage_successor_stays_fenced",
    )

    stale()
    crash_windows()
    transient()
    lineage_successor()

    # Keep one independent frozen Effect-Lineage adversarial check as a second
    # fence against a future remediation validator becoming narrower than the
    # accepted v0.3 lineage contract.
    lineage = _required_callable(
        "validate_operator_effect_lineage_v2",
        "validate_candidate_block_and_safe_never_authorized_resolution",
    )
    lineage()

    return {
        "status": "PASS",
        "evidence_kind": "stale-recorded-callback-baseline-validation",
        "wu8_passed": True,
        "assertions": {
            "stale_recorded_callback_durably_rejected_and_blocked": True,
            "durable_rejection_crash_window_repairs_mapped_stable_stop": True,
            "restart_reprocess_is_store_inert": True,
            "rejected_callback_has_zero_feature_translation_or_persist": True,
            "later_candidate_remains_lineage_blocked_while_predecessor_unresolved": True,
            "transient_feature_read_failure_remains_retryable_not_reclassified": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(), indent=2, sort_keys=True))
        return

    if not stale_recorded_callback_convergence_available():
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "evidence_kind": "stale-recorded-callback-baseline-validation",
                    "wu8_passed": False,
                    "stale_recorded_callback_convergence": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(2)

    result = run_wu8()
    print("OpenAI Responses WU8 stale-callback prerequisite validation passed")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
