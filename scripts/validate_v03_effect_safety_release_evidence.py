#!/usr/bin/env python3
"""Adversarial validation for the v0.3 real-runtime effect-safety evidence contract.

This script validates contract behavior only. Its in-memory examples are never
written as release evidence and never satisfy Issue #221 by themselves.
"""
from __future__ import annotations

from copy import deepcopy

from v03_effect_safety_release_evidence import (
    CALLBACK_SCENARIOS,
    EXACT_PERSIST_SCENARIOS,
    REAL_RUNTIME_EVIDENCE_LEVEL,
    RELEASE_REQUIRED_SCENARIOS,
    REQUIRED_PRODUCTION_PREREQUISITES,
    RUNTIME_RECEIPT_SCENARIOS,
    SCENARIO_ASSERTIONS,
    SUCCESSFUL_CALLBACK_PERSIST_SCENARIOS,
    SCHEMA_VERSION,
    ReleaseEvidenceError,
    validate_release_evidence,
    validate_release_matrix,
)

RUN_ID = 31458067505
MATERIALIZATION_SHA = "f" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_rejected(record, message, *, complete=False):
    try:
        if complete:
            validate_release_matrix(record, require_complete_pass=True)
        else:
            validate_release_evidence(record)
    except ReleaseEvidenceError:
        return
    raise AssertionError(message)


def base_record(scenario="lost-ack-crash-takeover"):
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": scenario,
        "status": "BLOCKED",
        "release_eligible": False,
        "subject": {
            "repository": "DREAM-XIN/ai-sdlc",
            "feature_id": "F-OPERATOR-DECISIONS-NOTIFICATIONS-0001",
            "target_ref": "feature/F-OPERATOR-DECISIONS-NOTIFICATIONS-0001",
            "feature_revision": 20,
            "candidate_pr_number": 230,
            "candidate_head_sha": "a" * 40,
            "operation_id": "op-release-evidence-contract",
            "operation_generation": 0,
        },
        "effect": {
            "semantic_effect_key": "effect-release-evidence-contract",
            "external_dispatch_key": "dispatch-release-evidence-contract",
        },
        "remaining_release_proof": ["real supported-runtime execution is still required"],
    }


def policy_authority():
    return {
        "repository": "DREAM-XIN/ai-sdlc",
        "state_ref": "refs/heads/ai-sdlc-operator-state",
        "installation_commit_sha": "1" * 40,
        "materialization_commit_sha": MATERIALIZATION_SHA,
        "receipt_ref": (
            "protected-commit://DREAM-XIN/ai-sdlc@"
            + MATERIALIZATION_SHA
            + "/config/operator/v03-vertical-policy/bundle-receipt.json"
        ),
        "receipt_digest": "2" * 64,
        "policy_bundle_digest": "3" * 64,
        "post_write_verified_state_ref_sha": "4" * 40,
        "post_write_protection": {
            "verifier_identity": "github-ruleset:integration:4576406",
            "verified_at": "2026-08-14T00:00:00Z",
            "policy_digest": "5" * 64,
        },
    }


