#!/usr/bin/env python3
"""Validate Issue #223 separation between protocol conformance and product-neutral trust benchmarking."""
from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_conformance import (
    AliasFixtureAdapter,
    DirectFixtureAdapter,
    FROZEN_CONFORMANCE_SUBSET,
    JsonRoundTripFixtureAdapter,
    assert_materially_independent,
)

CONTRACT = ROOT / "benchmarks" / "operator-trust-suites-v0.1.yaml"
DRAFT = ROOT / "release" / "v0.3.0-draft.yaml"
DOC = ROOT / "docs" / "operator-protocol-conformance-and-trust-benchmark.md"

EXPECTED_SCENARIOS = {
    "reviewed-candidate-changes-before-verdict",
    "duplicate-external-completion-callback",
    "external-success-acknowledgement-lost",
    "orchestrator-crash-resume-or-takeover",
    "cancellation-around-inflight-external-action",
    "reviewer-same-execution-identity-as-author",
    "prior-approval-replayed-against-different-commit",
    "uncertain-external-state-during-retry",
}
EXPECTED_METRICS = {
    "duplicate-external-effect-count",
    "unauthorized-lifecycle-transition-count",
    "stale-evidence-acceptance-rate",
    "self-review-acceptance-rate",
    "speculative-retry-count-under-uncertain-state",
    "recovery-success-rate",
    "recovery-time",
    "human-intervention-count",
    "false-blocked-rate",
    "happy-path-completion-latency",
    "persistent-write-api-storage-overhead",
}
REQUIRED_OUTCOMES = {
    "unsupported",
    "unsafe",
    "requires-human-intervention",
    "safe-recovered",
}


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def normalized(text: object) -> str:
    return " ".join(str(text or "").lower().replace("-", " ").split())


