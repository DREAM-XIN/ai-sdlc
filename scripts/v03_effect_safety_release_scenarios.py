#!/usr/bin/env python3
"""Release-scoped v0.3 effect-safety scenario taxonomy.

The original deterministic matrix predates the explicit duplicate Worker
completion acceptance row in Issue #221. Keep the base matrix stable while the
release contract extends it with that distinct collector-entrypoint scenario.
"""
from __future__ import annotations

from v03_effect_safety_matrix import RELEASE_REQUIRED_SCENARIOS as BASE_RELEASE_REQUIRED_SCENARIOS

DUPLICATE_WORKER_COMPLETION_SCENARIO = "duplicate-worker-completion"
RELEASE_REQUIRED_SCENARIOS = (
    *BASE_RELEASE_REQUIRED_SCENARIOS,
    DUPLICATE_WORKER_COMPLETION_SCENARIO,
)

if len(set(RELEASE_REQUIRED_SCENARIOS)) != len(RELEASE_REQUIRED_SCENARIOS):
    raise RuntimeError("release effect-safety scenario taxonomy contains duplicates")