def pass_record(scenario="lost-ack-crash-takeover"):
    record = base_record(scenario)
    record.update(
        {
            "status": "PASS",
            "release_eligible": True,
            "evidence_level": REAL_RUNTIME_EVIDENCE_LEVEL,
            "control_ref": "main",
            "prerequisites": {key: True for key in REQUIRED_PRODUCTION_PREREQUISITES},
            "durable_store": {
                "repository": "DREAM-XIN/ai-sdlc",
                "state_ref": "refs/heads/ai-sdlc-operator-state",
                "snapshot_sha": "c" * 40,
                "operation_projection_digest": "d" * 64,
                "operation_event_ids": ["EVT-STORE-1", "EVT-STORE-2"],
            },
            "policy_authority": policy_authority(),
            "github_run": {
                "id": RUN_ID,
                "url": f"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/{RUN_ID}",
                "workflow": "v0.3 Real Runtime Effect Safety Smoke",
                "head_sha": "b" * 40,
            },
            "runtime": {"adapter": "gh-aw/github-actions"},
            "callback": {},
            "translated_event": {},
            "persist": {},
            "observations": {
                "duplicate_external_effect_count": 0,
                "unauthorized_lifecycle_transition_count": 0,
                "stale_evidence_accepted_count": 0,
                "speculative_retry_under_unknown_count": 0,
            },
            "assertions": {key: True for key in SCENARIO_ASSERTIONS[scenario]},
            "evidence_uris": [
                f"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/{RUN_ID}",
                "https://github.com/DREAM-XIN/ai-sdlc/issues/221",
            ],
            "remaining_release_proof": [],
        }
    )
    if scenario in RUNTIME_RECEIPT_SCENARIOS:
        record["github_run"]["run_attempt"] = 1
        record["runtime"]["receipt_id"] = str(RUN_ID)
    if scenario in CALLBACK_SCENARIOS:
        record["callback"] = {"id": f"callback-{scenario}"}
    if scenario in SUCCESSFUL_CALLBACK_PERSIST_SCENARIOS:
        event_id = f"EVT-{scenario.upper()}"
        record["translated_event"] = {"id": event_id, "digest": "e" * 64}
        record["persist"] = {"event_id": event_id, "result_revision": 21}
    elif scenario in EXACT_PERSIST_SCENARIOS:
        record["persist"] = {"event_id": "EVT-RELEASE-EVIDENCE-CONTRACT", "result_revision": 21}
    return record


