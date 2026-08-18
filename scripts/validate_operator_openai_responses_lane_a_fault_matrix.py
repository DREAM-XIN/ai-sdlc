#!/usr/bin/env python3
"""Strict WU6 Lane-A adversarial-matrix closure.

The underlying fault driver intentionally supports a historical partially-blocked
mode while upstream Persist classification is absent. That compatibility mode is
not sufficient for current Responses implementation-completion evidence. This
wrapper makes the approved #6-#15 matrix fail closed: the expected coverage map
must remain exact and deterministic Persist rejection classification must execute
successfully before the Responses-boundary fault driver may be recorded PASS.

This remains Lane-A deterministic evidence only; it is never Supported-production
or lifecycle authority.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

import validate_operator_openai_responses_faults as faults

EXPECTED_COVERAGE_KEYS = frozenset(
    {
        "6_cancel_before_after_launch_linearization",
        "7_external_lookup_unknown_fail_closed",
        "8_lost_launch_ack_same_key_recovery",
        "9_generation_takeover_stable_external_identity",
        "10_candidate_stale_before_launch",
        "11_effect_lineage_blocked_successor",
        "12_decision_invalid_stale_expired_policy_mismatch",
        "13_notification_duplicate_ack",
        "14_persist_requested_linearized_ack_loss",
        "14_persist_deterministic_rejection_classification",
        "15_secret_error_redaction",
    }
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _coverage_assignment() -> ast.Dict:
    tree = ast.parse(inspect.getsource(faults.main))
    matches: list[ast.Dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "coverage" and isinstance(node.value, ast.Dict):
            matches.append(node.value)
    require(len(matches) == 1, "Lane-A fault coverage map definition drifted")
    return matches[0]


def validate_coverage_contract() -> None:
    coverage = _coverage_assignment()
    keys: set[str] = set()
    dynamic: dict[str, ast.AST] = {}
    for key_node, value_node in zip(coverage.keys, coverage.values):
        require(
            isinstance(key_node, ast.Constant) and isinstance(key_node.value, str),
            "Lane-A fault coverage contains a non-literal key",
        )
        key = key_node.value
        keys.add(key)
        dynamic[key] = value_node

    require(keys == EXPECTED_COVERAGE_KEYS, f"Lane-A fault coverage key set drifted: {sorted(keys)}")
    for key, value in dynamic.items():
        if key == "14_persist_deterministic_rejection_classification":
            require(
                isinstance(value, ast.Name) and value.id == "persist_classification",
                "Persist classification coverage stopped reflecting the executed baseline validator",
            )
        else:
            require(
                isinstance(value, ast.Constant) and value.value is True,
                f"Lane-A fault coverage entry is no longer a direct executed assertion: {key}",
            )


def main() -> None:
    validate_coverage_contract()

    # The legacy fault driver returns success while this dependency is absent.
    # Completion evidence may not. Execute the accepted baseline validator here
    # and fail closed before the fault-matrix step can be marked successful.
    require(
        faults._persist_classification_available() is True,
        "Lane-A fault matrix incomplete: deterministic Persist rejection classification did not execute PASS",
    )

    # Avoid executing the expensive accepted classification suite twice while
    # preserving the underlying Responses-boundary fault scenarios and coverage
    # rendering. The first call above is the actual proof.
    with patch.object(faults, "_persist_classification_available", return_value=True):
        faults.main()

    print("OpenAI Responses strict Lane-A adversarial matrix passed")
    print("- exact approved #6-#15 coverage map is present")
    print("- deterministic Persist rejection classification executed successfully")
    print("- all remaining fault scenarios crossed the real Responses boundary")
    print("- Lane A remains insufficient for Supported status")


if __name__ == "__main__":
    main()
