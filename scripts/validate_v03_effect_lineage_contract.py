#!/usr/bin/env python3
"""Validate the frozen v0.3 candidate/Effect Lineage amendment without legacy ambiguity."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "release" / "v0.3.0-draft.yaml"
RELEASE_SPEC = ROOT / "docs" / "v0.3-release-spec.md"

LEGACY_FIELD = "head_change_requires_new_semantic_dispatch"
LEGACY_TEST = "new-head-requires-new-semantic-dispatch"


def contract_errors(data: dict) -> list[str]:
    errors: list[str] = []
    worker = data.get("worker_result_contract") or {}
    if LEGACY_FIELD in worker:
        errors.append(f"legacy worker result contract field is forbidden: {LEGACY_FIELD}")
    required_worker = {
        "head_change_invalidates_stale_candidate_evidence": True,
        "head_change_requires_fresh_exact_candidate_bound_work": True,
        "head_change_alone_authorizes_new_external_dispatch": False,
        "fresh_candidate_external_dispatch_requires_effect_lineage_clearance": True,
    }
    for key, expected in required_worker.items():
        if worker.get(key) is not expected:
            errors.append(f"worker_result_contract.{key} must be {expected!r}")

    lineage = data.get("effect_lineage") or {}
    for key, expected in {
        "required": True,
        "lineage_gate_before_new_exact_reservation": True,
        "unresolved_predecessor_blocks_new_external_identity": True,
        "successor_proposal_has_independent_external_identity": False,
        "concurrent_active_descendants": "forbidden",
        "candidate_or_revision_change_alone_proves_new_effect": False,
    }.items():
        if lineage.get(key) != expected:
            errors.append(f"effect_lineage.{key} must be {expected!r}")

    recovery = data.get("external_dispatch_recovery") or {}
    if recovery.get("not_launched_after_durable_launch_authorization_revokes_authority") is not False:
        errors.append("NOT_LAUNCHED after durable launch authorization must not revoke authority")
    if recovery.get("launch_authorized_not_launched_successor_behavior") != "same-existing-key-or-blocked":
        errors.append("authorized + NOT_LAUNCHED must remain same-existing-key-or-blocked")
    if recovery.get("stronger_no_duplicate_proof_required_for_authorized_predecessor_retirement") is not True:
        errors.append("authorized predecessor retirement must require stronger no-duplicate proof")

    resolution = data.get("unknown_resolution") or {}
    if resolution.get("allowed_outcomes") != [
        "CORRELATE_EXISTING_RECEIPT",
        "PROVE_NOT_LAUNCHED",
        "RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN",
        "REMAIN_BLOCKED",
    ]:
        errors.append("unknown_resolution.allowed_outcomes must equal the frozen four outcomes")
    if resolution.get("launch_authorized_not_launched_is_revocation_proof") is not False:
        errors.append("authorized + NOT_LAUNCHED must not be revocation proof")
    if resolution.get("resolution_itself_dispatches") is not False:
        errors.append("resolution itself must not dispatch")

    tests = data.get("required_tests") or {}
    candidate = list(tests.get("candidate_binding") or [])
    if LEGACY_TEST in candidate:
        errors.append(f"legacy candidate test id is forbidden: {LEGACY_TEST}")
    for required in (
        "reviewer-result-after-head-change-rejected",
        "qa-result-after-head-change-rejected",
        "new-head-requires-fresh-exact-candidate-work",
        "unresolved-predecessor-blocks-new-head-external-dispatch",
    ):
        if required not in candidate:
            errors.append(f"required candidate binding test missing: {required}")
    lineage_tests = list(tests.get("effect_lineage") or [])
    for required in (
        "unknown-candidate-change-fresh-proposal-no-new-key",
        "concurrent-planners-no-sibling-active-descendants",
        "stale-resolution-rejected",
        "launch-authorized-not-launched-stale-runner-blocks-successor",
    ):
        if required not in lineage_tests:
            errors.append(f"required Effect Lineage test missing: {required}")
    return errors


def _assert_negative_fixtures(current: dict) -> None:
    legacy_field = deepcopy(current)
    legacy_field.setdefault("worker_result_contract", {})[LEGACY_FIELD] = True
    assert any(LEGACY_FIELD in row for row in contract_errors(legacy_field))

    legacy_test = deepcopy(current)
    legacy_test.setdefault("required_tests", {}).setdefault("candidate_binding", []).append(LEGACY_TEST)
    assert any(LEGACY_TEST in row for row in contract_errors(legacy_test))

    missing_clearance = deepcopy(current)
    missing_clearance["worker_result_contract"].pop("fresh_candidate_external_dispatch_requires_effect_lineage_clearance", None)
    assert contract_errors(missing_clearance)

    wrong_authorization = deepcopy(current)
    wrong_authorization["worker_result_contract"]["head_change_alone_authorizes_new_external_dispatch"] = True
    assert contract_errors(wrong_authorization)


def main() -> None:
    data = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    errors = contract_errors(data)
    if errors:
        raise SystemExit("v0.3 Effect Lineage contract invalid:\n- " + "\n- ".join(errors))
    _assert_negative_fixtures(data)
    spec = RELEASE_SPEC.read_text(encoding="utf-8")
    for required_phrase in (
        "dispatch.launch.authorized",
        "Effect Lineage",
        "PROVE_NOT_LAUNCHED",
        "NOT_LAUNCHED",
    ):
        if required_phrase not in spec:
            raise SystemExit(f"frozen Release Spec lacks required Effect Lineage phrase: {required_phrase}")
    print("v0.3 Effect Lineage contract validation passed")


if __name__ == "__main__":
    main()
