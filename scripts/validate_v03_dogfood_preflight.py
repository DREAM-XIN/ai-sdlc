#!/usr/bin/env python3
"""Validate v0.3 dogfood prerequisite evaluation and anti-overclaim behavior."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from v03_dogfood_preflight import evaluate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "operator" / "dogfood"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_blocked_fixture():
    observation = load_yaml(FIXTURES / "preflight-blocked.yaml")
    result = evaluate(observation)
    require(result["release_evidence_created"] is False, "preflight must never create release evidence")
    require(result["release_status_changed"] is False, "preflight must never mutate release status")
    require(result["supported_common_adapters"] == ["ai-sdlc.mcp.stdio"], "supported read adapter observation drifted")
    require(result["supported_write_adapters"] == [], "blocked fixture unexpectedly has a supported write adapter")

    happy = result["scenarios"]["happy_path"]
    remediation = result["scenarios"]["review_remediation"]
    recovery = result["scenarios"]["session_recovery"]
    for name, row in result["scenarios"].items():
        require(row["status"] == "BLOCKED", f"{name}: missing prerequisites unexpectedly READY")
        require("durable-state-ref-missing" in row["blockers"], f"{name}: missing durable state ref was not reported")
        require("durable-state-ref-protection-unknown" in row["blockers"], f"{name}: UNKNOWN protection did not fail closed")
    require("no-supported-write-capable-adapter" in happy["blockers"], "happy path did not require write-capable adapter")
    require("no-supported-write-capable-adapter" in remediation["blockers"], "remediation did not require write-capable adapter")
    require("trusted-runtime-receipt-lookup-unproven" in happy["blockers"], "happy path did not require trusted receipt lookup")
    require("trusted-runtime-receipt-lookup-unproven" in remediation["blockers"], "remediation did not require trusted receipt lookup")
    require("no-supported-write-capable-adapter" not in recovery["blockers"], "session recovery incorrectly requires a write adapter")


def validate_ready_fixture():
    result = evaluate(load_yaml(FIXTURES / "preflight-ready.yaml"))
    require(result["supported_common_adapters"] == ["ai-sdlc.mcp.stdio", "ai-sdlc.openai.responses"], "ready fixture common adapter set drifted")
    require(result["supported_write_adapters"] == ["ai-sdlc.openai.responses"], "ready fixture write adapter set drifted")
    for name, row in result["scenarios"].items():
        require(row == {"status": "READY", "blockers": []}, f"{name}: complete prerequisites did not produce READY")
    require(result["release_evidence_created"] is False, "READY preflight must still not create release evidence")
    require(result["release_status_changed"] is False, "READY preflight must still not mutate release status")


def validate_adversarial_unknowns():
    ready = load_yaml(FIXTURES / "preflight-ready.yaml")

    unknown_protection = copy.deepcopy(ready)
    unknown_protection["state_store"]["protection_status"] = "UNKNOWN"
    result = evaluate(unknown_protection)
    for name, row in result["scenarios"].items():
        require(row["status"] == "BLOCKED", f"{name}: UNKNOWN protection unexpectedly READY")

    no_receipt = copy.deepcopy(ready)
    no_receipt["runtime"]["trusted_receipt_lookup"] = False
    result = evaluate(no_receipt)
    require(result["scenarios"]["happy_path"]["status"] == "BLOCKED", "happy path ignored missing trusted receipt lookup")
    require(result["scenarios"]["review_remediation"]["status"] == "BLOCKED", "remediation ignored missing trusted receipt lookup")
    require(result["scenarios"]["session_recovery"]["status"] == "READY", "session recovery was over-coupled to worker receipt lookup")

    no_discovery = copy.deepcopy(ready)
    no_discovery["operator_backing"]["new_session_discovery"] = False
    result = evaluate(no_discovery)
    require(result["scenarios"]["session_recovery"]["status"] == "BLOCKED", "session recovery ignored missing new-session discovery")


def main():
    validate_blocked_fixture()
    validate_ready_fixture()
    validate_adversarial_unknowns()
    print("v0.3 dogfood preflight validation passed")
    print("- UNKNOWN protection: BLOCKED")
    print("- happy/remediation: require supported write adapter + real runtime receipt lookup")
    print("- session recovery: requires durable protected state + supported common adapter + discovery backing")
    print("- preflight READY never creates release evidence or changes release status")


if __name__ == "__main__":
    main()
