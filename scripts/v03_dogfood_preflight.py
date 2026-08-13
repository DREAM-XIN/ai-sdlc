#!/usr/bin/env python3
"""Evaluate v0.3 dogfood prerequisites from trusted/live observations.

This evaluator never creates release evidence and never upgrades the release
ledger. UNKNOWN or missing prerequisites fail closed as BLOCKED.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spec" / "operator" / "dogfood-preflight-observation.schema.json"
SCENARIOS = ("happy_path", "review_remediation", "session_recovery")


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_schema():
    with SCHEMA.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_observation(observation: dict) -> None:
    errors = sorted(
        Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(observation),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = "; ".join(error.message for error in errors)
        raise ValueError(f"invalid dogfood preflight observation: {rendered}")


def _supported_common_adapters(observation: dict) -> list[dict]:
    return [
        adapter
        for adapter in observation.get("adapters", [])
        if adapter.get("supported") is True and adapter.get("common_conformance") is True
    ]


def evaluate(observation: dict) -> dict:
    validate_observation(observation)
    state_store = observation["state_store"]
    runtime = observation["runtime"]
    backing = observation["operator_backing"]
    common_adapters = _supported_common_adapters(observation)
    write_adapters = [adapter for adapter in common_adapters if adapter.get("write_capable") is True]

    common_blockers: list[str] = []
    if state_store["exists"] is not True:
        common_blockers.append("durable-state-ref-missing")
    if state_store["protection_status"] != "PROTECTED":
        common_blockers.append(
            "durable-state-ref-protection-" + str(state_store["protection_status"]).lower()
        )
    if not common_adapters:
        common_blockers.append("no-supported-common-conformance-adapter")

    scenario_blockers: dict[str, list[str]] = {scenario: list(common_blockers) for scenario in SCENARIOS}

    for scenario in ("happy_path", "review_remediation"):
        if not write_adapters:
            scenario_blockers[scenario].append("no-supported-write-capable-adapter")
        if runtime["supported"] is not True:
            scenario_blockers[scenario].append("no-supported-worker-runtime")
        if runtime["stable_external_dispatch_key"] is not True:
            scenario_blockers[scenario].append("stable-external-dispatch-key-unproven")
        if runtime["trusted_receipt_lookup"] is not True:
            scenario_blockers[scenario].append("trusted-runtime-receipt-lookup-unproven")

    if backing["decision_notification_backing"] is not True:
        scenario_blockers["session_recovery"].append("decision-notification-backing-unavailable")
    if backing["new_session_discovery"] is not True:
        scenario_blockers["session_recovery"].append("new-session-discovery-unavailable")

    scenarios = {}
    for scenario in SCENARIOS:
        blockers = tuple(dict.fromkeys(scenario_blockers[scenario]))
        scenarios[scenario] = {
            "status": "READY" if not blockers else "BLOCKED",
            "blockers": list(blockers),
        }

    return {
        "contract": "ai-sdlc.v0.3-dogfood-preflight-result/v1",
        "repository": observation["repository"],
        "observed_at": observation["observed_at"],
        "state_ref": state_store["state_ref"],
        "release_evidence_created": False,
        "release_status_changed": False,
        "supported_common_adapters": [item["adapter_id"] for item in common_adapters],
        "supported_write_adapters": [item["adapter_id"] for item in write_adapters],
        "scenarios": scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("observation", type=Path)
    args = parser.parse_args()
    result = evaluate(load_yaml(args.observation))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
