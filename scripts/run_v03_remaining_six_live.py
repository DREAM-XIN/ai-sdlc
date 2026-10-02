#!/usr/bin/env python3
"""Official bootstrap for the six remaining trusted-main live rows.

The shared dispatch/recovery helper intentionally owns only its original three
scenario idempotency keys.  This bootstrap extends that process-local table with
the closed remaining-six ids before executing the dedicated runner.  No support
module or repository-global Python import behavior is modified.
"""
from __future__ import annotations

import runpy

import v03_dispatch_recovery_live_runner as shared


REMAINING_SIX_IDEMPOTENCY = {
    "cancel-before-persist-linearization": "v03-release-fi-cancel-before-persist-linearization-r4",
    "persist-linearized-before-cancel": "v03-release-fi-persist-linearized-before-cancel-r3",
    "duplicate-callback": "v03-release-fi-duplicate-callback-r3",
    "out-of-order-callback": "v03-release-fi-out-of-order-callback-r3",
    "duplicate-worker-completion": "v03-release-fi-duplicate-worker-completion-r3",
    "stale-candidate-result": "v03-release-fi-stale-candidate-result-r2",
}


def main() -> None:
    overlap = set(shared.IDEMPOTENCY) & set(REMAINING_SIX_IDEMPOTENCY)
    if overlap:
        raise RuntimeError("remaining-six idempotency ids overlap dispatch/recovery trio")
    shared.IDEMPOTENCY.update(REMAINING_SIX_IDEMPOTENCY)
    runpy.run_module("v03_remaining_six_live_runner", run_name="__main__")


if __name__ == "__main__":
    main()
