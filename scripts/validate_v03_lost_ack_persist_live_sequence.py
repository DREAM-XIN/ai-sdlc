#!/usr/bin/env python3
"""Zero-effect integration validation for the combined live #221 sequence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from operator_store_protection import ProtectionReceipt
import v03_lost_ack_persist_live_sequence as subject

REPOSITORY = "dream-xin/ai-sdlc"
FEATURE = "F-OPERATOR-V03-REAL-RUNTIME-FI-0001"
REF = "verification/v0.3-real-runtime-fixture-221"
MAIN = "1" * 40
MATERIALIZATION = "2" * 40
POLICY = "3" * 64


def require(value, message):
    if not value:
        raise AssertionError(message)


def preflight():
    receipt = ProtectionReceipt(
        repository=REPOSITORY,
        state_ref="refs/heads/ai-sdlc-operator-state",
        status="PROTECTED",
        verifier_identity="sequence-test-verifier",
        verified_at="2026-08-18T00:00:00Z",
        policy_digest="4" * 64,
    )
    config = SimpleNamespace(effect_lineage_required=True, old_writers_quiesced=True)
    executor = SimpleNamespace(base=SimpleNamespace(config=config))
    composition = SimpleNamespace(
        feature_id=FEATURE,
        target_ref=REF,
        bundle=SimpleNamespace(executor=executor),
    )
    policy = SimpleNamespace(
        installation_commit_sha=MAIN,
        materialization_commit_sha=MATERIALIZATION,
        bundle_digest=POLICY,
    )
    return SimpleNamespace(
        execution=SimpleNamespace(
            repository=REPOSITORY,
            installation_commit_sha=MAIN,
            state_ref="refs/heads/ai-sdlc-operator-state",
        ),
        live_authority=SimpleNamespace(
            materialization_commit_sha=MATERIALIZATION,
            protection_receipt=receipt,
            policy=policy,
        ),
        composition=composition,
    )


def combined_document():
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
        "operation_id": "op-sequence",
        "operation_generation": 1,
        "semantic_effect_key": "sem-sequence",
        "external_dispatch_key": "ext-sequence",
        "runtime_receipt_identity": "9001",
        "reviewer_run_id": 9001,
        "callback_id": "callback-sequence",
        "feature_event_id": "event-sequence",
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


def validate_lost_ack_takeover_must_remain_pending():
    accepted = {
        "schema_version": "ai-sdlc.v03-live-lost-ack/v1",
        "phase_status": "PASS",
        "status": "PENDING",
        "overall_issue_221_pass": False,
    }
    require(
        subject.run_lost_ack_takeover(preflight=object(), phase2_fn=lambda **_: accepted) is accepted,
        "sequence rejected exact takeover phase",
    )
    for bad in (
        {**accepted, "status": "PASS"},
        {**accepted, "phase_status": "PENDING"},
        {**accepted, "overall_issue_221_pass": True},
    ):
        try:
            subject.run_lost_ack_takeover(preflight=object(), phase2_fn=lambda **_: bad)
        except subject.V03LiveSequenceError:
            pass
        else:
            raise AssertionError("sequence accepted lost-ACK takeover overclaim")


def validate_finalization_writes_exact_envelope_and_partial_ledger():
    fixture = preflight()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_path = root / "combined.json"
        authority_path = root / "authority.json"
        provenance_path = root / "provenance.json"
        ledger_path = root / "ledger.json"

        def phase2_fn(*, preflight, final_path):
            document = combined_document()
            final_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return document

        result = subject.finalize_persist_recovery(
            preflight=fixture,
            github_workflow_run_id="32100999999",
            workflow_sha=MAIN,
            phase2_fn=phase2_fn,
            evidence_path=evidence_path,
            authority_path=authority_path,
            provenance_path=provenance_path,
            ledger_path=ledger_path,
        )
        require(result["status"] == "PASS", "sequence finalization did not PASS")
        require(result["overall_issue_221_pass"] is False, "sequence overclaimed overall Issue #221")
        require(result["unresolved_scenario_count"] == 11, "sequence did not retain eleven unresolved rows")

        authority = json.loads(authority_path.read_text())
        provenance = json.loads(provenance_path.read_text())
        ledger = json.loads(ledger_path.read_text())
        raw = evidence_path.read_bytes()
        require(authority["trusted_main_head_sha"] == MAIN, "sequence authority lost exact main")
        require(provenance["github_workflow_run_id"] == 32100999999, "sequence provenance lost exact workflow run")
        require(provenance["artifact_sha256"] == hashlib.sha256(raw).hexdigest(), "sequence provenance lost exact artifact bytes")
        require(ledger["status"] == "PENDING", "two-scenario sequence ledger overclaimed PASS")
        require(ledger["satisfied_scenarios"] == subject.EXPECTED_SATISFIED, "sequence ledger satisfied unexpected rows")
        require(len(ledger["unresolved_scenarios"]) == 11, "sequence partial ledger unresolved set drifted")


def validate_finalization_rejects_scenario_or_ledger_overclaim():
    fixture = preflight()
    with TemporaryDirectory() as directory:
        root = Path(directory)

        def wrong_claim(*, preflight, final_path):
            document = combined_document()
            document["completed_issue_221_scenarios"] = ["lost-ack-crash-takeover"]
            final_path.write_text(json.dumps(document), encoding="utf-8")
            return document

        try:
            subject.finalize_persist_recovery(
                preflight=fixture,
                github_workflow_run_id=1,
                workflow_sha=MAIN,
                phase2_fn=wrong_claim,
                evidence_path=root / "bad.json",
                authority_path=root / "authority.json",
                provenance_path=root / "provenance.json",
                ledger_path=root / "ledger.json",
            )
        except subject.V03LiveSequenceError:
            pass
        else:
            raise AssertionError("sequence accepted wrong completed scenario set")

        def good_phase(*, preflight, final_path):
            document = combined_document()
            final_path.write_text(json.dumps(document), encoding="utf-8")
            return document

        fake_pass = {
            "status": "PASS",
            "overall_issue_221_pass": True,
            "satisfied_scenarios": list(subject.EXPECTED_SATISFIED),
            "unresolved_scenarios": [],
        }
        try:
            subject.finalize_persist_recovery(
                preflight=fixture,
                github_workflow_run_id=2,
                workflow_sha=MAIN,
                phase2_fn=good_phase,
                ledger_evaluator=lambda **_: fake_pass,
                evidence_path=root / "good.json",
                authority_path=root / "authority2.json",
                provenance_path=root / "provenance2.json",
                ledger_path=root / "ledger2.json",
            )
        except subject.V03LiveSequenceError:
            pass
        else:
            raise AssertionError("sequence accepted a two-scenario ledger overclaim")


def main():
    validate_lost_ack_takeover_must_remain_pending()
    validate_finalization_writes_exact_envelope_and_partial_ledger()
    validate_finalization_rejects_scenario_or_ledger_overclaim()
    print("PASS: combined live sequence preserves four-process faults, exact provenance and 2-of-13 ledger scope")


if __name__ == "__main__":
    main()
