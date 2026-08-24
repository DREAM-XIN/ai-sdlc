#!/usr/bin/env python3
"""Release-scoped deterministic support projection for Issue #221.

The trusted-main base matrix currently has deterministic/orchestration support
for eleven scenarios. Issue #221 also requires duplicate Worker completion,
which needs the production Operation-bound collector from PR #270. Until that
prerequisite is independently accepted and on trusted main, report the missing
scenario explicitly instead of copying unmerged runtime code or claiming 12/12.
"""
from __future__ import annotations

import json

from v03_effect_safety_matrix_full_support import run_full_deterministic_support_matrix
from v03_effect_safety_release_scenarios import (
    DUPLICATE_WORKER_COMPLETION_SCENARIO,
    RELEASE_REQUIRED_SCENARIOS,
)


def run_release_scoped_deterministic_support_matrix() -> dict:
    matrix = run_full_deterministic_support_matrix()
    matrix["release_required_scenarios"] = list(RELEASE_REQUIRED_SCENARIOS)
    missing = [
        scenario
        for scenario in RELEASE_REQUIRED_SCENARIOS
        if scenario not in matrix["scenarios"]
    ]
    if missing != [DUPLICATE_WORKER_COMPLETION_SCENARIO]:
        raise AssertionError(f"unexpected release deterministic-support gap: {missing}")
    if len(matrix["scenarios"]) != len(RELEASE_REQUIRED_SCENARIOS) - 1:
        raise AssertionError("release deterministic-support scenario count drifted")
    matrix["release_scenarios_without_deterministic_support_yet"] = missing
    matrix["deterministic_support_complete"] = False
    matrix["orchestration_pending_runtime_remediation"] = list(missing)
    matrix["release_scenario_prerequisites"] = {
        DUPLICATE_WORKER_COMPLETION_SCENARIO: {
            "issue": 264,
            "pr": 270,
            "requirement": "production Operation-bound collector must be on trusted main before duplicate Worker completion orchestration",
        }
    }
    matrix["release_eligible"] = False
    matrix["evidence_kind"] = "deterministic-support"
    return matrix


if __name__ == "__main__":
    print(json.dumps(run_release_scoped_deterministic_support_matrix(), indent=2, sort_keys=True))
