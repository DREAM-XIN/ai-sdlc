#!/usr/bin/env python3
"""Fail-closed release ledger for v0.3 Issue #221 real-runtime evidence.

The ledger never executes a Worker, touches the protected Store, or creates
Feature Events.  It accepts only exact live artifact bytes plus a separately
bound trusted provenance envelope.  Deterministic/unit evidence is deliberately
not representable as release completion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_SCENARIOS = (
    "lost-ack-crash-takeover",
    "cancellation-before-launch-authorization",
    "launch-authorization-before-cancellation",
    "cancel-before-persist-linearization",
    "persist-linearized-before-cancel",
    "persist-ack-loss-recovery",
    "unknown-takeover",
    "duplicate-callback",
    "out-of-order-callback",
    "duplicate-worker-completion",
    "concurrent-resume",
    "stale-candidate-result",
    "reservation-committed-pre-authorization-crash-recovery",
)

SCENARIO_MEASUREMENTS = {
    "lost-ack-crash-takeover": frozenset(
        {"duplicate_external_effect_count", "unauthorized_lifecycle_transition_count", "speculative_retry_under_unknown_count"}
    ),
    "cancellation-before-launch-authorization": frozenset(
        {"duplicate_external_effect_count", "unauthorized_lifecycle_transition_count"}
    ),
    "launch-authorization-before-cancellation": frozenset(
        {"duplicate_external_effect_count", "unauthorized_lifecycle_transition_count"}
    ),
    "cancel-before-persist-linearization": frozenset(
        {"duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "persist-linearized-before-cancel": frozenset(
        {"duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "persist-ack-loss-recovery": frozenset(
        {"duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "unknown-takeover": frozenset(
        {"duplicate_external_effect_count", "speculative_retry_under_unknown_count"}
    ),
    "duplicate-callback": frozenset(
        {"duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "out-of-order-callback": frozenset(
        {"duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "duplicate-worker-completion": frozenset(
        {"duplicate_external_effect_count", "duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "concurrent-resume": frozenset(
        {"duplicate_external_effect_count", "duplicate_feature_write_count", "unauthorized_lifecycle_transition_count"}
    ),
    "stale-candidate-result": frozenset(
        {"stale_evidence_accepted_count", "unauthorized_lifecycle_transition_count"}
    ),
    "reservation-committed-pre-authorization-crash-recovery": frozenset(
        {"duplicate_external_effect_count", "unauthorized_lifecycle_transition_count"}
    ),
}

ALL_SAFETY_MEASUREMENTS = frozenset().union(*SCENARIO_MEASUREMENTS.values())


class LiveEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseAuthority:
    repository: str
    feature_id: str
    target_ref: str
    trusted_main_head_sha: str
    materialization_commit_sha: str
    policy_bundle_digest: str
    runtime_kind: str = "gh-aw-actions"
    protected_policy_status: str = "PROTECTED"
    effect_lineage_required: bool = True
    writer_fence_quiesced: bool = True

    @classmethod
    def from_document(cls, value: dict[str, Any]) -> "ReleaseAuthority":
        if not isinstance(value, dict) or value.get("schema_version") != "ai-sdlc.v03-effect-safety-live-authority/v1":
            raise LiveEvidenceError("invalid live authority document schema")
        authority = cls(
            repository=str(value.get("repository") or "").lower(),
            feature_id=str(value.get("feature_id") or ""),
            target_ref=str(value.get("target_ref") or ""),
            trusted_main_head_sha=str(value.get("trusted_main_head_sha") or ""),
            materialization_commit_sha=str(value.get("materialization_commit_sha") or ""),
            policy_bundle_digest=str(value.get("policy_bundle_digest") or ""),
            runtime_kind=str(value.get("runtime_kind") or ""),
            protected_policy_status=str(value.get("protected_policy_status") or ""),
            effect_lineage_required=value.get("effect_lineage_required") is True,
            writer_fence_quiesced=value.get("writer_fence_quiesced") is True,
        )
        if not authority.repository or "/" not in authority.repository:
            raise LiveEvidenceError("live authority repository is invalid")
        if not authority.feature_id or not authority.target_ref:
            raise LiveEvidenceError("live authority Feature/ref binding is incomplete")
        if not SHA40.fullmatch(authority.trusted_main_head_sha):
            raise LiveEvidenceError("live authority trusted-main SHA is invalid")
        if not SHA40.fullmatch(authority.materialization_commit_sha):
            raise LiveEvidenceError("live authority materialization commit is invalid")
        if not SHA256.fullmatch(authority.policy_bundle_digest):
            raise LiveEvidenceError("live authority policy bundle digest is invalid")
        if authority.runtime_kind != "gh-aw-actions":
            raise LiveEvidenceError("Issue #221 live authority must use the reviewed gh-aw Actions runtime")
        if authority.protected_policy_status != "PROTECTED":
            raise LiveEvidenceError("Issue #221 live authority is not stably PROTECTED")
        if not authority.effect_lineage_required or not authority.writer_fence_quiesced:
            raise LiveEvidenceError("Issue #221 live authority lacks lineage/fence prerequisites")
        return authority


@dataclass(frozen=True)
class EvidenceProvenance:
    record_id: str
    artifact_sha256: str
    github_workflow_run_id: int
    trusted_main_head_sha: str
    repository: str
    feature_id: str
    target_ref: str
    materialization_commit_sha: str
    policy_bundle_digest: str
    runtime_kind: str
    protected_policy_status: str
    effect_lineage_required: bool
    writer_fence_quiesced: bool


def _exact_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise LiveEvidenceError(f"{field} must be an exact integer >= {minimum}")
    return value


def _zero_measurement(value: Any, *, field: str) -> int:
    number = _exact_int(value, field=field, minimum=0)
    if number != 0:
        raise LiveEvidenceError(f"release safety measurement is non-zero: {field}={number}")
    return number


def _parse_provenance(
    value: dict[str, Any],
    *,
    raw_record: bytes,
    authority: ReleaseAuthority,
) -> EvidenceProvenance:
    if not isinstance(value, dict) or value.get("schema_version") != "ai-sdlc.v03-live-evidence-provenance/v1":
        raise LiveEvidenceError("invalid live evidence provenance schema")
    if value.get("evidence_class") != "release-live-real-runtime":
        raise LiveEvidenceError("deterministic/non-live evidence cannot satisfy Issue #221")
    digest = hashlib.sha256(raw_record).hexdigest()
    if value.get("artifact_sha256") != digest:
        raise LiveEvidenceError("live evidence artifact digest differs from trusted provenance")
    record_id = str(value.get("record_id") or "")
    if not record_id or len(record_id) > 160:
        raise LiveEvidenceError("live evidence provenance record_id is invalid")
    run_id = _exact_int(value.get("github_workflow_run_id"), field="github_workflow_run_id", minimum=1)
    provenance = EvidenceProvenance(
        record_id=record_id,
        artifact_sha256=digest,
        github_workflow_run_id=run_id,
        trusted_main_head_sha=str(value.get("trusted_main_head_sha") or ""),
        repository=str(value.get("repository") or "").lower(),
        feature_id=str(value.get("feature_id") or ""),
        target_ref=str(value.get("target_ref") or ""),
        materialization_commit_sha=str(value.get("materialization_commit_sha") or ""),
        policy_bundle_digest=str(value.get("policy_bundle_digest") or ""),
        runtime_kind=str(value.get("runtime_kind") or ""),
        protected_policy_status=str(value.get("protected_policy_status") or ""),
        effect_lineage_required=value.get("effect_lineage_required") is True,
        writer_fence_quiesced=value.get("writer_fence_quiesced") is True,
    )
    expected = {
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
    actual = asdict(provenance)
    for field, expected_value in expected.items():
        if actual[field] != expected_value:
            raise LiveEvidenceError(f"live evidence authority mismatch: {field}")
    return provenance


def _require_identity_fields(document: dict[str, Any]) -> None:
    operation_id = str(document.get("operation_id") or "")
    semantic_key = str(document.get("semantic_effect_key") or "")
    external_key = str(document.get("external_dispatch_key") or "")
    if not operation_id or not semantic_key or not external_key:
        raise LiveEvidenceError("live scenario lacks Operation/effect/external identity")
    _exact_int(document.get("operation_generation"), field="operation_generation", minimum=0)


def _normalize_304_pending(document: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    if document.get("status") != "PENDING" or document.get("phase_status") != "PASS":
        raise LiveEvidenceError("lost-ACK takeover phase must remain PENDING until result/Persist proof")
    if document.get("overall_issue_221_pass") is not False:
        raise LiveEvidenceError("takeover-only evidence attempted overall Issue #221 PASS")
    if document.get("remaining_release_proof") != [
        "exact first-attempt Worker result correlation",
        "Feature Persist at most once",
    ]:
        raise LiveEvidenceError("takeover-only evidence lost exact remaining release proof")
    binding = document.get("binding")
    if not isinstance(binding, dict):
        raise LiveEvidenceError("takeover-only evidence lacks exact binding")
    _require_identity_fields(
        {
            "operation_id": binding.get("operation_id"),
            "operation_generation": (document.get("phase2") or {}).get("generation"),
            "semantic_effect_key": binding.get("semantic_effect_key"),
            "external_dispatch_key": binding.get("external_dispatch_key"),
        }
    )
    _zero_measurement(document.get("duplicate_external_effect_count"), field="duplicate_external_effect_count")
    _zero_measurement(document.get("speculative_retry_under_unknown"), field="speculative_retry_under_unknown")
    # Deliberately no completed scenario claim.
    return tuple(), {}


def _normalize_305_combined(document: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    expected_claims = ("lost-ack-crash-takeover", "persist-ack-loss-recovery")
    claims = document.get("completed_issue_221_scenarios")
    if claims != list(expected_claims):
        raise LiveEvidenceError("Persist ACK-loss record has unexpected completed scenario set")
    if document.get("status") != "PASS":
        raise LiveEvidenceError("combined lost-ACK/Persist record is not PASS")
    if document.get("lost_ack_crash_takeover_status") != "PASS" or document.get("persist_ack_loss_recovery_status") != "PASS":
        raise LiveEvidenceError("combined record did not independently PASS both chained scenarios")
    if document.get("overall_issue_221_pass") is not False:
        raise LiveEvidenceError("two-scenario record attempted overall Issue #221 PASS")
    _require_identity_fields(document)
    if str(document.get("reviewer_run_id")) != str(document.get("runtime_receipt_identity")):
        raise LiveEvidenceError("combined record Reviewer run differs from durable runtime receipt")
    before = _exact_int(document.get("feature_revision_before"), field="feature_revision_before", minimum=0)
    after = _exact_int(document.get("feature_revision_after"), field="feature_revision_after", minimum=1)
    if after != before + 1:
        raise LiveEvidenceError("combined record Feature revision did not advance exactly once")
    if _exact_int(document.get("external_runtime_execution_count"), field="external_runtime_execution_count") != 1:
        raise LiveEvidenceError("combined record does not prove exactly one external runtime execution")
    if _exact_int(document.get("feature_persist_count"), field="feature_persist_count") != 1:
        raise LiveEvidenceError("combined record does not prove exactly one Feature Persist")
    measurements = {
        "duplicate_external_effect_count": _zero_measurement(
            document.get("duplicate_external_effect_count"), field="duplicate_external_effect_count"
        ),
        "duplicate_feature_write_count": _zero_measurement(
            document.get("duplicate_feature_write_count"), field="duplicate_feature_write_count"
        ),
        "unauthorized_lifecycle_transition_count": _zero_measurement(
            document.get("unauthorized_lifecycle_transition_count"), field="unauthorized_lifecycle_transition_count"
        ),
        "speculative_retry_under_unknown_count": _zero_measurement(
            document.get("speculative_retry_under_unknown_count"), field="speculative_retry_under_unknown_count"
        ),
    }
    if _exact_int(document.get("fresh_retry_write_count"), field="fresh_retry_write_count") != 0:
        raise LiveEvidenceError("combined record retried the Feature write after ACK loss")
    return expected_claims, measurements


def _normalize_generic(document: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    if document.get("status") != "PASS" or document.get("overall_issue_221_pass") is not False:
        raise LiveEvidenceError("generic scenario record must be scenario PASS and overall Issue #221 false")
    claims_value = document.get("completed_issue_221_scenarios")
    if not isinstance(claims_value, list) or not claims_value:
        raise LiveEvidenceError("generic live record lacks completed scenarios")
    if any(not isinstance(row, str) or row not in REQUIRED_SCENARIOS for row in claims_value):
        raise LiveEvidenceError("generic live record claims an unknown Issue #221 scenario")
    if len(claims_value) != len(set(claims_value)):
        raise LiveEvidenceError("generic live record repeats a scenario claim")
    _require_identity_fields(document)
    if "candidate_head_sha" not in document or not SHA40.fullmatch(str(document.get("candidate_head_sha") or "")):
        raise LiveEvidenceError("generic live record lacks exact candidate SHA")
    _exact_int(document.get("feature_revision_before"), field="feature_revision_before", minimum=0)
    if "runtime_receipt_identity" not in document:
        raise LiveEvidenceError("generic live record must explicitly record runtime receipt state/identity")
    lookup_state = document.get("runtime_lookup_state")
    if lookup_state not in {"NOT_LAUNCHED", "LAUNCHED", "UNKNOWN", "NOT_APPLICABLE"}:
        raise LiveEvidenceError("generic live record has invalid runtime lookup state")
    measurements_value = document.get("measurements")
    if not isinstance(measurements_value, dict):
        raise LiveEvidenceError("generic live record lacks measured safety counters")
    measurements: dict[str, int] = {}
    for name, value in measurements_value.items():
        if name not in ALL_SAFETY_MEASUREMENTS:
            raise LiveEvidenceError(f"generic live record contains unknown safety measurement: {name}")
        measurements[name] = _zero_measurement(value, field=name)
    claims = tuple(claims_value)
    for scenario in claims:
        missing = SCENARIO_MEASUREMENTS[scenario] - measurements.keys()
        if missing:
            raise LiveEvidenceError(
                f"scenario {scenario} lacks required live safety measurements: {','.join(sorted(missing))}"
            )
    return claims, measurements


def _normalize_record(document: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    if not isinstance(document, dict):
        raise LiveEvidenceError("live evidence artifact must be a JSON object")
    schema = document.get("schema_version")
    if schema == "ai-sdlc.v03-live-lost-ack/v1":
        return _normalize_304_pending(document)
    if schema == "ai-sdlc.v03-live-persist-ack-loss/v1":
        return _normalize_305_combined(document)
    if schema == "ai-sdlc.v03-effect-safety-live-scenario/v1":
        return _normalize_generic(document)
    raise LiveEvidenceError("unknown evidence schema cannot satisfy Issue #221")


def evaluate_issue_221(
    *,
    authority: ReleaseAuthority,
    evidence: Iterable[tuple[bytes, dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
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
        claims, measurements = _normalize_record(document)
        observed_measurements.update(measurements)
        for scenario in claims:
            if scenario in claimed_by:
                raise LiveEvidenceError(
                    f"scenario {scenario} has ambiguous multiple live evidence records"
                )
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


def _load_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise LiveEvidenceError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LiveEvidenceError(f"JSON root must be object: {path}")
    return raw, value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument(
        "--pair",
        action="append",
        default=[],
        metavar="RECORD::PROVENANCE",
        help="exact live evidence artifact and its trusted provenance envelope",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _, authority_document = _load_json(args.authority)
    authority = ReleaseAuthority.from_document(authority_document)
    rows = []
    for pair in args.pair:
        if "::" not in pair:
            raise SystemExit("--pair requires RECORD::PROVENANCE")
        record_name, provenance_name = pair.split("::", 1)
        raw, record = _load_json(Path(record_name))
        _, provenance = _load_json(Path(provenance_name))
        rows.append((raw, record, provenance))
    ledger = evaluate_issue_221(authority=authority, evidence=rows)
    text = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
