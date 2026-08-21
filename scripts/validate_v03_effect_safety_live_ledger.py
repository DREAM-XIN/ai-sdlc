#!/usr/bin/env python3
"""Deterministic zero-effect checks for the Issue #221 live evidence ledger."""
from __future__ import annotations

import copy
import hashlib
import json

from v03_effect_safety_live_ledger import (
    REQUIRED_SCENARIOS,
    SCENARIO_MEASUREMENTS,
    LiveEvidenceError,
    ReleaseAuthority,
    evaluate_issue_221,
)

REPOSITORY = "dream-xin/ai-sdlc"
FEATURE = "F-OPERATOR-V03-REAL-RUNTIME-FI-0001"
REF = "verification/v0.3-real-runtime-fixture-221"
MAIN = "1" * 40
MATERIALIZATION = "2" * 40
POLICY = "3" * 64


def require(value, message):
    if not value:
        raise AssertionError(message)


def authority_document():
    return {
        "schema_version": "ai-sdlc.v03-effect-safety-live-authority/v1",
        "repository": REPOSITORY,
        "feature_id": FEATURE,
        "target_ref": REF,
        "trusted_main_head_sha": MAIN,
        "materialization_commit_sha": MATERIALIZATION,
        "policy_bundle_digest": POLICY,
        "runtime_kind": "gh-aw-actions",
        "protected_policy_status": "PROTECTED",
        "effect_lineage_required": True,
        "writer_fence_quiesced": True,
    }


def raw(document):
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def provenance(document, *, record_id, run_id, evidence_class="release-live-real-runtime"):
    payload = raw(document)
    return {
        "schema_version": "ai-sdlc.v03-live-evidence-provenance/v1",
        "evidence_class": evidence_class,
        "record_id": record_id,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "github_workflow_run_id": run_id,
        "trusted_main_head_sha": MAIN,
        "repository": REPOSITORY,
        "feature_id": FEATURE,
        "target_ref": REF,
        "materialization_commit_sha": MATERIALIZATION,
        "policy_bundle_digest": POLICY,
        "runtime_kind": "gh-aw-actions",
        "protected_policy_status": "PROTECTED",
        "effect_lineage_required": True,
        "writer_fence_quiesced": True,
    }


def row(document, *, record_id, run_id, evidence_class="release-live-real-runtime"):
    return (
        raw(document),
        document,
        provenance(document, record_id=record_id, run_id=run_id, evidence_class=evidence_class),
    )


def lost_ack_pending():
    return {
        "schema_version": "ai-sdlc.v03-live-lost-ack/v1",
        "scenario": "lost-ack-crash-takeover",
        "status": "PENDING",
        "phase_status": "PASS",
        "remaining_release_proof": [
            "exact first-attempt Worker result correlation",
            "Feature Persist at most once",
        ],
        "binding": {
            "operation_id": "op-lost-ack",
            "semantic_effect_key": "sem-lost-ack",
            "external_dispatch_key": "ext-lost-ack",
        },
        "phase2": {"generation": 1},
        "duplicate_external_effect_count": 0,
        "speculative_retry_under_unknown": 0,
        "overall_issue_221_pass": False,
    }


def combined_lost_ack_persist():
    return {
        "schema_version": "ai-sdlc.v03-live-persist-ack-loss/v1",
        "scenario": "persist-ack-loss-fresh-process-recovery",
        "completed_issue_221_scenarios": [
            "lost-ack-crash-takeover",
            "persist-ack-loss-recovery",
        ],
        "lost_ack_crash_takeover_status": "PASS",
        "persist_ack_loss_recovery_status": "PASS",
        "status": "PASS",
        "operation_id": "op-lost-ack",
        "operation_generation": 1,
        "semantic_effect_key": "sem-lost-ack",
        "external_dispatch_key": "ext-lost-ack",
        "runtime_receipt_identity": "101",
        "reviewer_run_id": 101,
        "callback_id": "callback-1",
        "feature_event_id": "event-1",
        "feature_revision_before": 11,
        "feature_revision_after": 12,
        "fresh_retry_write_count": 0,
        "external_runtime_execution_count": 1,
        "feature_persist_count": 1,
        "duplicate_external_effect_count": 0,
        "duplicate_feature_write_count": 0,
        "unauthorized_lifecycle_transition_count": 0,
        "speculative_retry_under_unknown_count": 0,
        "overall_issue_221_pass": False,
    }


def generic_scenario(scenario, index):
    state = "LAUNCHED"
    receipt = f"run-{1000 + index}"
    if scenario == "cancellation-before-launch-authorization":
        state, receipt = "NOT_LAUNCHED", None
    elif scenario == "unknown-takeover":
        state, receipt = "UNKNOWN", None
    measurements = {name: 0 for name in SCENARIO_MEASUREMENTS[scenario]}
    return {
        "schema_version": "ai-sdlc.v03-effect-safety-live-scenario/v1",
        "status": "PASS",
        "completed_issue_221_scenarios": [scenario],
        "operation_id": f"op-{index}",
        "operation_generation": index % 3,
        "semantic_effect_key": f"sem-{index}",
        "external_dispatch_key": f"ext-{index}",
        "candidate_head_sha": f"{(index % 9) + 1}" * 40,
        "feature_revision_before": 20 + index,
        "runtime_receipt_identity": receipt,
        "runtime_lookup_state": state,
        "measurements": measurements,
        "overall_issue_221_pass": False,
    }


def expect_error(fn, contains):
    try:
        fn()
    except LiveEvidenceError as exc:
        require(contains in str(exc), f"wrong failure: {exc}")
    else:
        raise AssertionError(f"expected LiveEvidenceError containing {contains!r}")


