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
from operator_store_model import digest_json, normalize_repository, operation_events
from operator_vertical import (
    FeatureSnapshot, TrustedDispatchContext, validate_collected_outputs, validate_worker_result,
)
from operator_vertical_store import vertical_projection
from operator_store_model import reservation_path
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
    if (projection.get("target_repository"), projection.get("feature_id")) != (
        preflight.execution.repository, preflight.slot.feature_id
    ) or observation.get("installation_commit_sha") != preflight.execution.installation_commit_sha:
        raise V03DogfoodPostRunFinalizerError("Operation/source installation escaped fixed dogfood scope")
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


def _verify_consumed_result(*, events, trusted, resolved, result_source, lookup_sequence):
    """Verify the original accepted callback; never mint replacement receipts."""
    key, generation = trusted["external_dispatch_key"], trusted["operation_generation"]
    callbacks = [row for row in events if row.get("event_type") == "worker.callback.recorded"
                 and row.get("operation_generation") == generation
                 and (row.get("payload") or {}).get("external_dispatch_key") == key]
    if len(callbacks) != 1:
        raise V03DogfoodPostRunFinalizerError("run lacks one original protected callback")
    recorded = callbacks[0]
    payload = recorded.get("payload") or {}
    envelope = payload.get("trusted_callback_envelope")
    if not isinstance(envelope, dict) or digest_json(envelope) != payload.get("trusted_callback_envelope_digest"):
        raise V03DogfoodPostRunFinalizerError("original callback envelope digest differs")
    worker_payload = envelope.get("worker_payload")
    receipts = envelope.get("collected_outputs")
    context = envelope.get("trusted_context")
    if not isinstance(worker_payload, dict) or not isinstance(receipts, list) or not isinstance(context, dict):
        raise V03DogfoodPostRunFinalizerError("original callback envelope is incomplete")
    if digest_json({"worker_payload": worker_payload, "receipts": receipts}) != payload.get("callback_digest"):
        raise V03DogfoodPostRunFinalizerError("original callback result digest differs")
    expected_context = {name: trusted[name] for name in (
        "operation_id", "operation_generation", "operation_profile", "semantic_effect_key",
        "external_dispatch_key", "dispatch_id", "target_repository", "target_ref", "feature_id",
        "expected_revision", "feature_stage", "role",
    )}
    expected_context.update(
        runtime_receipt_identity=str(resolved.run.run_id), task_id=resolved.run.task_id,
        candidate_pr_number=resolved.run.candidate_pr_number if trusted["role"] in {"reviewer", "qa"} else None,
        candidate_head_sha=trusted["launch_candidate_head_sha"],
        worker_identity=resolved.run.worker_identity, collector_identity=resolved.run.collector_identity,
    )
    comparable = dict(context)
    comparable["target_repository"] = normalize_repository(str(context.get("target_repository") or ""))
    expected_context["target_repository"] = normalize_repository(expected_context["target_repository"])
    if comparable != expected_context:
        raise V03DogfoodPostRunFinalizerError("original callback differs from historical launch/fresh run")
    callback_id = "gh-aw-callback-" + digest_json({
        "operation_id": trusted["operation_id"], "generation": generation,
        "external_dispatch_key": key, "runtime_receipt_identity": str(resolved.run.run_id),
        "run_id": resolved.run.run_id,
    })[:24]
    if payload.get("callback_id") != callback_id:
        raise V03DogfoodPostRunFinalizerError("original callback identity differs from exact run")
    accepted = [row for row in events if row.get("event_type") == "worker.result.validated"
                and row.get("operation_generation") == generation
                and (row.get("payload") or {}).get("callback_id") == callback_id]
    rejected = [row for row in events if row.get("event_type") == "worker.result.rejected"
                and row.get("operation_generation") == generation
                and (row.get("payload") or {}).get("callback_id") == callback_id]
    if len(accepted) != 1 or rejected:
        raise V03DogfoodPostRunFinalizerError("original callback lacks one accepted result")
    acceptance = accepted[0]
    if not (lookup_sequence < int(recorded.get("sequence") or 0) < int(acceptance.get("sequence") or 0)):
        raise V03DogfoodPostRunFinalizerError("original callback/acceptance ordering differs")
    if ((acceptance.get("payload") or {}).get("role"),
        (acceptance.get("payload") or {}).get("dispatch_id")) != (trusted["role"], trusted["dispatch_id"]):
        raise V03DogfoodPostRunFinalizerError("accepted result differs from original dispatch")

    fresh_payload = validate_worker_result(trusted["role"], resolved.role_payload)
    if digest_json(fresh_payload) != digest_json(worker_payload):
        raise V03DogfoodPostRunFinalizerError("fresh Worker result differs from originally consumed result")
    def descriptors(outputs):
        result = {}
        for output in outputs:
            label = output["label"]
            if label in result:
                raise V03DogfoodPostRunFinalizerError("duplicate sealed output label")
            result[label] = tuple(output[name] for name in ("kind", "media_type", "trusted_uri"))
        return result
    original_descriptors = descriptors(receipts)
    fresh_descriptors = descriptors([{
        "label": output.label, "kind": output.kind, "media_type": output.media_type,
        "trusted_uri": output.trusted_uri,
    } for output in resolved.outputs])
    if original_descriptors != fresh_descriptors:
        raise V03DogfoodPostRunFinalizerError("fresh outputs differ from original sealed receipt locations")

    # This narrow historical Feature view is only for the unchanged receipt
    # validator's revision/stage/candidate fences; it cannot attest milestones.
    historical_feature = FeatureSnapshot(
        repository=expected_context["target_repository"], feature_id=trusted["feature_id"],
        target_ref=trusted["target_ref"], revision=trusted["expected_revision"],
        manifest_digest="", current_stage=trusted["feature_stage"],
        stages={}, gates={}, remediation_tasks=(), artifacts=(),
        candidate_pr_number=expected_context["candidate_pr_number"],
        candidate_head_sha=trusted["launch_candidate_head_sha"],
    )
    validate_collected_outputs(
        context=TrustedDispatchContext(**context), feature=historical_feature,
        worker_payload=fresh_payload, receipts=receipts, content_loader=result_source.load_content,
    )


