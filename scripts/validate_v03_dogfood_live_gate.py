#!/usr/bin/env python3
"""Deterministic validation for the trusted v0.3 real-dogfood upstream gate."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from gh_aw_provider_registry import load_registry
from v03_dogfood_execution_bindings import credential_identities
from v03_dogfood_live_gate import (
    Issue221Closure,
    V03DogfoodLiveGateError,
    assemble_dogfood_live_gate,
    public_gate,
)

SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def ready_env():
    registry = load_registry()
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": "DREAM-XIN/ai-sdlc",
        "GITHUB_API_URL": "https://api.github.test",
        "AI_SDLC_ACTIONS_READ_TOKEN": "presence-only-test-token",
    }
    for identity in credential_identities(registry):
        env[f"HAS_{identity}"] = "false"
    # Reproduce the current production route: Developer falls back to Copilot,
    # while independent Reviewer and QA use Gemini.
    env["HAS_COPILOT_GITHUB_TOKEN"] = "true"
    env["HAS_GEMINI_API_KEY"] = "true"
    return env


def closure(**kwargs):
    return Issue221Closure(
        trusted_main_head_sha=kwargs["installation_sha"],
        accepted_record_count=11,
        accepted_workflow_run_count=11,
        satisfied_scenario_count=13,
        workflow_run_ids=tuple(range(101, 112)),
        ledger_digest="sha256:" + "b" * 64,
    )


def expect_failure(*, scenario="happy_path", env=None, verifier=closure, label):
    try:
        assemble_dogfood_live_gate(
            scenario=scenario,
            env=ready_env() if env is None else env,
            checkout_sha=SHA,
            issue221_verifier=verifier,
        )
    except Exception:
        return
    raise AssertionError(f"{label} unexpectedly passed dogfood gate")


def validate_real_workflow_presence_contract():
    identities = credential_identities(load_registry())
    expected = {
        f"HAS_{identity}": "${{ secrets." + identity + " != '' }}"
        for identity in identities
    }
    for filename, step_name in (
        ("v03-real-dogfood-scenario.yml", "Execute one frozen real dogfood scenario"),
        ("v03-finalize-real-dogfood-scenario.yml", "Re-read protected runtime truth and finalize canonical release-run"),
        ("v03-dogfood-execution-bindings-preflight.yml", "Resolve exact production Worker / provider / credential bindings"),
    ):
        try:
            workflow = yaml.safe_load((ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AssertionError(f"{filename} is not valid workflow YAML: {exc.problem}") from exc
        steps = [step for job in workflow["jobs"].values() for step in job["steps"] if step.get("name") == step_name]
        require(len(steps) == 1, f"{filename} must have exactly one {step_name!r} step")
        actual = {key: value for key, value in steps[0].get("env", {}).items() if key.startswith("HAS_")}
        require(actual == expected, f"{filename} does not pass the exact production credential-presence contract")


def main():
    validate_real_workflow_presence_contract()
    for scenario in ("happy_path", "review_remediation", "session_recovery"):
        gate = assemble_dogfood_live_gate(
            scenario=scenario,
            env=ready_env(),
            checkout_sha=SHA,
            issue221_verifier=closure,
        )
        require(gate.installation_commit_sha == SHA, "gate installation SHA drifted")
        require(gate.issue221.satisfied_scenario_count == 13, "#221 closure lost 13-row proof")
        require(len(gate.issue221.workflow_run_ids) == 11, "#221 closure lost 11 source runs")
        bindings = {row.role: row for row in gate.bindings}
        require(bindings["developer"].selected_profile == "copilot", "Developer fallback drifted")
        require(bindings["reviewer"].selected_profile == "gemini", "Reviewer binding drifted")
        require(bindings["qa"].selected_profile == "gemini", "QA binding drifted")
        rendered = public_gate(gate)
        require(rendered["status"] == "READY", "public gate did not render READY")
        require(rendered["model_called"] is False, "gate claimed a model call")
        require(rendered["worker_dispatched"] is False, "gate claimed Worker dispatch")
        require(rendered["operator_store_mutated"] is False, "gate claimed Store mutation")
        require(rendered["dogfood_evidence_created"] is False, "gate fabricated dogfood evidence")

    expect_failure(scenario="unknown", label="unknown scenario")

    wrong_ref = ready_env()
    wrong_ref["GITHUB_REF"] = "refs/heads/release/test"
    expect_failure(env=wrong_ref, label="non-main trusted context")

    wrong_repo = ready_env()
    wrong_repo["GITHUB_REPOSITORY"] = "DREAM-XIN/other"
    expect_failure(env=wrong_repo, label="wrong repository")

    no_qa = ready_env()
    no_qa["HAS_GEMINI_API_KEY"] = "false"
    # Copilot is a legitimate QA fallback, so remove that too to prove the gate
    # rejects before any live action when the complete QA route is unavailable.
    no_qa["HAS_COPILOT_GITHUB_TOKEN"] = "false"
    expect_failure(env=no_qa, label="missing production role credential")

    def not_closed(**kwargs):
        raise V03DogfoodLiveGateError("Issue #221 final live ledger is not 13/13 PASS")

    expect_failure(verifier=not_closed, label="Issue #221 unresolved")

    def wrong_generation(**kwargs):
        return replace(closure(**kwargs), trusted_main_head_sha="c" * 40)

    expect_failure(verifier=wrong_generation, label="Issue #221 generation mismatch")

    print("v0.3 dogfood live upstream gate: PASS")


if __name__ == "__main__":
    main()
