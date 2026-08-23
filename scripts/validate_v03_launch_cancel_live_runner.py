#!/usr/bin/env python3
"""Zero-effect validation for the live launch/cancel pair runner and ledger adapter."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from v03_effect_safety_live_ledger import ReleaseAuthority
from v03_effect_safety_live_ledger_launch_cancel import (
    PAIR_SCENARIOS,
    LiveEvidenceError,
    evaluate_issue_221_with_launch_cancel,
)
import v03_launch_cancel_live_runner as subject

REPOSITORY = "dream-xin/ai-sdlc"
FEATURE = "F-OPERATOR-V03-REAL-RUNTIME-FI-0001"
REF = "verification/v0.3-real-runtime-fixture-221"
MAIN = "1" * 40
MATERIALIZATION = "2" * 40
POLICY = "3" * 64
SEMANTIC = "sem-qa"
EXTERNAL = "ext-qa"
CANDIDATE = "4" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def authority():
    return ReleaseAuthority.from_document(
        {
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
    )


def detail_before():
    return {
        "status": "PASS",
        "scenario": PAIR_SCENARIOS[0],
        "operation_id": "op-before",
        "operation_generation": 1,
        "semantic_effect_key": SEMANTIC,
        "external_dispatch_key": EXTERNAL,
        "candidate_head_sha": CANDIDATE,
        "feature_revision_before": 13,
        "runtime_lookup_state": "NOT_LAUNCHED",
        "runtime_receipt_identity": None,
        "final_status": "CANCELLED",
        "dispatch_claim_count": 1,
        "launch_authorization_count": 0,
        "launch_lookup_count": 0,
        "external_runtime_execution_count": 0,
        "setup_feature_persist_count": 1,
        "post_cancel_persist_authority_count": 0,
        "claim_sequence": 20,
        "cancel_sequence": 21,
        "measurements": {
            "duplicate_external_effect_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
    }


def detail_after():
    return {
        "status": "PASS",
        "scenario": PAIR_SCENARIOS[1],
        "operation_id": "op-after",
        "operation_generation": 0,
        "semantic_effect_key": SEMANTIC,
        "external_dispatch_key": EXTERNAL,
        "candidate_head_sha": CANDIDATE,
        "feature_revision_before": 13,
        "runtime_lookup_state": "LAUNCHED",
        "runtime_receipt_identity": "run-qa-1",
        "final_status": "CANCELLED",
        "dispatch_claim_count": 1,
        "launch_authorization_count": 1,
        "launch_lookup_count": 1,
        "external_runtime_execution_count": 1,
        "post_cancel_persist_authority_count": 0,
        "claim_sequence": 2,
        "authorization_sequence": 3,
        "cancel_sequence": 4,
        "lookup_sequence": 5,
        "measurements": {
            "duplicate_external_effect_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
    }


def pair_document():
    return {
        "schema_version": "ai-sdlc.v03-live-launch-cancel-pair/v1",
        "status": "PASS",
        "completed_issue_221_scenarios": list(PAIR_SCENARIOS),
        "before_authorization": detail_before(),
        "after_authorization": detail_after(),
        "overall_issue_221_pass": False,
    }


def provenance(document):
    raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    return raw, {
        "schema_version": "ai-sdlc.v03-live-evidence-provenance/v1",
        "evidence_class": "release-live-real-runtime",
        "record_id": "launch-cancel-pair-live",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "github_workflow_run_id": 32101000001,
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


def expect_ledger_error(document, contains):
    raw, proof = provenance(document)
    try:
        evaluate_issue_221_with_launch_cancel(
            authority=authority(),
            evidence=[(raw, document, proof)],
        )
    except LiveEvidenceError as exc:
        require(contains in str(exc), f"wrong pair failure: {exc}")
    else:
        raise AssertionError(f"expected launch/cancel ledger failure containing {contains!r}")


def validate_pair_ledger_is_strict_and_partial():
    document = pair_document()
    raw, proof = provenance(document)
    ledger = evaluate_issue_221_with_launch_cancel(
        authority=authority(),
        evidence=[(raw, document, proof)],
    )
    require(ledger["status"] == "PENDING", "two cancellation rows overclaimed Issue #221 PASS")
    require(ledger["satisfied_scenarios"] == list(PAIR_SCENARIOS), "pair satisfied unexpected rows")
    require(len(ledger["unresolved_scenarios"]) == 11, "pair did not retain eleven unresolved rows")

    bad = pair_document()
    bad["before_authorization"]["runtime_lookup_state"] = "LAUNCHED"
    expect_ledger_error(bad, "must prove NOT_LAUNCHED")
    bad = pair_document()
    bad["after_authorization"]["external_dispatch_key"] = "different-key"
    expect_ledger_error(bad, "shared semantic identity")
    bad = pair_document()
    bad["after_authorization"]["external_runtime_execution_count"] = 2
    expect_ledger_error(bad, "exactly one external runtime execution")
    bad = pair_document()
    bad["after_authorization"]["cancel_sequence"] = 6
    bad["after_authorization"]["lookup_sequence"] = 5
    expect_ledger_error(bad, "claim -> authorization -> cancel -> lookup")
    bad = pair_document()
    bad["after_authorization"]["measurements"]["duplicate_external_effect_count"] = 1
    expect_ledger_error(bad, "non-zero")


def validate_no_external_gateway_is_fail_closed():
    delegate = SimpleNamespace()
    gateway = subject.NoExternalDispatchGateway(delegate)
    for method, kwargs in (
        (gateway.launch, {"dispatch": {}}),
        (gateway.lookup, {"external_dispatch_key": EXTERNAL}),
    ):
        try:
            method(**kwargs)
        except subject.V03LaunchCancelLiveError:
            pass
        else:
            raise AssertionError("cancel-before-authorization fence allowed external transport")
    require(gateway.launch_calls == 1 and gateway.lookup_calls == 1, "external fence did not account attempted escape")


def validate_cancel_after_new_claim_injects_once():
    original_events = subject.operation_events
    original_cancel = subject.plan_cancel
    commits = []
    claim = {
        "event_type": "dispatch.claimed",
        "payload": {
            "claim_id": "claim-new",
            "semantic_effect_key": SEMANTIC,
            "external_dispatch_key": EXTERNAL,
        },
    }
    subject.operation_events = lambda snapshot, operation_id: [claim]
    subject.plan_cancel = lambda snapshot, **kwargs: {"cancel": kwargs}

    class Runtime:
        backend = SimpleNamespace(read_snapshot=lambda: object())

        @staticmethod
        def commit_replanned(planner):
            commits.append(planner(object()))
            return commits[-1]

    delegate = SimpleNamespace(read_feature=lambda *, operation_id: ("feature", operation_id))
    gateway = subject.CancelAfterNewClaimFeatureGateway(
        delegate=delegate,
        runtime=Runtime(),
        clock=lambda: "2026-08-18T00:00:00Z",
        trusted_context_digest="trusted",
        operation_id="op-before",
        baseline_claim_ids={"claim-old"},
    )
    try:
        require(gateway.read_feature(operation_id="op-before") == ("feature", "op-before"), "feature delegate result changed")
        require(gateway.injected_claim["claim_id"] == "claim-new", "claim fence did not bind exact new claim")
        require(len(commits) == 1, "claim fence did not inject exactly one durable cancel")
        gateway.read_feature(operation_id="op-before")
        require(len(commits) == 1, "claim fence injected cancellation twice")
    finally:
        subject.operation_events = original_events
        subject.plan_cancel = original_cancel


def validate_authorized_gateway_launches_once_then_cancels_before_return():
    original_events = subject.operation_events
    original_cancel = subject.plan_cancel
    commits = []
    subject.operation_events = lambda snapshot, operation_id: [
        {
            "event_type": "dispatch.launch.authorized",
            "operation_generation": 0,
            "payload": {"external_dispatch_key": EXTERNAL},
        }
    ]
    subject.plan_cancel = lambda snapshot, **kwargs: {"cancel": kwargs}

    class Runtime:
        backend = SimpleNamespace(read_snapshot=lambda: object())

        @staticmethod
        def commit_replanned(planner):
            commits.append(planner(object()))
            return commits[-1]

    class Delegate:
        def __init__(self):
            self.launches = 0
            self.lookups = 0

        def launch(self, *, dispatch):
            self.launches += 1
            return {"lookup_state": "LAUNCHED", "receipt_id": "run-qa-1"}

        def lookup(self, *, external_dispatch_key):
            self.lookups += 1
            return {"lookup_state": "LAUNCHED", "receipt_id": "run-qa-1"}

    delegate = Delegate()
    gateway = subject.CancelAfterAuthorizedProductionGateway(
        delegate=delegate,
        runtime=Runtime(),
        clock=lambda: "2026-08-18T00:00:00Z",
        trusted_context_digest="trusted",
        expected_operation_id="op-after",
        expected_semantic_effect_key=SEMANTIC,
        expected_external_dispatch_key=EXTERNAL,
    )
    dispatch = {
        "operation_id": "op-after",
        "operation_generation": 0,
        "semantic_effect_key": SEMANTIC,
        "external_dispatch_key": EXTERNAL,
    }
    try:
        receipt = gateway.launch(dispatch=dispatch)
        require(receipt["receipt_id"] == "run-qa-1", "authorized wrapper changed real receipt")
        require(delegate.launches == 1 and gateway.launch_calls == 1, "authorized wrapper did not launch exactly once")
        require(len(commits) == 1, "authorized wrapper did not durably cancel exactly once")
        try:
            gateway.launch(dispatch=dispatch)
        except subject.V03LaunchCancelLiveError:
            require(delegate.launches == 1, "duplicate launch reached production delegate")
        else:
            raise AssertionError("authorized wrapper allowed a duplicate launch")
    finally:
        subject.operation_events = original_events
        subject.plan_cancel = original_cancel


def main():
    validate_pair_ledger_is_strict_and_partial()
    validate_no_external_gateway_is_fail_closed()
    validate_cancel_after_new_claim_injects_once()
    validate_authorized_gateway_launches_once_then_cancels_before_return()
    print("PASS: launch/cancel pair uses one shared semantic effect, strict ordering, and fail-closed transport fences")


if __name__ == "__main__":
    main()