def _is_ancestor(result_source, repository: str, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    payload = result_source._json(
        repository,
        f"/compare/{ancestor}...{descendant}",
        result_source.config.target_token,
    )
    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "ahead"
        and int(payload.get("behind_by") or 0) == 0
        and str(((payload.get("merge_base_commit") or {}).get("sha")) or "").lower() == ancestor
    )


def _durable_run_bindings(preflight, observation, events):
    """Re-establish launches plus every trusted Developer-output candidate handoff."""
    snapshot = preflight.composition.runtime.backend.read_snapshot()
    projection = vertical_projection(snapshot, observation["operation_id"])
    bindings = {}
    ordered: list[dict[str, Any]] = []
    for row in events:
        if row.get("event_type") != "dispatch.launch.lookup-recorded" or (row.get("payload") or {}).get("lookup_state") != "LAUNCHED":
            continue
        lookup = row["payload"]
        key = str(lookup.get("external_dispatch_key") or "")
        run_id = int(lookup["receipt_id"])
        authorizations = [event for event in events if event.get("event_type") == "dispatch.launch.authorized"
                          and (event.get("payload") or {}).get("external_dispatch_key") == key
                          and event.get("operation_generation") == row.get("operation_generation")]
        if not key or len(authorizations) != 1 or run_id in bindings:
            raise V03DogfoodPostRunFinalizerError("run lacks one exact protected launch authorization")
        launch = authorizations[0]["payload"]
        reservation = snapshot.get(reservation_path(str(launch.get("semantic_effect_key") or "")))
        if not isinstance(reservation, dict) or (
            reservation.get("external_dispatch_key"), reservation.get("feature_id"), reservation.get("role")
        ) != (key, preflight.slot.feature_id, launch.get("role")):
            raise V03DogfoodPostRunFinalizerError("run launch/reservation target binding differs")
        trusted = {
            "operation_id": observation["operation_id"],
            "operation_generation": int(row["operation_generation"]),
            "operation_profile": str(projection["operation_profile"]),
            "semantic_effect_key": str(launch["semantic_effect_key"]),
            "external_dispatch_key": key, "dispatch_id": str(launch["dispatch_id"]),
            "target_repository": preflight.execution.repository, "target_ref": preflight.slot.target_ref,
            "feature_id": preflight.slot.feature_id, "expected_revision": int(reservation["expected_revision"]),
            "feature_stage": str(launch["stage"]), "role": str(launch["role"]),
            "launch_candidate_head_sha": launch.get("candidate_head_sha"),
        }
        resolved = preflight.composition.result_source.resolve(
            external_dispatch_key=key, expected_receipt_identity=str(run_id), trusted_context=trusted
        )
        if resolved.run.run_id != run_id or resolved.run.role != trusted["role"]:
            raise V03DogfoodPostRunFinalizerError("production result source differs from durable launch")
        if trusted["role"] in {"reviewer", "qa"} and (
            resolved.run.candidate_pr_number != preflight.candidate_pr_number
            or resolved.run.candidate_head_sha != launch.get("candidate_head_sha")
        ):
            raise V03DogfoodPostRunFinalizerError("Gate result differs from historical exact candidate")
        if observation.get("scenario") == "session_recovery":
            # Recovery deliberately leaves the completed Worker unconsumed.
            if any(event.get("event_type") in {"worker.callback.recorded", "worker.result.validated"}
                   for event in events):
                raise V03DogfoodPostRunFinalizerError("recovery unexpectedly consumed a Worker callback")
        else:
            _verify_consumed_result(
                events=events, trusted=trusted, resolved=resolved,
                result_source=preflight.composition.result_source,
                lookup_sequence=int(row.get("sequence") or 0),
            )

        output_pr = resolved.run.candidate_pr_number
        output_head = resolved.run.candidate_head_sha
        if trusted["role"] == "developer" and observation.get("scenario") != "session_recovery":
            callbacks = [
                event for event in events
                if event.get("event_type") == "worker.callback.recorded"
                and event.get("operation_generation") == row.get("operation_generation")
                and (event.get("payload") or {}).get("external_dispatch_key") == key
            ]
            if len(callbacks) != 1:
                raise V03DogfoodPostRunFinalizerError("Developer run lacks one sealed callback for candidate handoff")
            callback = callbacks[0]
            callback_id = str((callback.get("payload") or {}).get("callback_id") or "")
            handoffs = [
                event for event in events
                if event.get("event_type") == "candidate.handoff.adopted"
                and event.get("operation_generation") == row.get("operation_generation")
                and (event.get("payload") or {}).get("callback_id") == callback_id
            ]
            if len(handoffs) != 1:
                raise V03DogfoodPostRunFinalizerError("Developer output lacks one durable trusted candidate handoff")
            handoff = handoffs[0]
            handoff_payload = handoff.get("payload") or {}
            if (
                int(handoff_payload.get("source_candidate_pr_number") or 0) != int(output_pr or 0)
                or handoff_payload.get("source_candidate_head_sha") != output_head
                or handoff_payload.get("prior_candidate_head_sha") != launch.get("candidate_head_sha")
                or handoff_payload.get("dispatch_id") != launch.get("dispatch_id")
                or int(handoff_payload.get("fixture_candidate_pr_number") or 0) != preflight.candidate_pr_number
                or not (
                    int(row.get("sequence") or 0)
                    < int(callback.get("sequence") or 0)
                    < int(handoff.get("sequence") or 0)
                )
            ):
                raise V03DogfoodPostRunFinalizerError("Developer candidate handoff identity/order differs")
        binding = {
            "repository": preflight.execution.repository, "feature_id": preflight.slot.feature_id,
            "target_ref": preflight.slot.target_ref, "candidate_pr_number": preflight.candidate_pr_number,
            "candidate_input_head_sha": launch.get("candidate_head_sha"),
            "candidate_output_pr_number": output_pr,
            "candidate_output_head_sha": output_head,
            "role": trusted["role"], "workflow": preflight.workflows.workflow_for(trusted["role"]),
            "external_dispatch_key": key, "lookup_sequence": int(row.get("sequence") or 0),
        }
        bindings[run_id] = binding
        ordered.append(binding)

    latest_developer_head: str | None = None
    for binding in ordered:
        role = binding["role"]
        input_head = str(binding.get("candidate_input_head_sha") or "")
        if role == "developer" and observation.get("scenario") != "session_recovery":
            output_head = str(binding.get("candidate_output_head_sha") or "")
            if not _is_ancestor(
                preflight.composition.result_source,
                preflight.execution.repository,
                input_head,
                output_head,
            ) or input_head == output_head:
                raise V03DogfoodPostRunFinalizerError("Developer output is not a strict descendant of its dispatched input")
            if latest_developer_head is not None and not _is_ancestor(
                preflight.composition.result_source,
                preflight.execution.repository,
                latest_developer_head,
                output_head,
            ):
                raise V03DogfoodPostRunFinalizerError("remediation candidate does not carry predecessor implementation")
            latest_developer_head = output_head
        elif role in {"reviewer", "qa"}:
            if latest_developer_head is None or not _is_ancestor(
                preflight.composition.result_source,
                preflight.execution.repository,
                latest_developer_head,
                input_head,
            ):
                raise V03DogfoodPostRunFinalizerError("Gate role did not inspect a candidate containing exact Developer output")
    return bindings


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


