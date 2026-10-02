#!/usr/bin/env python3
"""Pure authority-gate validation for the v0.3 dogfood runtime driver."""
from __future__ import annotations

from v03_dogfood_runtime_driver import (
    PREFLIGHT_ONLY,
    RUN,
    VALIDATE_ONLY,
    V03DogfoodRuntimeDriverError,
    require_mode,
)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def rejected(**kwargs):
    try:
        require_mode(**kwargs)
    except V03DogfoodRuntimeDriverError:
        return
    raise AssertionError(f"unexpectedly accepted: {kwargs}")


def main():
    for scenario in ("happy_path", "review_remediation", "session_recovery"):
        expect(
            require_mode(mode=VALIDATE_ONLY, scenario=scenario, event_name="pull_request", ref="refs/pull/348/merge")
            == (VALIDATE_ONLY, scenario),
            "PR validate-only mode",
        )
        expect(
            require_mode(mode=PREFLIGHT_ONLY, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/main")
            == (PREFLIGHT_ONLY, scenario),
            "trusted-main preflight mode",
        )
        expect(
            require_mode(mode=RUN, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/main")
            == (RUN, scenario),
            "trusted-main run mode",
        )
        rejected(mode=RUN, scenario=scenario, event_name="pull_request", ref="refs/heads/main")
        rejected(mode=RUN, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/release/v03-dogfood-real-run-342")
        rejected(mode=VALIDATE_ONLY, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/main")
    rejected(mode=RUN, scenario="unknown", event_name="workflow_dispatch", ref="refs/heads/main")
    rejected(mode="unsafe", scenario="happy_path", event_name="pull_request", ref="refs/pull/348/merge")

    from pathlib import Path
    import yaml
    from gh_aw_provider_registry import load_registry
    from v03_dogfood_execution_bindings import credential_identities
    root = Path(__file__).resolve().parents[1]
    live = yaml.safe_load((root / ".github/workflows/v03-real-dogfood-scenario.yml").read_text())
    finalizer_text = (root / ".github/workflows/v03-finalize-real-dogfood-scenario.yml").read_text()
    finalizer = yaml.safe_load(finalizer_text)
    for workflow in (live, finalizer):
        for identity in credential_identities(load_registry()):
            expect("HAS_" + identity in workflow["env"], "missing production binding presence signal")
    expect(live["permissions"]["actions"] == "write", "real Worker dispatch lacks authority")
    expect("workflow_run:" not in finalizer_text, "finalizer violates repository trigger boundary")
    expect(finalizer["permissions"]["actions"] == "read", "finalizer must remain read-only")
    expect(any("gh workflow run v03-finalize-real-dogfood-scenario.yml" in step.get("run", "")
               for step in live["jobs"]["dogfood"]["steps"]), "successful dogfood does not queue closed finalizer")
    expect(any("30" in step.get("run", "") and ".status" in step.get("run", "")
               for step in finalizer["jobs"]["finalize"]["steps"]), "finalizer lacks bounded source completion wait")
    print("v0.3 dogfood runtime driver validation passed")
    print("- PR validation cannot enter live preflight/run")
    print("- live preflight/run require workflow_dispatch on refs/heads/main")
    print("- only the three frozen dogfood scenarios are selectable")


if __name__ == "__main__":
    main()