def main() -> None:
    errors: list[str] = []
    require(CONTRACT.is_file(), "Issue #223 benchmark contract is missing", errors)
    require(DOC.is_file(), "Issue #223 suite-separation document is missing", errors)
    require(DRAFT.is_file(), "v0.3 release draft is missing", errors)
    if errors:
        raise SystemExit("\n".join(errors))

    contract = load_yaml(CONTRACT)
    draft = load_yaml(DRAFT)
    require(isinstance(contract, dict), "benchmark contract must be a mapping", errors)
    if not isinstance(contract, dict):
        raise SystemExit("\n".join(errors))

    require(contract.get("version") == "0.1.0", "benchmark contract version must be 0.1.0", errors)
    require(contract.get("tracking_issue") == "#223", "benchmark contract must bind Issue #223", errors)

    suite_a = contract.get("suite_a")
    require(isinstance(suite_a, dict), "suite_a must be a mapping", errors)
    if isinstance(suite_a, dict):
        require(suite_a.get("id") == "ai-sdlc-operator-protocol-conformance", "unexpected Suite A id", errors)
        require(suite_a.get("protocol") == "ai-sdlc.operator/v1", "Suite A must bind the canonical Operator protocol", errors)
        require(suite_a.get("normative_for_ai_sdlc_compatibility") is True, "Suite A must be normative for AI-SDLC compatibility", errors)
        require(suite_a.get("product_comparison") is False, "Suite A must not be the product-comparison benchmark", errors)
        require(suite_a.get("fixture_adapters_count_as_supported") is False, "test fixtures must never count as supported adapters", errors)
        harness = suite_a.get("harness")
        require(isinstance(harness, str) and (ROOT / harness).is_file(), "Suite A executable harness must exist", errors)
        declared_subset = tuple(suite_a.get("common_capabilities") or [])
        require(declared_subset == FROZEN_CONFORMANCE_SUBSET, "Suite A common capabilities drift from operator_conformance.py", errors)
        release_subset = tuple((((draft or {}).get("client_contract") or {}).get("common_conformance_subset") or []))
        require(declared_subset == release_subset, "Suite A common capabilities drift from the frozen v0.3 release draft", errors)
        assertions = set(suite_a.get("required_assertions") or [])
        require(
            {
                "api-version-negotiation",
                "structured-error-semantics",
                "identity-propagation",
                "unsupported-capability-behavior",
                "trusted-context-injection-rejected",
                "materially-independent-adapter-evidence",
            }.issubset(assertions),
            "Suite A omits required conformance assertions",
            errors,
        )

    # Exercise the existing harness's independence guardrails. Two distinct test
    # fixtures may prove the guard itself works, but neither becomes release evidence.
    direct = DirectFixtureAdapter()
    json_roundtrip = JsonRoundTripFixtureAdapter()
    try:
        left, right = assert_materially_independent(direct, json_roundtrip)
        require(left.adapter_id != right.adapter_id, "independence evidence must preserve distinct adapter ids", errors)
    except AssertionError as exc:
        errors.append(f"independence positive control failed: {exc}")
    try:
        assert_materially_independent(direct, AliasFixtureAdapter(direct))
        errors.append("thin wrapper alias unexpectedly counted as materially independent")
    except AssertionError:
        pass

    suite_b = contract.get("suite_b")
    require(isinstance(suite_b, dict), "suite_b must be a mapping", errors)
    if isinstance(suite_b, dict):
        require(suite_b.get("id") == "autonomous-sdlc-trust-benchmark", "unexpected Suite B id", errors)
        require(suite_b.get("normative_for_ai_sdlc_compatibility") is False, "Suite B must not define AI-SDLC compatibility", errors)
        require(suite_b.get("release_blocker") is False, "Suite B must remain non-blocking for v0.3 unless separately approved", errors)
        require(suite_b.get("product_neutral") is True, "Suite B must be product-neutral", errors)
        require(suite_b.get("internal_contract_required") is False, "Suite B must not require AI-SDLC internal contracts", errors)

        metrics = set(suite_b.get("metrics") or [])
        require(metrics == EXPECTED_METRICS, "Suite B metric vocabulary drifted from Issue #223", errors)
        require(set(suite_b.get("required_outcome_classes") or []) == REQUIRED_OUTCOMES, "Suite B outcome classes are incomplete", errors)

        scenarios = suite_b.get("scenarios") or []
        require(isinstance(scenarios, list), "Suite B scenarios must be a list", errors)
        scenario_ids = {item.get("id") for item in scenarios if isinstance(item, dict)}
        require(scenario_ids == EXPECTED_SCENARIOS, "Suite B scenarios drifted from the approved black-box set", errors)

        forbidden = [normalized(term) for term in suite_b.get("forbidden_internal_terms_in_observable_scenarios") or []]
        for item in scenarios if isinstance(scenarios, list) else []:
            if not isinstance(item, dict):
                errors.append("Suite B scenario must be a mapping")
                continue
            scenario_id = item.get("id") or "<unknown>"
            observable = normalized(item.get("observable_condition"))
            expected = normalized(item.get("expected_safety_property"))
            require(bool(observable), f"{scenario_id}: observable_condition is required", errors)
            require(bool(expected), f"{scenario_id}: expected_safety_property is required", errors)
            for term in forbidden:
                require(term not in observable, f"{scenario_id}: observable condition leaks AI-SDLC internal term {term!r}", errors)
            scenario_metrics = set(item.get("metrics") or [])
            require(bool(scenario_metrics), f"{scenario_id}: at least one observable metric is required", errors)
            require(scenario_metrics.issubset(metrics), f"{scenario_id}: scenario uses undeclared metrics", errors)

        fairness = suite_b.get("fairness") or {}
        require(fairness.get("require_observable_outcomes") is True, "Suite B must score observable outcomes", errors)
        require(fairness.get("require_environment_and_inputs") is True, "Suite B must publish environment and scenario inputs", errors)
        require(fairness.get("require_scoring_methodology") is True, "Suite B must publish scoring methodology", errors)
        require(fairness.get("do_not_require_ai_sdlc_internal_objects") is True, "Suite B fairness must reject internal-object requirements", errors)

    if errors:
        print("Operator trust suite validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print("Operator trust suite separation passed")
    print(f"- Suite A common capabilities: {len(FROZEN_CONFORMANCE_SUBSET)}")
    print(f"- Suite B black-box scenarios: {len(EXPECTED_SCENARIOS)}")
    print(f"- Suite B observable metrics: {len(EXPECTED_METRICS)}")
    print("- fixture/thin-wrapper adapters: excluded from supported-adapter evidence")
    print("- Suite B: product-neutral and non-blocking for v0.3")


if __name__ == "__main__":
    main()
