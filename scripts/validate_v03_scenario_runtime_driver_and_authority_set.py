#!/usr/bin/env python3
"""Zero-effect validation for #314 scenario driver and multi-authority Issue #221 ledger."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import v03_scenario_runtime_driver as driver
from provision_v03_real_runtime_fixture import FEATURE_ID as ORIGINAL_FEATURE_ID, TARGET_REF as ORIGINAL_TARGET_REF
from v03_effect_safety_live_ledger import (
    REQUIRED_SCENARIOS,
    SCENARIO_MEASUREMENTS,
    LiveEvidenceError,
    ReleaseAuthority,
)
from v03_effect_safety_live_ledger_authority_set import (
    ReleaseAuthoritySet,
    authority_set_document,
    evaluate_issue_221_authority_set,
    expected_scenario_bindings,
)
from v03_scenario_fixture_pool import EXPECTED_SCENARIOS, SLOTS, inventory_document

REPOSITORY = "dream-xin/ai-sdlc"
MAIN = "1" * 40
MATERIALIZATION = "2" * 40
POLICY = "a" * 64
CANDIDATE = "3" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def common_authority(feature_id=ORIGINAL_FEATURE_ID, target_ref=ORIGINAL_TARGET_REF):
    return ReleaseAuthority.from_document({
        "schema_version": "ai-sdlc.v03-effect-safety-live-authority/v1",
        "repository": REPOSITORY,
        "feature_id": feature_id,
        "target_ref": target_ref,
        "trusted_main_head_sha": MAIN,
        "materialization_commit_sha": MATERIALIZATION,
        "policy_bundle_digest": POLICY,
        "runtime_kind": "gh-aw-actions",
        "protected_policy_status": "PROTECTED",
        "effect_lineage_required": True,
        "writer_fence_quiesced": True,
    })


def authority_set():
    doc = authority_set_document(
        authority=common_authority(),
        fixture_pool_inventory_digest=inventory_document()["inventory_digest"],
    )
    return doc, ReleaseAuthoritySet.from_document(doc)


def scenario_record(scenario, index, *, claims=None):
    claims = list(claims or [scenario])
    measurements = {}
    for claim in claims:
        for name in SCENARIO_MEASUREMENTS[claim]:
            measurements[name] = 0
    document = {
        "schema_version": "ai-sdlc.v03-effect-safety-live-scenario/v1",
        "status": "PASS",
        "completed_issue_221_scenarios": claims,
        "operation_id": f"op-{index}",
        "operation_generation": 0,
        "semantic_effect_key": f"semantic-{index}",
        "external_dispatch_key": f"external-{index}",
        "candidate_head_sha": CANDIDATE,
        "feature_revision_before": 1,
        "runtime_receipt_identity": None,
        "runtime_lookup_state": "NOT_APPLICABLE",
        "measurements": measurements,
        "overall_issue_221_pass": False,
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return raw, document


def provenance(raw, authority, index):
    return {
        "schema_version": "ai-sdlc.v03-live-evidence-provenance/v1",
        "evidence_class": "release-live-real-runtime",
        "record_id": f"record-{index}",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "github_workflow_run_id": 1000 + index,
        "trusted_main_head_sha": authority.trusted_main_head_sha,
        "repository": authority.repository,
        "feature_id": authority.feature_id,
        "target_ref": authority.target_ref,
        "materialization_commit_sha": authority.materialization_commit_sha,
        "policy_bundle_digest": authority.policy_bundle_digest,
        "runtime_kind": authority.runtime_kind,
        "protected_policy_status": authority.protected_policy_status,
        "effect_lineage_required": authority.effect_lineage_required,
        "writer_fence_quiesced": authority.writer_fence_quiesced,
    }


def validate_driver_is_closed_and_zero_effect():
    for scenario in EXPECTED_SCENARIOS:
        mode, selected = driver.require_scenario_driver_mode(
            mode=driver.VALIDATE_ONLY,
            scenario=scenario,
            event_name="pull_request",
            ref="refs/pull/1/merge",
        )
        require(mode == driver.VALIDATE_ONLY and selected == scenario, "validate-only scenario drifted")
    for invalid in ("", "unknown", "lost-ack-crash-takeover"):
        try:
            driver.require_scenario_driver_mode(
                mode=driver.VALIDATE_ONLY,
                scenario=invalid,
                event_name="pull_request",
                ref="refs/pull/1/merge",
            )
        except driver.V03ScenarioRuntimeDriverError:
            continue
        raise AssertionError("scenario driver accepted selector outside #310 inventory")
    for event_name, ref in (("pull_request", "refs/heads/main"), ("workflow_dispatch", "refs/heads/other")):
        try:
            driver.require_scenario_driver_mode(
                mode=driver.PREFLIGHT_ONLY,
                scenario=EXPECTED_SCENARIOS[0],
                event_name=event_name,
                ref=ref,
            )
        except driver.V03ScenarioRuntimeDriverError:
            continue
        raise AssertionError("scenario live preflight escaped workflow_dispatch/main gate")

    captured = {}
    fake_preflight = SimpleNamespace(scenario=EXPECTED_SCENARIOS[0])
    fake_execution = SimpleNamespace(repository=REPOSITORY, installation_commit_sha=MAIN)
    fake_live = SimpleNamespace()
    fake_reviewer = SimpleNamespace()
    fake_protection = object()

    def fake_execution_builder(**kwargs):
        captured["execution"] = kwargs
        return fake_execution

    def fake_live_loader(**kwargs):
        captured["live"] = kwargs
        return fake_live

    def fake_reviewer_selector(**kwargs):
        captured["reviewer"] = kwargs
        return fake_reviewer

    def fake_protection_factory(**kwargs):
        captured["protection"] = kwargs
        return fake_protection

    def fake_preflight_builder(**kwargs):
        captured["preflight"] = kwargs
        return fake_preflight

    original_execution = driver.require_trusted_main_execution
    driver.require_trusted_main_execution = fake_execution_builder
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_SHA": MAIN,
        "GITHUB_API_URL": "https://api.github.com",
        "AI_SDLC_OPERATOR_ADMIN_TOKEN": "admin",
        "AI_SDLC_OPERATOR_APP_SLUG": "ai-sdlc-operator",
        "AI_SDLC_OPERATOR_APP_INTEGRATION_ID": "42",
        "AI_SDLC_ACTIONS_READ_TOKEN": "read",
        "AI_SDLC_EVENT_WRITE_TOKEN": "write",
        "AI_SDLC_STORE_CHECKOUT": "/tmp/scenario-store-checkout",
    }
    try:
        result = driver.assemble_scenario_live_preflight(
            scenario=EXPECTED_SCENARIOS[0],
            env=env,
            checkout_sha=MAIN,
            live_loader=fake_live_loader,
            reviewer_selector=fake_reviewer_selector,
            preflight_builder=fake_preflight_builder,
            protection_verifier_factory=fake_protection_factory,
            clock=lambda: "now",
        )
    finally:
        driver.require_trusted_main_execution = original_execution
    require(result is fake_preflight, "scenario driver lost preflight object")
    call = captured["preflight"]
    require(call["scenario"] == EXPECTED_SCENARIOS[0], "scenario preflight selector drifted")
    require("feature_id" not in call and "target_ref" not in call, "driver exposed arbitrary Feature/ref")
    require(str(call["store_checkout"]) == "/tmp/scenario-store-checkout", "separate Store checkout was not honored")
    require(call["actions_token"] == "read" and call["event_write_token"] == "write", "credential split drifted")


def validate_authority_set_is_exact():
    doc, parsed = authority_set()
    expected = expected_scenario_bindings()
    require(set(doc["scenario_bindings"]) == set(REQUIRED_SCENARIOS), "authority-set does not cover exactly 13 rows")
    for scenario in (
        "lost-ack-crash-takeover",
        "persist-ack-loss-recovery",
        "cancellation-before-launch-authorization",
        "launch-authorization-before-cancellation",
    ):
        require(expected[scenario] == {"feature_id": ORIGINAL_FEATURE_ID, "target_ref": ORIGINAL_TARGET_REF}, "original live row escaped original fixture")
    for slot in SLOTS:
        require(expected[slot.scenario] == {"feature_id": slot.feature_id, "target_ref": slot.target_ref}, f"slot authority drifted: {slot.scenario}")
        authority = parsed.authority_for(slot.scenario)
        require(authority.feature_id == slot.feature_id and authority.target_ref == slot.target_ref, "derived slot authority drifted")

    adversarial = []
    missing = copy.deepcopy(doc)
    missing["scenario_bindings"].pop(REQUIRED_SCENARIOS[-1])
    adversarial.append(missing)
    extra = copy.deepcopy(doc)
    extra["scenario_bindings"]["extra"] = {"feature_id": "F-EXTRA", "target_ref": "extra"}
    adversarial.append(extra)
    swapped = copy.deepcopy(doc)
    a, b = EXPECTED_SCENARIOS[:2]
    swapped["scenario_bindings"][a], swapped["scenario_bindings"][b] = swapped["scenario_bindings"][b], swapped["scenario_bindings"][a]
    adversarial.append(swapped)
    bad_digest = copy.deepcopy(doc)
    bad_digest["fixture_pool_inventory_digest"] = "f" * 64
    adversarial.append(bad_digest)
    bad_main = copy.deepcopy(doc)
    bad_main["trusted_main_head_sha"] = "short"
    adversarial.append(bad_main)
    for candidate in adversarial:
        try:
            ReleaseAuthoritySet.from_document(candidate)
        except LiveEvidenceError:
            continue
        raise AssertionError("authority-set accepted drifted closed authority")


def validate_cross_feature_aggregation_and_fail_closed():
    _doc, authorities = authority_set()
    evidence = []
    for index, scenario in enumerate(REQUIRED_SCENARIOS, start=1):
        raw, document = scenario_record(scenario, index)
        evidence.append((raw, document, provenance(raw, authorities.authority_for(scenario), index)))
    ledger = evaluate_issue_221_authority_set(authority_set=authorities, evidence=evidence)
    require(ledger["status"] == "PASS" and ledger["overall_issue_221_pass"] is True, "exact 13-scenario authority set did not PASS")
    require(ledger["satisfied_scenarios"] == list(REQUIRED_SCENARIOS), "final ledger ordering drifted")
    require(ledger["accepted_record_count"] == 13 and ledger["accepted_workflow_run_count"] == 13, "final ledger record/run count drifted")
    require(ledger["deterministic_evidence_accepted"] is False, "authority-set ledger weakened evidence class")

    # Wrong Feature/ref provenance for one #310 row must fail.
    scenario = EXPECTED_SCENARIOS[0]
    raw, document = scenario_record(scenario, 50)
    wrong = provenance(raw, common_authority(), 50)
    try:
        evaluate_issue_221_authority_set(authority_set=authorities, evidence=[(raw, document, wrong)])
    except LiveEvidenceError:
        pass
    else:
        raise AssertionError("new-slot evidence was accepted under original fixture authority")

    # One artifact may not claim rows that belong to different fixed slots.
    first, second = EXPECTED_SCENARIOS[:2]
    raw, document = scenario_record(first, 51, claims=[first, second])
    prov = provenance(raw, authorities.authority_for(first), 51)
    try:
        evaluate_issue_221_authority_set(authority_set=authorities, evidence=[(raw, document, prov)])
    except LiveEvidenceError:
        pass
    else:
        raise AssertionError("one record spanned different Feature/ref authorities")

    # Duplicate scenario, record id, and workflow run remain forbidden.
    raw1, doc1 = scenario_record(first, 60)
    prov1 = provenance(raw1, authorities.authority_for(first), 60)
    raw2, doc2 = scenario_record(first, 61)
    prov2 = provenance(raw2, authorities.authority_for(first), 61)
    try:
        evaluate_issue_221_authority_set(authority_set=authorities, evidence=[(raw1, doc1, prov1), (raw2, doc2, prov2)])
    except LiveEvidenceError:
        pass
    else:
        raise AssertionError("duplicate scenario evidence was accepted")

    prov2 = provenance(raw2, authorities.authority_for(first), 61)
    prov2["record_id"] = prov1["record_id"]
    raw_other, doc_other = scenario_record(second, 62)
    prov_other = provenance(raw_other, authorities.authority_for(second), 62)
    prov_other["record_id"] = prov1["record_id"]
    try:
        evaluate_issue_221_authority_set(authority_set=authorities, evidence=[(raw1, doc1, prov1), (raw_other, doc_other, prov_other)])
    except LiveEvidenceError:
        pass
    else:
        raise AssertionError("duplicate provenance record id was accepted")


def main():
    validate_driver_is_closed_and_zero_effect()
    validate_authority_set_is_exact()
    validate_cross_feature_aggregation_and_fail_closed()
    print("PASS: scenario driver remains closed/zero-effect and 13-row authority-set ledger is exact")
    print("- original four rows stay on the original fixture; remaining nine bind one-to-one to #310 slots")
    print("- exact per-record provenance is preserved across multiple Feature/ref domains")
    print("- cross-domain claims, authority drift and duplicate evidence fail closed")
    print("- deterministic validation only; no Issue #221 live row is satisfied")


if __name__ == "__main__":
    main()
