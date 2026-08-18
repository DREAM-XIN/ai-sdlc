#!/usr/bin/env python3
"""WU6 deterministic Persist-reconciliation classification baseline proof.

The previous stacked PR number/file name is not authority. This harness binds to
the reviewed runtime semantic identity and the accepted deterministic validator
that exercises that exact class. `--probe` is observation-only; default execution
runs the accepted proof and reports PASS only when that proof completes.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json

RUNTIME_MODULE = "operator_vertical_reconcile_classified"
RUNTIME_CLASS = "FailureClassifyingTrustedRecoveringVerticalExecutor"
BASE_EXECUTOR_MODULE = "operator_vertical_reconcile"
BASE_EXECUTOR_CLASS = "TrustedRecoveringVerticalExecutor"
BASELINE_VALIDATOR_MODULE = "validate_operator_persist_reconcile_classification"
REQUIRED_DETERMINISTIC_CODES = frozenset(
    {
        "STALE_REVISION",
        "CONFLICT",
        "UNAUTHORIZED",
        "POLICY_DENIED",
        "INVALID_REQUEST",
        "INTERNAL_FAILURE",
    }
)
REQUIRED_RUNTIME_METHODS = (
    "_persist_blocked",
    "_persist_wait",
    "_classify_persist_exception",
    "_reconcile_persist",
)
REQUIRED_VALIDATOR_CALLABLES = (
    "validate_all_live_deterministic_codes_block",
    "validate_live_transient_and_unclassified_wait",
    "validate_lookup_absent_then_deterministic_submit_failure_blocks",
    "validate_invalid_receipts_block",
    "assert_cancelled_no_mutation",
    "validate_cancelled_exact_confirmation_remains_legal",
    "main",
)


def _runtime_type() -> type | None:
    try:
        module = importlib.import_module(RUNTIME_MODULE)
        base_module = importlib.import_module(BASE_EXECUTOR_MODULE)
    except ImportError:
        return None

    runtime = getattr(module, RUNTIME_CLASS, None)
    base = getattr(base_module, BASE_EXECUTOR_CLASS, None)
    if not isinstance(runtime, type) or not isinstance(base, type):
        return None
    if not issubclass(runtime, base):
        return None

    deterministic_codes = getattr(module, "_DETERMINISTIC_PERSIST_ERRORS", None)
    if not isinstance(deterministic_codes, frozenset):
        return None
    if deterministic_codes != REQUIRED_DETERMINISTIC_CODES:
        return None
    if not all(callable(getattr(runtime, name, None)) for name in REQUIRED_RUNTIME_METHODS):
        return None
    return runtime


def _baseline_validator() -> object | None:
    if _runtime_type() is None:
        return None
    try:
        module = importlib.import_module(BASELINE_VALIDATOR_MODULE)
    except ImportError:
        return None

    if not all(callable(getattr(module, name, None)) for name in REQUIRED_VALIDATOR_CALLABLES):
        return None

    deterministic_codes = getattr(module, "DETERMINISTIC_CODES", None)
    if deterministic_codes is None or frozenset(deterministic_codes) != REQUIRED_DETERMINISTIC_CODES:
        return None

    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return None
    required_semantic_markers = (
        RUNTIME_CLASS,
        "WAITING_EXTERNAL",
        "CANCELLED",
        "EXTERNAL_WAIT",
        "persist_calls == 0",
        "result_revision",
    )
    if not all(marker in source for marker in required_semantic_markers):
        return None
    return module


def probe() -> dict[str, object]:
    runtime_ready = _runtime_type() is not None
    validator = _baseline_validator() if runtime_ready else None
    return {
        "status": "READY" if runtime_ready and validator is not None else "BLOCKED",
        "evidence_kind": "persist-reconcile-classification-readiness-only",
        "wu6_persist_classification_passed": False,
        "classified_runtime_present": runtime_ready,
        "deterministic_error_codes_exact": runtime_ready,
        "qualifying_validator_modules": [BASELINE_VALIDATOR_MODULE] if validator is not None else [],
    }


def run_strict() -> dict[str, object]:
    runtime = _runtime_type()
    validator = _baseline_validator()
    if runtime is None or validator is None:
        raise RuntimeError("reviewed deterministic Persist classification proof is not on this baseline")

    validator.main()
    return {
        "status": "PASS",
        "evidence_kind": "persist-reconcile-classification-baseline-validation",
        "wu6_persist_classification_passed": True,
        "runtime_module": RUNTIME_MODULE,
        "runtime_class": RUNTIME_CLASS,
        "executed_validator_modules": [BASELINE_VALIDATOR_MODULE],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(), indent=2, sort_keys=True))
        return

    state = probe()
    if state["status"] != "READY":
        print(
            json.dumps(
                {
                    **state,
                    "evidence_kind": "persist-reconcile-classification-baseline-validation",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(2)

    result = run_strict()
    print("OpenAI Responses WU6 Persist classification validation passed")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
