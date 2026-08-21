#!/usr/bin/env python3
"""Closed multi-Feature authority-set ledger for final v0.3 Issue #221 evidence.

The original four live rows use the single release fixture.  The remaining nine
rows deliberately use the fixed #310 fixture pool, one Feature/ref per destructive
scenario.  This adapter keeps the #306 record/provenance validation rules while
binding each scenario to exactly its authorized Feature/ref.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable

from provision_v03_real_runtime_fixture import FEATURE_ID as ORIGINAL_FEATURE_ID, TARGET_REF as ORIGINAL_TARGET_REF
from v03_effect_safety_live_ledger import (
    ALL_SAFETY_MEASUREMENTS,
    REQUIRED_SCENARIOS,
    LiveEvidenceError,
    ReleaseAuthority,
    _normalize_record,
    _parse_provenance,
)
from v03_effect_safety_live_ledger_launch_cancel import PAIR_SCHEMA, _normalize_launch_cancel_pair
from v03_scenario_fixture_pool import SLOTS, inventory_document

AUTHORITY_SET_SCHEMA = "ai-sdlc.v03-effect-safety-live-authority-set/v1"
LEDGER_SCHEMA = "ai-sdlc.v03-effect-safety-live-authority-set-ledger/v1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ORIGINAL_FIXTURE_SCENARIOS = frozenset({
    "lost-ack-crash-takeover",
    "persist-ack-loss-recovery",
    "cancellation-before-launch-authorization",
    "launch-authorization-before-cancellation",
})


def expected_scenario_bindings() -> dict[str, dict[str, str]]:
    bindings = {
        scenario: {"feature_id": ORIGINAL_FEATURE_ID, "target_ref": ORIGINAL_TARGET_REF}
        for scenario in ORIGINAL_FIXTURE_SCENARIOS
    }
    bindings.update({
        slot.scenario: {"feature_id": slot.feature_id, "target_ref": slot.target_ref}
        for slot in SLOTS
    })
    if set(bindings) != set(REQUIRED_SCENARIOS):
        raise LiveEvidenceError("authority-set scenario inventory differs from closed Issue #221 matrix")
    return {scenario: bindings[scenario] for scenario in REQUIRED_SCENARIOS}


@dataclass(frozen=True)
class ReleaseAuthoritySet:
    repository: str
    trusted_main_head_sha: str
    materialization_commit_sha: str
    policy_bundle_digest: str
    fixture_pool_inventory_digest: str
    scenario_bindings: dict[str, dict[str, str]]
    runtime_kind: str = "gh-aw-actions"
    protected_policy_status: str = "PROTECTED"
    effect_lineage_required: bool = True
    writer_fence_quiesced: bool = True

    @classmethod
    def from_document(cls, value: dict[str, Any]) -> "ReleaseAuthoritySet":
        if not isinstance(value, dict) or value.get("schema_version") != AUTHORITY_SET_SCHEMA:
            raise LiveEvidenceError("invalid live authority-set schema")
        raw_bindings = value.get("scenario_bindings")
        if not isinstance(raw_bindings, dict):
            raise LiveEvidenceError("live authority-set lacks scenario bindings")
        normalized: dict[str, dict[str, str]] = {}
        for scenario, binding in raw_bindings.items():
            if not isinstance(scenario, str) or not isinstance(binding, dict):
                raise LiveEvidenceError("live authority-set contains malformed scenario binding")
            feature_id = str(binding.get("feature_id") or "")
            target_ref = str(binding.get("target_ref") or "")
            if not feature_id or not target_ref or set(binding) != {"feature_id", "target_ref"}:
                raise LiveEvidenceError("live authority-set contains incomplete scenario binding")
            normalized[scenario] = {"feature_id": feature_id, "target_ref": target_ref}
        expected = expected_scenario_bindings()
        if normalized != expected:
            raise LiveEvidenceError("live authority-set bindings differ from frozen original/#310 inventory")
        authority = cls(
            repository=str(value.get("repository") or "").lower(),
            trusted_main_head_sha=str(value.get("trusted_main_head_sha") or "").lower(),
            materialization_commit_sha=str(value.get("materialization_commit_sha") or "").lower(),
            policy_bundle_digest=str(value.get("policy_bundle_digest") or "").lower(),
            fixture_pool_inventory_digest=str(value.get("fixture_pool_inventory_digest") or "").lower(),
            scenario_bindings=normalized,
            runtime_kind=str(value.get("runtime_kind") or ""),
            protected_policy_status=str(value.get("protected_policy_status") or ""),
            effect_lineage_required=value.get("effect_lineage_required") is True,
            writer_fence_quiesced=value.get("writer_fence_quiesced") is True,
        )
        if not authority.repository or "/" not in authority.repository:
            raise LiveEvidenceError("authority-set repository is invalid")
        if not _SHA40.fullmatch(authority.trusted_main_head_sha):
            raise LiveEvidenceError("authority-set trusted-main SHA is invalid")
        if not _SHA40.fullmatch(authority.materialization_commit_sha):
            raise LiveEvidenceError("authority-set materialization SHA is invalid")
        if not _SHA256.fullmatch(authority.policy_bundle_digest):
            raise LiveEvidenceError("authority-set policy digest is invalid")
        expected_inventory = inventory_document()["inventory_digest"]
        if authority.fixture_pool_inventory_digest != expected_inventory:
            raise LiveEvidenceError("authority-set fixture-pool inventory digest drifted")
        if authority.runtime_kind != "gh-aw-actions":
            raise LiveEvidenceError("authority-set runtime must be reviewed gh-aw Actions")
        if authority.protected_policy_status != "PROTECTED":
            raise LiveEvidenceError("authority-set protected policy is not PROTECTED")
        if not authority.effect_lineage_required or not authority.writer_fence_quiesced:
            raise LiveEvidenceError("authority-set lacks lineage/writer-fence prerequisites")
        return authority

    def authority_for(self, scenario: str) -> ReleaseAuthority:
        binding = self.scenario_bindings.get(scenario)
        if scenario not in REQUIRED_SCENARIOS or binding is None:
            raise LiveEvidenceError("scenario is outside closed authority set")
        return ReleaseAuthority.from_document({
            "schema_version": "ai-sdlc.v03-effect-safety-live-authority/v1",
            "repository": self.repository,
            "feature_id": binding["feature_id"],
            "target_ref": binding["target_ref"],
            "trusted_main_head_sha": self.trusted_main_head_sha,
            "materialization_commit_sha": self.materialization_commit_sha,
            "policy_bundle_digest": self.policy_bundle_digest,
            "runtime_kind": self.runtime_kind,
            "protected_policy_status": self.protected_policy_status,
            "effect_lineage_required": self.effect_lineage_required,
            "writer_fence_quiesced": self.writer_fence_quiesced,
        })


def authority_set_document(*, authority: ReleaseAuthority, fixture_pool_inventory_digest: str) -> dict[str, Any]:
    """Expand one common trusted-main/policy anchor into the frozen 13-scenario authority set."""
    if fixture_pool_inventory_digest != inventory_document()["inventory_digest"]:
        raise LiveEvidenceError("cannot build authority set from non-canonical fixture inventory digest")
    return {
        "schema_version": AUTHORITY_SET_SCHEMA,
        "repository": authority.repository,
        "trusted_main_head_sha": authority.trusted_main_head_sha,
        "materialization_commit_sha": authority.materialization_commit_sha,
        "policy_bundle_digest": authority.policy_bundle_digest,
        "fixture_pool_inventory_digest": fixture_pool_inventory_digest,
        "runtime_kind": authority.runtime_kind,
        "protected_policy_status": authority.protected_policy_status,
        "effect_lineage_required": authority.effect_lineage_required,
        "writer_fence_quiesced": authority.writer_fence_quiesced,
        "scenario_bindings": expected_scenario_bindings(),
    }


def _normalize_any(document: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    if isinstance(document, dict) and document.get("schema_version") == PAIR_SCHEMA:
        return _normalize_launch_cancel_pair(document)
    return _normalize_record(document)


def evaluate_issue_221_authority_set(
    *,
    authority_set: ReleaseAuthoritySet,
    evidence: Iterable[tuple[bytes, dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Evaluate exact per-scenario provenance across the original fixture plus #310 pool."""
    claimed_by: dict[str, str] = {}
    record_ids: set[str] = set()
    run_ids: set[int] = set()
    observed_measurements: set[str] = set()

    for raw_record, document, provenance_document in evidence:
        claims, measurements = _normalize_any(document)
        if not claims:
            raise LiveEvidenceError("authority-set ledger does not accept pending/zero-claim records")
        authorities = [authority_set.authority_for(scenario) for scenario in claims]
        binding_pairs = {(row.feature_id, row.target_ref) for row in authorities}
        if len(binding_pairs) != 1:
            raise LiveEvidenceError("one live record may not span multiple Feature/ref authorities")
        first_authority = authorities[0]
        for row in authorities[1:]:
            if row != first_authority:
                raise LiveEvidenceError("multi-scenario record authority differs inside one fixture domain")
        provenance = _parse_provenance(
            provenance_document,
            raw_record=raw_record,
            authority=first_authority,
        )
        if provenance.record_id in record_ids:
            raise LiveEvidenceError("duplicate live evidence record_id")
        if provenance.github_workflow_run_id in run_ids:
            raise LiveEvidenceError("same workflow run was reused as multiple authority-set records")
        record_ids.add(provenance.record_id)
        run_ids.add(provenance.github_workflow_run_id)
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
                "complete authority-set scenario set lacks global live measurement coverage: "
                + ",".join(sorted(missing_global))
            )
    return {
        "schema_version": LEDGER_SCHEMA,
        "issue": 221,
        "status": status,
        "overall_issue_221_pass": status == "PASS",
        "authority_set": asdict(authority_set),
        "required_scenarios": list(REQUIRED_SCENARIOS),
        "satisfied_scenarios": satisfied,
        "unresolved_scenarios": unresolved,
        "scenario_evidence": {row: claimed_by[row] for row in satisfied},
        "accepted_record_count": len(record_ids),
        "accepted_workflow_run_count": len(run_ids),
        "observed_zero_measurements": sorted(observed_measurements),
        "deterministic_evidence_accepted": False,
    }