def _persist_cycles(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct exact Feature Persist triplets after accepted Worker results."""
    cycles: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for row in events:
        event_type = str(row.get("event_type") or "")
        if event_type not in {"persist.requested", "persist.linearized", "persist.confirmed"}:
            continue
        payload = row.get("payload") or {}
        feature_event_id = str(payload.get("feature_event_id") or "")
        if not feature_event_id:
            raise V03DogfoodPostRunFinalizerError("Persist fact lacks Feature Event identity")
        bucket = by_id.setdefault(feature_event_id, {})
        if event_type in bucket:
            raise V03DogfoodPostRunFinalizerError("duplicate Persist phase for one Feature Event")
        bucket[event_type] = row
    for feature_event_id, phases in by_id.items():
        if set(phases) != {"persist.requested", "persist.linearized", "persist.confirmed"}:
            raise V03DogfoodPostRunFinalizerError("Feature Event lacks complete Persist triplet")
        requested, linearized, confirmed = (
            phases["persist.requested"], phases["persist.linearized"], phases["persist.confirmed"]
        )
        sequences = tuple(int(row.get("sequence") or 0) for row in (requested, linearized, confirmed))
        if not (sequences[0] < sequences[1] < sequences[2]):
            raise V03DogfoodPostRunFinalizerError("Feature Persist phases are out of order")
        rp, lp, cp = (row.get("payload") or {} for row in (requested, linearized, confirmed))
        stable = ("feature_event_id", "expected_revision", "target_ref", "candidate_head_sha")
        if any(rp.get(name) != lp.get(name) or rp.get(name) != cp.get(name) for name in stable):
            raise V03DogfoodPostRunFinalizerError("Feature Persist identity drifted across phases")
        expected_revision = rp.get("expected_revision")
        result_revision = cp.get("result_revision")
        if not isinstance(expected_revision, int) or not isinstance(result_revision, int) or result_revision <= expected_revision:
            raise V03DogfoodPostRunFinalizerError("Feature Persist confirmation lacks advancing revision")
        cycles.append({
            "feature_event_id": feature_event_id,
            "expected_revision": expected_revision,
            "result_revision": result_revision,
            "target_ref": rp.get("target_ref"),
            "candidate_head_sha": rp.get("candidate_head_sha"),
            "requested_sequence": sequences[0],
            "linearized_sequence": sequences[1],
            "confirmed_sequence": sequences[2],
        })
    return sorted(cycles, key=lambda row: row["requested_sequence"])


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

    validated_rows = [row for row in events if row.get("event_type") == "worker.result.validated"]
    validated = [int(row.get("sequence") or 0) for row in validated_rows]
    launched = [
        int(row.get("sequence") or 0)
        for row in events
        if row.get("event_type") == "dispatch.launch.lookup-recorded"
        and (row.get("payload") or {}).get("lookup_state") == "LAUNCHED"
    ]
    expected_validated = 0 if scenario == "session_recovery" else len(expected_roles)
    if len(validated) != expected_validated or len(launched) != len(expected_roles):
        raise V03DogfoodPostRunFinalizerError("durable worker validation/launch count differs from frozen role sequence")
    persist_cycles = _persist_cycles(events)
    if scenario == "session_recovery":
        if persist_cycles:
            raise V03DogfoodPostRunFinalizerError("session recovery unexpectedly persisted an unconsumed Worker result")
    else:
        if len(persist_cycles) != len(validated_rows):
            raise V03DogfoodPostRunFinalizerError("accepted Worker results do not each have one complete Feature Persist")
        previous_revision = None
        for index, (accepted, cycle) in enumerate(zip(validated_rows, persist_cycles)):
            accepted_sequence = int(accepted.get("sequence") or 0)
            next_claim = int(dispatches[index + 1]["claim_sequence"]) if index + 1 < len(dispatches) else None
            if cycle["requested_sequence"] <= accepted_sequence:
                raise V03DogfoodPostRunFinalizerError("Feature Persist began before exact Worker acceptance")
            if next_claim is not None and cycle["confirmed_sequence"] >= next_claim:
                raise V03DogfoodPostRunFinalizerError("next dispatch began before prior Feature Persist confirmation")
            if previous_revision is not None and cycle["expected_revision"] != previous_revision:
                raise V03DogfoodPostRunFinalizerError("Feature revision chain is discontinuous across milestones")
            previous_revision = cycle["result_revision"]
            if cycle["target_ref"] != observation.get("target_ref"):
                raise V03DogfoodPostRunFinalizerError("Feature Persist escaped frozen target ref")
        done_rows = [row for row in events if row.get("event_type") == "operation.done"]
        if len(done_rows) != 1 or (done_rows[0].get("payload") or {}).get("feature_revision") != persist_cycles[-1]["result_revision"]:
            raise V03DogfoodPostRunFinalizerError("terminal DONE is not bound to final confirmed Feature revision")
    for index, seq in enumerate(validated[:-1]):
        next_claim = int(dispatches[index + 1]["claim_sequence"])
        if not any(
            seq < int(row.get("sequence") or 0) < next_claim
            and row.get("event_type") == "loop.stable-stop"
            and str((row.get("payload") or {}).get("status") or "") == "WAITING_EXTERNAL"
            for row in events
        ):
            raise V03DogfoodPostRunFinalizerError("durable intermediate result lacks bounded WAITING_EXTERNAL stable stop")

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
        recovery_responses = tuple(str(value) for value in (observation.get("recovery_response_ids") or []))
        recovery_calls = tuple(str(value) for value in (observation.get("recovery_function_call_ids") or []))
        original_responses = set(str(value) for value in (observation.get("response_ids") or []))
        original_calls = set(str(value) for value in (observation.get("function_call_ids") or []))
        discovered_decisions = tuple(sorted(str(value) for value in (observation.get("recovery_discovery_decision_ids") or [])))
        discovered_notifications = tuple(sorted(str(value) for value in (observation.get("recovery_discovery_notification_ids") or [])))
        if (
            observation.get("new_session_discovery_observed") is not True
            or not recovery_responses or not recovery_calls
            or original_responses.intersection(recovery_responses)
            or original_calls.intersection(recovery_calls)
            or discovered_decisions != tuple(sorted(pending))
            or discovered_notifications != tuple(sorted(unread))
        ):
            raise V03DogfoodPostRunFinalizerError(
                "session recovery trace is not bound to exact durable Decision/Notification identities"
            )
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
    if observation.get("candidate_head_sha") != preflight.candidate_head_sha:
        raise V03DogfoodPostRunFinalizerError("candidate head changed after raw execution")
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
            installation_commit_sha=preflight.execution.installation_commit_sha,
            github_api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        ),
        runtime_receipt_resolver=lambda record: _durable_receipt(events, observation),
        runtime_binding_resolver=lambda record: _durable_run_bindings(preflight, observation, events),
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
