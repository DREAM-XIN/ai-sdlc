#!/usr/bin/env python3
"""Strict #221 ledger extension for the two launch/cancel ordering rows."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from v03_effect_safety_live_ledger import (
    ALL_SAFETY_MEASUREMENTS,
    REQUIRED_SCENARIOS,
    LiveEvidenceError,
    ReleaseAuthority,
    _exact_int,
    _normalize_generic,
    _normalize_record,
    _parse_provenance,
)

PAIR_SCHEMA = "ai-sdlc.v03-live-launch-cancel-pair/v1"
PAIR_SCENARIOS = (
    "cancellation-before-launch-authorization",
    "launch-authorization-before-cancellation",
)


def _detail_as_generic(detail: dict[str, Any], scenario: str) -> tuple[dict[str, Any], dict[str, int]]:
    if not isinstance(detail, dict) or detail.get("scenario") != scenario:
        raise LiveEvidenceError(f"launch/cancel pair lacks exact {scenario} detail")
    generic = {
        "schema_version": "ai-sdlc.v03-effect-safety-live-scenario/v1",
        "status": detail.get("status"),
        "completed_issue_221_scenarios": [scenario],
        "operation_id": detail.get("operation_id"),
        "operation_generation": detail.get("operation_generation"),
        "semantic_effect_key": detail.get("semantic_effect_key"),
        "external_dispatch_key": detail.get("external_dispatch_key"),
        "candidate_head_sha": detail.get("candidate_head_sha"),
        "feature_revision_before": detail.get("feature_revision_before"),
        "runtime_receipt_identity": detail.get("runtime_receipt_identity"),
        "runtime_lookup_state": detail.get("runtime_lookup_state"),
        "measurements": detail.get("measurements"),
        "overall_issue_221_pass": False,
    }
    claims, measurements = _normalize_generic(generic)
    if claims != (scenario,):
        raise LiveEvidenceError("launch/cancel detail normalized to unexpected scenario")
    return generic, measurements


def _exact_zero(value: Any, field: str) -> None:
    if _exact_int(value, field=field, minimum=0) != 0:
        raise LiveEvidenceError(f"launch/cancel pair expected zero {field}")


def _normalize_launch_cancel_pair(document: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    if not isinstance(document, dict) or document.get("schema_version") != PAIR_SCHEMA:
        raise LiveEvidenceError("invalid launch/cancel pair schema")
    if document.get("status") != "PASS":
        raise LiveEvidenceError("launch/cancel pair is not PASS")
    if document.get("completed_issue_221_scenarios") != list(PAIR_SCENARIOS):
        raise LiveEvidenceError("launch/cancel pair claims unexpected Issue #221 rows")
    if document.get("overall_issue_221_pass") is not False:
        raise LiveEvidenceError("launch/cancel pair attempted overall Issue #221 PASS")

    before = document.get("before_authorization")
    after = document.get("after_authorization")
    before_generic, before_measurements = _detail_as_generic(before, PAIR_SCENARIOS[0])
    after_generic, after_measurements = _detail_as_generic(after, PAIR_SCENARIOS[1])

    if before_generic["runtime_lookup_state"] != "NOT_LAUNCHED" or before_generic["runtime_receipt_identity"] is not None:
        raise LiveEvidenceError("cancel-before-authorization must prove NOT_LAUNCHED with no receipt")
    if after_generic["runtime_lookup_state"] != "LAUNCHED" or not str(after_generic["runtime_receipt_identity"] or ""):
        raise LiveEvidenceError("authorization-before-cancel must prove one exact LAUNCHED receipt")
    if before.get("final_status") != "CANCELLED" or after.get("final_status") != "CANCELLED":
        raise LiveEvidenceError("both launch/cancel pair Operations must finish CANCELLED")
    if before_generic["operation_id"] == after_generic["operation_id"]:
        raise LiveEvidenceError("launch/cancel pair requires distinct durable Operations")

    for field in ("semantic_effect_key", "external_dispatch_key", "candidate_head_sha", "feature_revision_before"):
        if before_generic[field] != after_generic[field]:
            raise LiveEvidenceError(f"launch/cancel pair drifted shared semantic identity: {field}")

    if _exact_int(before.get("external_runtime_execution_count"), field="before.external_runtime_execution_count") != 0:
        raise LiveEvidenceError("cancel-before-authorization touched external runtime")
    if _exact_int(after.get("external_runtime_execution_count"), field="after.external_runtime_execution_count") != 1:
        raise LiveEvidenceError("authorization-before-cancel did not create exactly one external runtime execution")
    if _exact_int(before.get("dispatch_claim_count"), field="before.dispatch_claim_count") != 1:
        raise LiveEvidenceError("cancel-before-authorization lacks exactly one dispatch claim")
    _exact_zero(before.get("launch_authorization_count"), "before.launch_authorization_count")
    _exact_zero(before.get("launch_lookup_count"), "before.launch_lookup_count")
    if _exact_int(after.get("dispatch_claim_count"), field="after.dispatch_claim_count") != 1:
        raise LiveEvidenceError("authorization-before-cancel lacks exactly one dispatch claim")
    if _exact_int(after.get("launch_authorization_count"), field="after.launch_authorization_count") != 1:
        raise LiveEvidenceError("authorization-before-cancel lacks exactly one launch authorization")
    if _exact_int(after.get("launch_lookup_count"), field="after.launch_lookup_count") != 1:
        raise LiveEvidenceError("authorization-before-cancel lacks exactly one receipt observation")
    _exact_zero(before.get("post_cancel_persist_authority_count"), "before.post_cancel_persist_authority_count")
    _exact_zero(after.get("post_cancel_persist_authority_count"), "after.post_cancel_persist_authority_count")

    claim_seq = _exact_int(after.get("claim_sequence"), field="after.claim_sequence", minimum=0)
    authorization_seq = _exact_int(after.get("authorization_sequence"), field="after.authorization_sequence", minimum=0)
    cancel_seq = _exact_int(after.get("cancel_sequence"), field="after.cancel_sequence", minimum=0)
    lookup_seq = _exact_int(after.get("lookup_sequence"), field="after.lookup_sequence", minimum=0)
    if not claim_seq < authorization_seq < cancel_seq < lookup_seq:
        raise LiveEvidenceError("authorization-before-cancel durable order is not claim -> authorization -> cancel -> lookup")

    measurements = dict(before_measurements)
    for name, value in after_measurements.items():
        if name in measurements and measurements[name] != value:
            raise LiveEvidenceError(f"launch/cancel pair has conflicting safety measurement: {name}")
        measurements[name] = value
    return PAIR_SCENARIOS, measurements


def evaluate_issue_221_with_launch_cancel(
    *,
    authority: ReleaseAuthority,
    evidence: Iterable[tuple[bytes, dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Core #306 evaluator plus one strictly validated launch/cancel pair schema."""
    claimed_by: dict[str, str] = {}
    record_ids: set[str] = set()
    run_ids: set[int] = set()
    observed_measurements: set[str] = set()

    for raw_record, document, provenance_document in evidence:
        provenance = _parse_provenance(
            provenance_document,
            raw_record=raw_record,
            authority=authority,
        )
        if provenance.record_id in record_ids:
            raise LiveEvidenceError("duplicate live evidence record_id")
        if provenance.github_workflow_run_id in run_ids:
            raise LiveEvidenceError("same workflow run was reused as multiple live evidence records")
        record_ids.add(provenance.record_id)
        run_ids.add(provenance.github_workflow_run_id)

        if isinstance(document, dict) and document.get("schema_version") == PAIR_SCHEMA:
            claims, measurements = _normalize_launch_cancel_pair(document)
        else:
            claims, measurements = _normalize_record(document)
        observed_measurements.update(measurements)
        for scenario in claims:
            if scenario in claimed_by:
                raise LiveEvidenceError(f"scenario {scenario} has ambiguous multiple live evidence records")
            claimed_by[scenario] = provenance.record_id

    satisfied = [row for row in REQUIRED_SCENARIOS if row in claimed_by]
    unresolved = [row for row in REQUIRED_SCENARIOS if row not in claimed_by]
    status = "PASS" if not unresolved else "PENDING"
    if status == "PASS":
        missing_global = ALL_SAFETY_MEASUREMENTS - observed_measurements
        if missing_global:
            raise LiveEvidenceError(
                "complete scenario set still lacks global live measurement coverage: "
                + ",".join(sorted(missing_global))
            )
    return {
        "schema_version": "ai-sdlc.v03-effect-safety-live-ledger/v1",
        "evaluator_profile": "launch-cancel-pair/v1",
        "issue": 221,
        "status": status,
        "overall_issue_221_pass": status == "PASS",
        "authority": asdict(authority),
        "required_scenarios": list(REQUIRED_SCENARIOS),
        "satisfied_scenarios": satisfied,
        "unresolved_scenarios": unresolved,
        "scenario_evidence": {row: claimed_by[row] for row in satisfied},
        "accepted_record_count": len(record_ids),
        "accepted_workflow_run_count": len(run_ids),
        "observed_zero_measurements": sorted(observed_measurements),
        "deterministic_evidence_accepted": False,
    }