def validate_empty_and_takeover_only_remain_pending():
    authority = ReleaseAuthority.from_document(authority_document())
    empty = evaluate_issue_221(authority=authority, evidence=[])
    require(empty["status"] == "PENDING", "empty ledger overclaimed PASS")
    require(empty["unresolved_scenarios"] == list(REQUIRED_SCENARIOS), "empty ledger lost closed scenario set")

    pending = lost_ack_pending()
    ledger = evaluate_issue_221(
        authority=authority,
        evidence=[row(pending, record_id="lost-ack-phase", run_id=100)],
    )
    require(ledger["status"] == "PENDING", "takeover-only record overclaimed full scenario")
    require("lost-ack-crash-takeover" in ledger["unresolved_scenarios"], "PENDING lost-ACK phase incorrectly satisfied row")


def validate_305_satisfies_only_two_rows():
    authority = ReleaseAuthority.from_document(authority_document())
    pending = lost_ack_pending()
    combined = combined_lost_ack_persist()
    ledger = evaluate_issue_221(
        authority=authority,
        evidence=[
            row(pending, record_id="lost-ack-phase", run_id=100),
            row(combined, record_id="combined-result-persist", run_id=101),
        ],
    )
    require(ledger["status"] == "PENDING", "two live scenarios overclaimed overall Issue #221 PASS")
    require(
        ledger["satisfied_scenarios"] == ["lost-ack-crash-takeover", "persist-ack-loss-recovery"],
        "combined record satisfied unexpected rows",
    )
    require(len(ledger["unresolved_scenarios"]) == len(REQUIRED_SCENARIOS) - 2, "wrong unresolved row count")
    require(ledger["deterministic_evidence_accepted"] is False, "ledger exposed deterministic substitution")


def validate_fail_closed_provenance_and_measurements():
    authority = ReleaseAuthority.from_document(authority_document())
    combined = combined_lost_ack_persist()

    expect_error(
        lambda: evaluate_issue_221(
            authority=authority,
            evidence=[row(combined, record_id="deterministic", run_id=200, evidence_class="deterministic-support")],
        ),
        "deterministic/non-live",
    )

    bad_digest = row(combined, record_id="bad-digest", run_id=201)
    bad_digest[2]["artifact_sha256"] = "0" * 64
    expect_error(
        lambda: evaluate_issue_221(authority=authority, evidence=[bad_digest]),
        "artifact digest",
    )

    bad_authority = row(combined, record_id="bad-authority", run_id=202)
    bad_authority[2]["trusted_main_head_sha"] = "9" * 40
    expect_error(
        lambda: evaluate_issue_221(authority=authority, evidence=[bad_authority]),
        "authority mismatch",
    )

    bad_counter = copy.deepcopy(combined)
    bad_counter["duplicate_external_effect_count"] = 1
    expect_error(
        lambda: evaluate_issue_221(
            authority=authority,
            evidence=[row(bad_counter, record_id="bad-counter", run_id=203)],
        ),
        "non-zero",
    )

    overclaim = copy.deepcopy(combined)
    overclaim["overall_issue_221_pass"] = True
    expect_error(
        lambda: evaluate_issue_221(
            authority=authority,
            evidence=[row(overclaim, record_id="overclaim", run_id=204)],
        ),
        "overall Issue #221 PASS",
    )


def validate_only_closed_complete_live_set_can_pass():
    authority = ReleaseAuthority.from_document(authority_document())
    combined = combined_lost_ack_persist()
    rows = [row(combined, record_id="combined-result-persist", run_id=300)]
    index = 1
    for scenario in REQUIRED_SCENARIOS:
        if scenario in {"lost-ack-crash-takeover", "persist-ack-loss-recovery"}:
            continue
        document = generic_scenario(scenario, index)
        rows.append(row(document, record_id=f"record-{scenario}", run_id=300 + index))
        index += 1
    ledger = evaluate_issue_221(authority=authority, evidence=rows)
    require(ledger["status"] == "PASS", "closed complete live scenario set did not PASS")
    require(ledger["overall_issue_221_pass"] is True, "complete ledger did not produce overall Issue #221 PASS")
    require(ledger["unresolved_scenarios"] == [], "complete ledger retained unresolved scenarios")
    require(ledger["satisfied_scenarios"] == list(REQUIRED_SCENARIOS), "complete ledger scenario order/set drifted")
    require(set(ledger["observed_zero_measurements"]) == set().union(*SCENARIO_MEASUREMENTS.values()), "complete ledger lacks global safety measurement coverage")


def validate_duplicate_scenario_claim_is_ambiguous():
    authority = ReleaseAuthority.from_document(authority_document())
    first = generic_scenario("duplicate-callback", 1)
    second = generic_scenario("duplicate-callback", 2)
    expect_error(
        lambda: evaluate_issue_221(
            authority=authority,
            evidence=[
                row(first, record_id="duplicate-callback-a", run_id=401),
                row(second, record_id="duplicate-callback-b", run_id=402),
            ],
        ),
        "ambiguous multiple",
    )


def main():
    validate_empty_and_takeover_only_remain_pending()
    validate_305_satisfies_only_two_rows()
    validate_fail_closed_provenance_and_measurements()
    validate_only_closed_complete_live_set_can_pass()
    validate_duplicate_scenario_claim_is_ambiguous()
    print("PASS: Issue #221 live evidence ledger is closed, provenance-bound, anti-overclaim and deterministic-proof rejecting")


if __name__ == "__main__":
    main()
