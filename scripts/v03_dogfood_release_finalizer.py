#!/usr/bin/env python3
"""Construct one canonical v0.3 real-dogfood release-run record.

Raw runner observations are never release authority. This module only promotes
one observation after independently reconstructed trusted facts and the existing
schema + trusted provenance verifier accept the complete final record.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from validate_v03_dogfood_evidence import SCENARIO_PROFILES, validate_record


class V03DogfoodReleaseFinalizerError(RuntimeError):
    pass


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "" or value == []:
        raise V03DogfoodReleaseFinalizerError(f"missing finalization fact: {key}")
    return value


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _milestones(scenario: str, facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    profile = SCENARIO_PROFILES.get(scenario)
    if profile is None:
        raise V03DogfoodReleaseFinalizerError("scenario escaped frozen dogfood inventory")
    supplied = facts.get("milestones")
    if not isinstance(supplied, list):
        raise V03DogfoodReleaseFinalizerError("trusted milestone facts are required")
    by_name = {
        str(row.get("name")): row
        for row in supplied
        if isinstance(row, Mapping) and row.get("name")
    }
    expected_names = [name for name, _, _ in profile["milestones"]]
    if set(by_name) != set(expected_names):
        raise V03DogfoodReleaseFinalizerError("trusted milestone fact set differs from frozen profile")
    result = []
    for sequence, (name, state_after, categories) in enumerate(profile["milestones"], start=1):
        row = by_name[name]
        uris = row.get("evidence_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(uri, str) and uri for uri in uris):
            raise V03DogfoodReleaseFinalizerError(f"milestone {name} lacks durable evidence URIs")
        supplied_categories = frozenset(str(value) for value in (row.get("evidence_categories") or []))
        if supplied_categories != frozenset(categories):
            raise V03DogfoodReleaseFinalizerError(f"milestone {name} evidence categories drifted")
        result.append({
            "sequence": sequence,
            "name": name,
            "state_after": state_after,
            "evidence_categories": sorted(categories),
            "evidence_uris": list(dict.fromkeys(uris)),
        })
    return result


def _trusted_assertions(scenario: str, facts: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, bool]:
    supplied = facts.get("assertions")
    if not isinstance(supplied, Mapping):
        raise V03DogfoodReleaseFinalizerError("independently reconstructed trusted assertions are required")
    keys = {
        "durable_operation_state",
        "independent_review_observed",
        "remediation_round_trip_observed",
        "new_session_discovery_observed",
    }
    if set(supplied) != keys or any(type(supplied[key]) is not bool for key in keys):
        raise V03DogfoodReleaseFinalizerError("trusted assertion set is incomplete or malformed")
    assertions = dict(supplied)
    assertions["no_repeated_continue_messages"] = observation.get("repeated_continue_messages") == 0
    if scenario == "session_recovery":
        if observation.get("new_session_discovery_observed") is not True or assertions["new_session_discovery_observed"] is not True:
            raise V03DogfoodReleaseFinalizerError("session recovery discovery lacks both raw and durable proof")
    elif assertions["new_session_discovery_observed"] is not False:
        raise V03DogfoodReleaseFinalizerError("non-recovery scenario cannot claim new-session discovery")
    return assertions


def build_release_record(
    *,
    observation: Mapping[str, Any],
    trusted_facts: Mapping[str, Any],
    verifier_identity: str,
    attestation_uri: str,
    adapter_id: str,
    runtime_kind: str,
) -> dict[str, Any]:
    if observation.get("release_eligible") is not False or observation.get("provenance_verified") is not False:
        raise V03DogfoodReleaseFinalizerError("input observation must remain raw and non-authoritative")
    scenario = str(_required(observation, "scenario"))
    profile = SCENARIO_PROFILES.get(scenario)
    if profile is None:
        raise V03DogfoodReleaseFinalizerError("scenario escaped frozen dogfood inventory")
    if observation.get("final_status") != profile["end_state"]:
        raise V03DogfoodReleaseFinalizerError("raw final state differs from frozen profile")
    run_ids = tuple(int(value) for value in _required(observation, "workflow_run_ids"))
    if not run_ids or any(value < 1 for value in run_ids) or len(set(run_ids)) != len(run_ids):
        raise V03DogfoodReleaseFinalizerError("raw workflow run set is invalid")
    repository = str(_required(observation, "repository"))
    candidate_pr = int(_required(observation, "candidate_pr_number"))
    candidate_head = str(_required(observation, "candidate_head_sha"))
    milestones = _milestones(scenario, trusted_facts)
    assertions = _trusted_assertions(scenario, trusted_facts, observation)
    evidence_uris = list(dict.fromkeys(str(uri) for uri in _required(trusted_facts, "evidence_uris")))
    for run_id in run_ids:
        expected = f"https://github.com/{repository}/actions/runs/{run_id}"
        if not any(uri == expected or uri.startswith(expected + "/") for uri in evidence_uris):
            raise V03DogfoodReleaseFinalizerError(f"workflow run {run_id} lacks exact durable URI")
    for required_uri in (
        f"https://github.com/{repository}/pull/{candidate_pr}",
        f"https://github.com/{repository}/commit/{candidate_head}",
        attestation_uri,
    ):
        if required_uri not in evidence_uris:
            raise V03DogfoodReleaseFinalizerError("final evidence set lacks required exact authority URI")

    record = {
        "schema_version": "ai-sdlc.v0.3-dogfood-evidence/v1",
        "evidence_kind": "release-run",
        "scenario": scenario,
        "run_id": str(_required(trusted_facts, "release_run_id")),
        "recorded_at": str(trusted_facts.get("recorded_at") or _iso_now()),
        "repository": repository,
        "feature_id": str(_required(observation, "feature_id")),
        "target_ref": str(_required(observation, "target_ref")),
        "candidate": {"pr_number": candidate_pr, "head_sha": candidate_head},
        "operation": {
            "operation_id": str(_required(observation, "operation_id")),
            "generation": int(_required(trusted_facts, "operation_generation")),
        },
        "adapter": {"adapter_id": adapter_id, "supported": True, "write_capable": True},
        "runtime": {
            "runtime_kind": runtime_kind,
            "real_supported_runtime": True,
            "receipt_identity": str(_required(observation, "runtime_receipt_identity")),
            "workflow_run_ids": list(run_ids),
        },
        "provenance": {
            "verification_status": "VERIFIED",
            "verifier_identity": verifier_identity,
            "attestation_uri": attestation_uri,
        },
        "start_state": profile["start_state"],
        "end_state": profile["end_state"],
        "milestones": milestones,
        "counts": {
            "human_interventions": int(trusted_facts.get("human_interventions", 0)),
            "repeated_continue_messages": int(observation.get("repeated_continue_messages", -1)),
        },
        "assertions": assertions,
        "evidence_uris": evidence_uris,
        "verdict": "PASS",
        "release_eligible": True,
    }
    validate_record(record, f"real dogfood {scenario}", provenance_verifier=trusted_facts.get("provenance_verifier"))
    return deepcopy(record)