def main():
    blocked = base_record()
    require(validate_release_evidence(blocked)["status"] == "BLOCKED", "valid BLOCKED record rejected")
    bad = deepcopy(blocked)
    bad["release_eligible"] = True
    expect_rejected(bad, "BLOCKED evidence was allowed to become release eligible")

    structural_pass = pass_record()
    require(validate_release_evidence(structural_pass)["status"] == "PASS", "valid PASS shape rejected")
    require(len(REQUIRED_PRODUCTION_PREREQUISITES) == 18, "production prerequisite contract must bind all 18 current prerequisites")

    bad = deepcopy(structural_pass)
    bad["evidence_level"] = "deterministic-support"
    expect_rejected(bad, "deterministic support was accepted as release PASS")
    bad = deepcopy(structural_pass)
    bad["control_ref"] = "verification/v0.3-real-runtime-effect-safety-221"
    expect_rejected(bad, "untrusted verification ref was accepted as release runtime authority")

    bad = deepcopy(structural_pass)
    bad.pop("prerequisites")
    expect_rejected(bad, "PASS without production prerequisites was accepted")
    missing_prerequisite = sorted(REQUIRED_PRODUCTION_PREREQUISITES)[0]
    bad = deepcopy(structural_pass)
    bad["prerequisites"].pop(missing_prerequisite)
    expect_rejected(bad, "PASS with an omitted production prerequisite was accepted")
    false_prerequisite = sorted(REQUIRED_PRODUCTION_PREREQUISITES)[1]
    bad = deepcopy(structural_pass)
    bad["prerequisites"][false_prerequisite] = False
    expect_rejected(bad, "PASS with a false production prerequisite was accepted")
    bad = deepcopy(structural_pass)
    bad["prerequisites"]["invented_release_prerequisite"] = True
    expect_rejected(bad, "PASS with an unrecognized production prerequisite key was accepted")

    bad = deepcopy(structural_pass)
    bad.pop("durable_store")
    expect_rejected(bad, "PASS without protected durable Store evidence was accepted")
    bad = deepcopy(structural_pass)
    bad["durable_store"]["snapshot_sha"] = "not-a-store-sha"
    expect_rejected(bad, "PASS without exact durable Store snapshot SHA was accepted")
    bad = deepcopy(structural_pass)
    bad["durable_store"]["operation_projection_digest"] = "abc123"
    expect_rejected(bad, "PASS without exact durable Operation projection digest was accepted")

    # Current protected policy authority must be explicit and cross-bound to the
    # same repository/state ref as the durable Store evidence.
    bad = deepcopy(structural_pass)
    bad.pop("policy_authority")
    expect_rejected(bad, "PASS without protected policy authority was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["repository"] = "DREAM-XIN/foreign"
    expect_rejected(bad, "policy authority for a foreign repository was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["state_ref"] = "refs/heads/foreign-state"
    expect_rejected(bad, "policy authority for a different state ref was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["installation_commit_sha"] = "bad"
    expect_rejected(bad, "invalid policy installation SHA was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["materialization_commit_sha"] = "6" * 40
    expect_rejected(bad, "policy materialization/receipt ref mismatch was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["receipt_ref"] = "protected-commit://DREAM-XIN/ai-sdlc@" + MATERIALIZATION_SHA + "/wrong.json"
    expect_rejected(bad, "wrong policy receipt path was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["receipt_digest"] = "bad"
    expect_rejected(bad, "invalid policy receipt digest was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["policy_bundle_digest"] = "bad"
    expect_rejected(bad, "invalid policy bundle digest was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["post_write_verified_state_ref_sha"] = "bad"
    expect_rejected(bad, "invalid post-write protected snapshot SHA was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["post_write_protection"]["policy_digest"] = "bad"
    expect_rejected(bad, "invalid post-write protection digest was accepted")
    bad = deepcopy(structural_pass)
    bad["policy_authority"]["post_write_protection"].pop("verifier_identity")
    expect_rejected(bad, "policy authority without protection verifier identity was accepted")

    bad = deepcopy(structural_pass)
    bad["subject"]["candidate_head_sha"] = "abc123"
    expect_rejected(bad, "non-exact candidate SHA was accepted")
    bad = deepcopy(structural_pass)
    bad["github_run"].pop("run_attempt")
    expect_rejected(bad, "runtime receipt PASS without run_attempt was accepted")
    bad = deepcopy(structural_pass)
    bad["github_run"]["run_attempt"] = 2
    expect_rejected(bad, "GitHub Actions rerun attempt was accepted")
    bad = deepcopy(structural_pass)
    bad["runtime"]["receipt_id"] = str(RUN_ID + 1)
    expect_rejected(bad, "runtime receipt id differed from GitHub run id")
    bad = deepcopy(structural_pass)
    bad["callback"].pop("id")
    expect_rejected(bad, "callback-bearing PASS without durable callback id was accepted")
    bad = deepcopy(structural_pass)
    bad["translated_event"].pop("digest")
    expect_rejected(bad, "successful callback PASS without translated Event digest was accepted")
    bad = deepcopy(structural_pass)
    bad["translated_event"]["digest"] = "not-a-sha256"
    expect_rejected(bad, "invalid translated Event digest was accepted")
    bad = deepcopy(structural_pass)
    bad["persist"]["event_id"] = "EVT-DIFFERENT"
    expect_rejected(bad, "Persist Event differed from translated Event")
    bad = deepcopy(structural_pass)
    bad["persist"].pop("result_revision")
    expect_rejected(bad, "successful callback PASS without Persist revision was accepted")
    bad = deepcopy(structural_pass)
    bad["persist"]["result_revision"] = 22
    expect_rejected(bad, "Persist result revision skipped exact next Feature revision")
    bad = deepcopy(structural_pass)
    bad["persist"]["receipt_id"] = "fabricated-persist-receipt"
    expect_rejected(bad, "fabricated Persist receipt identity was accepted")

    bad = deepcopy(structural_pass)
    bad["observations"]["duplicate_external_effect_count"] = 1
    expect_rejected(bad, "non-zero duplicate external effect count was accepted")
    bad = deepcopy(structural_pass)
    bad["observations"]["unauthorized_lifecycle_transition_count"] = 1
    expect_rejected(bad, "unauthorized lifecycle mutation was accepted")
    bad = deepcopy(structural_pass)
    bad["assertions"]["exactly_one_external_run"] = False
    expect_rejected(bad, "false scenario assertion was accepted")

    persist_pass = pass_record("persist-ack-loss-recovery")
    require(validate_release_evidence(persist_pass)["status"] == "PASS", "Persist PASS shape rejected")
    require("receipt_id" not in persist_pass["persist"], "validator fixture fabricated a Persist receipt id")
    bad = deepcopy(persist_pass)
    bad["persist"].pop("event_id")
    expect_rejected(bad, "Persist scenario without exact Event identity was accepted")
    bad = deepcopy(persist_pass)
    bad["persist"]["result_revision"] = 22
    expect_rejected(bad, "Persist recovery accepted a non-next result revision")
    bad = deepcopy(persist_pass)
    bad["persist"]["receipt_id"] = "fabricated"
    expect_rejected(bad, "Persist recovery accepted fabricated receipt identity")

    out_of_order = pass_record("out-of-order-callback")
    require(validate_release_evidence(out_of_order)["status"] == "PASS", "callback rejection PASS shape rejected")
    require(out_of_order["translated_event"] == {}, "stale callback fixture fabricated translated Event")
    require(out_of_order["persist"] == {}, "stale callback fixture fabricated Persist evidence")

    duplicate_completion = pass_record("duplicate-worker-completion")
    require(validate_release_evidence(duplicate_completion)["status"] == "PASS", "duplicate Worker completion PASS shape rejected")
    require(duplicate_completion["github_run"]["run_attempt"] == 1, "duplicate completion lost first-attempt binding")
    require(duplicate_completion["runtime"]["receipt_id"] == str(RUN_ID), "duplicate completion receipt binding drifted")
    require(bool(duplicate_completion["callback"]["id"]), "duplicate completion lacks callback identity")
    require(duplicate_completion["persist"]["event_id"] == duplicate_completion["translated_event"]["id"], "duplicate completion Persist binding drifted")
    bad = deepcopy(duplicate_completion)
    bad["assertions"]["same_deterministic_callback_id"] = False
    expect_rejected(bad, "duplicate Worker completion accepted different callback identities")
    bad = deepcopy(duplicate_completion)
    bad["assertions"]["zero_second_external_run"] = False
    expect_rejected(bad, "duplicate Worker completion accepted a second external run")

    expect_rejected([blocked], "partial matrix was accepted as complete release proof", complete=True)
    complete = [pass_record(scenario) for scenario in RELEASE_REQUIRED_SCENARIOS]
    by_scenario = validate_release_matrix(complete, require_complete_pass=True)
    require(set(by_scenario) == set(RELEASE_REQUIRED_SCENARIOS), "complete matrix lost a required scenario")
    require(len(by_scenario) == 12, "Issue #221 release matrix must contain twelve explicit scenarios")

    print("v0.3 real-runtime effect-safety release evidence contract validation passed")
    print(f"- required release scenarios: {len(RELEASE_REQUIRED_SCENARIOS)}")
    print(f"- required trusted-main production prerequisites: {len(REQUIRED_PRODUCTION_PREREQUISITES)}")
    print("- PASS binds exact #267/#273 protected policy authority and post-write protection generation")
    print("- duplicate Worker completion is explicit and distinct from duplicate callback")
    print("- runtime receipts bind exact GitHub run id and first attempt")
    print("- callback scenarios bind durable callback identity")
    print("- successful callback lifecycle binds translated Event digest to exact Persist Event")
    print("- Persist authority is exact Event id + exact next Feature revision; no synthetic receipt id exists")
    print("- deterministic support cannot masquerade as release PASS")
    print("- BLOCKED/PENDING records are never release eligible")
    print("- validation fixtures are in-memory contract tests only; no Issue #221 PASS is emitted")


if __name__ == "__main__":
    main()
