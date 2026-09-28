#!/usr/bin/env python3
"""Trusted-main post-run finalizer for one real v0.3 dogfood scenario.

This process never executes dogfood. It consumes one completed raw observation,
re-opens the protected production Store through the trusted-main composition,
reconstructs scenario authority from durable Store facts, re-resolves GitHub
provenance, and only then may emit release-run evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_store_model import operation_events
from operator_vertical_store import vertical_projection
from v03_dogfood_production_provenance import (
    ProductionDogfoodProvenanceConfig,
    ProductionDogfoodProvenanceVerifier,
)
from v03_dogfood_release_finalizer import build_release_record
from v03_dogfood_runtime_driver import assemble_preflight, _head
from v03_dogfood_scenario_runner import SCENARIO_ROLE_SEQUENCES, STEP_ROLE
from validate_v03_dogfood_evidence import SCENARIO_PROFILES

VERIFIER_IDENTITY = "ai-sdlc/v0.3-production-dogfood-post-run-verifier/v1"
RUNTIME_KIND = "github-actions/gh-aw-production"


class V03DogfoodPostRunFinalizerError(RuntimeError):
    pass


def _required(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise V03DogfoodPostRunFinalizerError(f"missing {label}")
    return text


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise V03DogfoodPostRunFinalizerError("raw observation is not an object")
    if payload.get("release_eligible") is not False or payload.get("provenance_verified") is not False:
        raise V03DogfoodPostRunFinalizerError("source observation must remain non-authoritative")
    return payload


def _run_uri(repository: str, run_id: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def _durable_operation_facts(preflight: Any, observation: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    operation_id = _required(observation.get("operation_id"), "operation id")
    snapshot = preflight.composition.runtime.backend.read_snapshot()
    events = operation_events(snapshot, operation_id)
    if not events:
        raise V03DogfoodPostRunFinalizerError("protected Store contains no durable Operation history")
    projection = vertical_projection(snapshot, operation_id)
    if not isinstance(projection, dict):
        raise V03DogfoodPostRunFinalizerError("protected Store Operation projection is malformed")
    if str(projection.get("status") or "") != str(observation.get("final_status") or ""):
        raise V03DogfoodPostRunFinalizerError("raw final state differs from protected Store projection")
    return events, projection


def _durable_receipt(events: list[dict[str, Any]], observation: Mapping[str, Any]) -> Mapping[str, Any]:
    run_ids: list[int] = []
    for row in events:
        if row.get("event_type") != "dispatch.launch.lookup-recorded":
            continue
        payload = row.get("payload") or {}
        if payload.get("lookup_state") != "LAUNCHED":
            continue
        receipt = str(payload.get("receipt_id") or "")
        if not receipt.isdigit() or int(receipt) < 1:
            raise V03DogfoodPostRunFinalizerError("durable LAUNCHED lookup lacks exact Actions receipt")
        run_ids.append(int(receipt))
    declared = [int(value) for value in (observation.get("workflow_run_ids") or [])]
    if run_ids != declared or not run_ids or len(set(run_ids)) != len(run_ids):
        raise V03DogfoodPostRunFinalizerError("protected Store runtime receipt sequence differs from raw observation")
    receipt_identity = str(observation.get("runtime_receipt_identity") or "")
    if receipt_identity != str(run_ids[-1]):
        raise V03DogfoodPostRunFinalizerError("runtime receipt identity is not the final durable launch receipt")
    return {"receipt_identity": receipt_identity, "workflow_run_ids": run_ids}


def _selected_dispatches(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, Any] | None = None
    result: list[dict[str, Any]] = []
    for row in events:
        event_type = str(row.get("event_type") or "")
        sequence = int(row.get("sequence") or 0)
        if event_type == "loop.step.selected":
            step = str((row.get("payload") or {}).get("step") or "")
            role = STEP_ROLE.get(step)
            selected = {"step": step, "role": role, "selected_sequence": sequence} if role else None
            continue
        if event_type != "dispatch.claimed":
            continue
        if selected is None or int(selected["selected_sequence"]) >= sequence:
            raise V03DogfoodPostRunFinalizerError("durable dispatch claim lacks preceding trusted role-bearing selected step")
        result.append({**selected, "claim_sequence": sequence})
        selected = None
    return result


def _event_sequences(events: list[dict[str, Any]], event_type: str) -> list[int]:
    return [int(row.get("sequence") or 0) for row in events if row.get("event_type") == event_type]


def _stable_stop_after(events: list[dict[str, Any]], sequence: int, expected_status: str) -> bool:
    return any(
        int(row.get("sequence") or 0) > sequence
        and row.get("event_type") == "loop.stable-stop"
        and str((row.get("payload") or {}).get("status") or "") == expected_status
        for row in events
    )


def _reconstruct_release_authority(
    scenario: str,
    events: list[dict[str, Any]],
    projection: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> tuple[Mapping[str, set[str]], Mapping[str, bool]]:
    profile = SCENARIO_PROFILES.get(scenario)
    expected_roles = SCENARIO_ROLE_SEQUENCES.get(scenario)
    if profile is None or expected_roles is None:
        raise V03DogfoodPostRunFinalizerError("scenario escaped frozen evidence inventory")
    if str(projection.get("status") or "") != profile["end_state"]:
        raise V03DogfoodPostRunFinalizerError("durable final state differs from frozen profile")

    dispatches = _selected_dispatches(events)
    roles = tuple(str(row.get("role") or "") for row in dispatches)
    if roles != expected_roles:
        raise V03DogfoodPostRunFinalizerError(
            f"durable role sequence differs from frozen scenario: expected {expected_roles}, got {roles}"
        )
    steps = tuple(str(row.get("step") or "") for row in dispatches)
    expected_steps = {
        "happy_path": ("IMPLEMENTATION_WORK", "CODE_REVIEW", "VERIFICATION_QA"),
        "review_remediation": (
            "IMPLEMENTATION_WORK",
            "CODE_REVIEW",
            "CODE_REMEDIATION",
            "CODE_REREVIEW",
            "VERIFICATION_QA",
        ),
        "session_recovery": ("IMPLEMENTATION_WORK",),
    }[scenario]
    if steps != expected_steps:
        raise V03DogfoodPostRunFinalizerError("durable selected-step sequence differs from frozen scenario")

    validated = _event_sequences(events, "worker.result.validated")
    launched = [
        int(row.get("sequence") or 0)
        for row in events
        if row.get("event_type") == "dispatch.launch.lookup-recorded"
        and (row.get("payload") or {}).get("lookup_state") == "LAUNCHED"
    ]
    if len(validated) != len(expected_roles) or len(launched) != len(expected_roles):
        raise V03DogfoodPostRunFinalizerError("durable worker validation/launch count differs from frozen role sequence")
    if any(not _stable_stop_after(events, seq, "WAITING_EXTERNAL") for seq in validated[:-1]):
        raise V03DogfoodPostRunFinalizerError("durable intermediate worker result lacks WAITING_EXTERNAL stable stop")

    types = {str(row.get("event_type") or "") for row in events}
    if "operation.started" not in types:
        raise V03DogfoodPostRunFinalizerError("protected Store lacks durable operation.started")

    independent_review = False
    remediation_round_trip = False
    new_session_discovery = False
    if scenario == "happy_path":
        if "operation.done" not in types or "notification.created" not in types:
            raise V03DogfoodPostRunFinalizerError("happy path lacks durable DONE/Notification facts")
        independent_review = steps[1] == "CODE_REVIEW" and steps[2] == "VERIFICATION_QA"
    elif scenario == "review_remediation":
        if "operation.done" not in types or "notification.created" not in types:
            raise V03DogfoodPostRunFinalizerError("review remediation lacks durable DONE/Notification facts")
        # The transition from a validated CODE_REVIEW result to a subsequently
        # selected CODE_REMEDIATION step is the durable lifecycle decision that
        # proves REWORK. CODE_REREVIEW followed by VERIFICATION_QA proves the
        # independent re-review PASS, without trusting the scenario label.
        independent_review = steps[1] == "CODE_REVIEW" and steps[3] == "CODE_REREVIEW"
        remediation_round_trip = steps[2] == "CODE_REMEDIATION" and steps[4] == "VERIFICATION_QA"
    else:
        pending = tuple(str(value) for value in (projection.get("pending_decisions") or []))
        unread = tuple(str(value) for value in (projection.get("unread_notifications") or []))
        if not pending or not unread or "decision.requested" not in types or "notification.created" not in types:
            raise V03DogfoodPostRunFinalizerError(
                "session recovery lacks durable pending Decision/Notification facts"
            )
        if observation.get("new_session_discovery_observed") is not True:
            raise V03DogfoodPostRunFinalizerError("session recovery lacks raw fresh-session discovery observation")
        new_session_discovery = True

    assertions = {
        "durable_operation_state": True,
        "independent_review_observed": independent_review,
        "remediation_round_trip_observed": remediation_round_trip,
        "new_session_discovery_observed": new_session_discovery,
    }
    required_assertions = {
        "happy_path": (True, False, False),
        "review_remediation": (True, True, False),
        "session_recovery": (False, False, True),
    }[scenario]
    actual_assertions = (
        assertions["independent_review_observed"],
        assertions["remediation_round_trip_observed"],
        assertions["new_session_discovery_observed"],
    )
    if actual_assertions != required_assertions:
        raise V03DogfoodPostRunFinalizerError("durable assertion reconstruction differs from frozen scenario")

    categories = {name: set(required) for name, _state, required in profile["milestones"]}
    return categories, assertions


def _milestone_facts(
    scenario: str,
    repository: str,
    source_run_id: int,
    worker_run_ids: list[int],
    categories: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    durable = [_run_uri(repository, source_run_id)] + [_run_uri(repository, value) for value in worker_run_ids]
    expected_names = [name for name, _state, _categories in SCENARIO_PROFILES[scenario]["milestones"]]
    if set(categories) != set(expected_names):
        raise V03DogfoodPostRunFinalizerError("reconstructed milestone set differs from frozen profile")
    return [
        {"name": name, "evidence_categories": sorted(categories[name]), "evidence_uris": durable}
        for name in expected_names
    ]


def finalize(*, observation: Mapping[str, Any], preflight: Any, source_run_id: int, finalizer_run_id: int, github_token: str) -> dict[str, Any]:
    scenario = _required(observation.get("scenario"), "scenario")
    if scenario != preflight.slot.scenario:
        raise V03DogfoodPostRunFinalizerError("observation scenario differs from trusted fixed slot")
    repository = preflight.execution.repository
    if observation.get("repository") != repository:
        raise V03DogfoodPostRunFinalizerError("observation repository differs from trusted execution")
    if observation.get("feature_id") != preflight.slot.feature_id or observation.get("target_ref") != preflight.slot.target_ref:
        raise V03DogfoodPostRunFinalizerError("observation escaped the frozen scenario fixture")
    if int(observation.get("candidate_pr_number") or 0) != preflight.candidate_pr_number:
        raise V03DogfoodPostRunFinalizerError("candidate PR differs from independently resolved fixture authority")

    events, projection = _durable_operation_facts(preflight, observation)
    receipt = _durable_receipt(events, observation)
    categories, assertions = _reconstruct_release_authority(scenario, events, projection, observation)
    generation = int(projection.get("generation") or 0)
    if generation < 1:
        generations = [int(row.get("operation_generation") or 0) for row in events]
        generation = max(generations or [0])
    if generation < 1:
        raise V03DogfoodPostRunFinalizerError("protected Store lacks positive Operation generation")

    worker_run_ids = list(receipt["workflow_run_ids"])
    attestation_uri = _run_uri(repository, finalizer_run_id)
    evidence_uris = [
        f"https://github.com/{repository}/pull/{int(observation['candidate_pr_number'])}",
        f"https://github.com/{repository}/commit/{observation['candidate_head_sha']}",
        _run_uri(repository, source_run_id),
        attestation_uri,
        *[_run_uri(repository, value) for value in worker_run_ids],
    ]
    verifier = ProductionDogfoodProvenanceVerifier(
        config=ProductionDogfoodProvenanceConfig(
            repository=repository,
            verifier_identity=VERIFIER_IDENTITY,
            supported_adapter_id=OPENAI_RESPONSES_ADAPTER_ID,
            runtime_kind=RUNTIME_KIND,
            github_token=github_token,
            github_api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        ),
        runtime_receipt_resolver=lambda record: _durable_receipt(events, observation),
        milestone_resolver=lambda record: categories,
    )
    trusted_facts = {
        "release_run_id": str(finalizer_run_id),
        "operation_generation": generation,
        "human_interventions": 0,
        "milestones": _milestone_facts(scenario, repository, source_run_id, worker_run_ids, categories),
        "assertions": assertions,
        "evidence_uris": evidence_uris,
        "provenance_verifier": verifier,
    }
    return build_release_record(
        observation=observation,
        trusted_facts=trusted_facts,
        verifier_identity=VERIFIER_IDENTITY,
        attestation_uri=attestation_uri,
        adapter_id=OPENAI_RESPONSES_ADAPTER_ID,
        runtime_kind=RUNTIME_KIND,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIO_PROFILES))
    parser.add_argument("--observation", required=True, type=Path)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--finalizer-run-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    observation = _load(args.observation)
    if observation.get("scenario") != args.scenario:
        raise V03DogfoodPostRunFinalizerError("workflow scenario differs from observation")
    preflight = assemble_preflight(scenario=args.scenario, env=os.environ, checkout_sha=_head())
    record = finalize(
        observation=observation,
        preflight=preflight,
        source_run_id=args.source_run_id,
        finalizer_run_id=args.finalizer_run_id,
        github_token=_required(os.environ.get("AI_SDLC_ACTIONS_READ_TOKEN"), "Actions read token"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
