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
from operator_store_model import (
    StoreSnapshot,
    decision_path,
    digest_json,
    event_path,
    normalize_repository,
    notification_path,
    operation_events,
    rebuild_projection,
)
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
from v03_dogfood_full_composition import candidate_handoff_records
from v03_dogfood_scenario_runner import (
    SCENARIO_ROLE_SEQUENCES,
    SESSION_TRACE_SCHEMA,
    STEP_ROLE,
    session_trace_path,
)
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


def _durable_operation_facts(preflight: Any, observation: Mapping[str, Any]) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
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
    return snapshot, events, projection


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
    durable_handoffs = candidate_handoff_records(snapshot, observation["operation_id"])
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
                handoff for handoff in durable_handoffs
                if int(handoff.get("operation_generation") if handoff.get("operation_generation") is not None else -1) == int(row.get("operation_generation") if row.get("operation_generation") is not None else -2)
                and str(handoff.get("callback_id") or "") == callback_id
            ]
            if len(handoffs) != 1:
                raise V03DogfoodPostRunFinalizerError("Developer output lacks one durable trusted candidate handoff")
            handoff_payload = handoffs[0]
            if (
                int(handoff_payload.get("source_candidate_pr_number") or 0) != int(output_pr or 0)
                or handoff_payload.get("source_candidate_head_sha") != output_head
                or handoff_payload.get("prior_candidate_head_sha") != launch.get("candidate_head_sha")
                or handoff_payload.get("dispatch_id") != launch.get("dispatch_id")
                or int(handoff_payload.get("fixture_candidate_pr_number") or 0) != preflight.candidate_pr_number
                or int(handoff_payload.get("callback_sequence") or 0) != int(callback.get("sequence") or 0)
                or str(handoff_payload.get("callback_event_id") or "") != str(callback.get("event_id") or "")
                or not (int(row.get("sequence") or 0) < int(callback.get("sequence") or 0))
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


def _store_event_uri(repository: str, snapshot: Any, row: Mapping[str, Any]) -> str:
    ref_sha = str(getattr(snapshot, "ref_sha", "") or "")
    if len(ref_sha) != 40:
        raise V03DogfoodPostRunFinalizerError("protected Store snapshot lacks exact ref SHA")
    path = event_path(
        str(row.get("operation_id") or ""),
        int(row.get("sequence") or 0),
        str(row.get("event_id") or ""),
    )
    return f"https://github.com/{repository}/blob/{ref_sha}/{path}"


def _store_object_uri(repository: str, snapshot: Any, path: str) -> str:
    ref_sha = str(getattr(snapshot, "ref_sha", "") or "")
    if len(ref_sha) != 40:
        raise V03DogfoodPostRunFinalizerError("protected Store snapshot lacks exact ref SHA")
    return f"https://github.com/{repository}/blob/{ref_sha}/{path}"


def _status_at(events: list[dict[str, Any]], sequence: int) -> str:
    if not events:
        raise V03DogfoodPostRunFinalizerError("cannot reconstruct state from empty Operation history")
    operation_id = str(events[0].get("operation_id") or "")
    files = {}
    for row in events:
        if int(row.get("sequence") or 0) > sequence:
            break
        path = event_path(
            operation_id,
            int(row.get("sequence") or 0),
            str(row.get("event_id") or ""),
        )
        files[path] = row
    try:
        return str(rebuild_projection(StoreSnapshot(ref_sha=None, files=files), operation_id)["status"])
    except Exception as exc:
        raise V03DogfoodPostRunFinalizerError("durable milestone state cannot be independently reconstructed") from exc


def _persist_cycles(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bind every Persist triplet to its exact translated Feature Event."""
    translated: dict[str, dict[str, Any]] = {}
    for row in events:
        if row.get("event_type") != "feature.event.translated":
            continue
        payload = row.get("payload") or {}
        feature_event_id = str(payload.get("feature_event_id") or "")
        feature_event = payload.get("feature_event")
        if (
            not feature_event_id
            or feature_event_id in translated
            or not isinstance(feature_event, dict)
            or feature_event.get("id") != feature_event_id
            or digest_json(feature_event) != payload.get("feature_event_digest")
            or feature_event.get("expected_revision") != payload.get("feature_revision")
        ):
            raise V03DogfoodPostRunFinalizerError("translated Feature Event identity/digest is not exact")
        translated[feature_event_id] = row

    phases: dict[str, dict[str, dict[str, Any]]] = {}
    for row in events:
        event_type = str(row.get("event_type") or "")
        if event_type not in {"persist.requested", "persist.linearized", "persist.confirmed"}:
            continue
        payload = row.get("payload") or {}
        feature_event_id = str(payload.get("feature_event_id") or "")
        bucket = phases.setdefault(feature_event_id, {})
        if not feature_event_id or event_type in bucket:
            raise V03DogfoodPostRunFinalizerError("Persist phase identity is missing or duplicated")
        bucket[event_type] = row

    if set(phases) != set(translated):
        raise V03DogfoodPostRunFinalizerError("translated Feature Event set differs from Persisted Event set")
    cycles: list[dict[str, Any]] = []
    for feature_event_id, translated_row in translated.items():
        bucket = phases.get(feature_event_id) or {}
        if set(bucket) != {"persist.requested", "persist.linearized", "persist.confirmed"}:
            raise V03DogfoodPostRunFinalizerError("Feature Event lacks complete Persist triplet")
        requested, linearized, confirmed = (
            bucket["persist.requested"], bucket["persist.linearized"], bucket["persist.confirmed"]
        )
        sequences = (
            int(translated_row.get("sequence") or 0),
            int(requested.get("sequence") or 0),
            int(linearized.get("sequence") or 0),
            int(confirmed.get("sequence") or 0),
        )
        if not (sequences[0] < sequences[1] < sequences[2] < sequences[3]):
            raise V03DogfoodPostRunFinalizerError("translated/Persist phases are out of order")
        tp = translated_row.get("payload") or {}
        rp, lp, cp = (row.get("payload") or {} for row in (requested, linearized, confirmed))
        stable = ("feature_event_id", "expected_revision", "target_ref", "candidate_head_sha")
        if any(rp.get(name) != lp.get(name) or rp.get(name) != cp.get(name) for name in stable):
            raise V03DogfoodPostRunFinalizerError("Feature Persist identity drifted across phases")
        if (
            rp.get("expected_revision") != tp.get("feature_revision")
            or rp.get("target_ref") != tp.get("target_ref")
            or rp.get("candidate_head_sha") != tp.get("candidate_head_sha")
        ):
            raise V03DogfoodPostRunFinalizerError("Persist is not bound to exact translated Feature truth")
        expected_revision = rp.get("expected_revision")
        result_revision = cp.get("result_revision")
        if not isinstance(expected_revision, int) or result_revision != expected_revision + 1:
            raise V03DogfoodPostRunFinalizerError("Feature Persist confirmation is not exact next revision")
        cycles.append({
            "feature_event_id": feature_event_id,
            "callback_id": str(tp.get("callback_id") or ""),
            "expected_revision": expected_revision,
            "result_revision": result_revision,
            "target_ref": rp.get("target_ref"),
            "candidate_head_sha": rp.get("candidate_head_sha"),
            "translated": translated_row,
            "requested": requested,
            "linearized": linearized,
            "confirmed": confirmed,
            "confirmed_sequence": sequences[3],
        })
    return sorted(cycles, key=lambda row: int(row["translated"].get("sequence") or 0))


def _has_change(translations: list[dict[str, Any]], kind: str, **expected: Any) -> bool:
    for translated in translations:
        feature_event = (translated.get("payload") or {}).get("feature_event") or {}
        for change in feature_event.get("changes") or []:
            if not isinstance(change, dict) or change.get("kind") != kind:
                continue
            record = change.get("record") if isinstance(change.get("record"), dict) else {}
            if all(record.get(key, change.get(key)) == value for key, value in expected.items()):
                return True
    return False


def _verify_result_semantics(
    scenario: str,
    step: str,
    role: str,
    worker_payload: Mapping[str, Any],
    translations: list[dict[str, Any]],
) -> None:
    if role == "developer":
        if worker_payload.get("status") != "COMPLETED":
            raise V03DogfoodPostRunFinalizerError("Developer milestone is not an exact completed Worker result")
        if step == "IMPLEMENTATION_WORK":
            if len(translations) != 1 or not _has_change(translations, "artifact-record", type="implementation") or not _has_change(
                translations, "stage", id="code-review", status="READY"
            ):
                raise V03DogfoodPostRunFinalizerError("Developer implementation milestone lacks exact Feature transition")
        elif step == "CODE_REMEDIATION":
            if (
                len(translations) != 2
                or not _has_change(translations, "artifact-record", type="implementation")
                or not _has_change(translations, "task", status="DONE")
                or not _has_change(translations, "artifact", status="superseded")
            ):
                raise V03DogfoodPostRunFinalizerError("remediation milestone lacks exact replacement/supersession lifecycle")
        else:
            raise V03DogfoodPostRunFinalizerError("Developer result occurred at unsupported frozen step")
        return
    if role == "reviewer":
        expected_verdict = "REWORK" if scenario == "review_remediation" and step == "CODE_REVIEW" else "PASS"
        if worker_payload.get("verdict") != expected_verdict or len(translations) != 1:
            raise V03DogfoodPostRunFinalizerError("Reviewer verdict differs from frozen milestone")
        if expected_verdict == "REWORK":
            if not _has_change(translations, "task-record", kind="remediation"):
                raise V03DogfoodPostRunFinalizerError("Reviewer REWORK lacks durable remediation task decision")
        elif not _has_change(translations, "gate", id="code-gate", status="PASS"):
            raise V03DogfoodPostRunFinalizerError("Reviewer PASS lacks durable code Gate PASS")
        return
    if role == "qa":
        if worker_payload.get("verdict") != "PASS" or len(translations) != 1 or not _has_change(
            translations, "gate", id="verification-gate", status="PASS"
        ):
            raise V03DogfoodPostRunFinalizerError("QA milestone lacks durable verification Gate PASS")
        return
    raise V03DogfoodPostRunFinalizerError("unexpected Worker role in release milestone")


def _dispatch_chains(
    scenario: str,
    events: list[dict[str, Any]],
    dispatches: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    generation: int,
) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    cycle_by_callback: dict[str, list[dict[str, Any]]] = {}
    for cycle in cycles:
        cycle_by_callback.setdefault(str(cycle.get("callback_id") or ""), []).append(cycle)

    for index, dispatch in enumerate(dispatches):
        start = int(dispatch["claim_sequence"])
        end = int(dispatches[index + 1]["selected_sequence"]) if index + 1 < len(dispatches) else 10**18
        in_segment = lambda row: start < int(row.get("sequence") or 0) < end
        claim = next(
            (row for row in events if int(row.get("sequence") or 0) == start and row.get("event_type") == "dispatch.claimed"),
            None,
        )
        if claim is None:
            raise V03DogfoodPostRunFinalizerError("selected dispatch lost exact protected claim")
        claim_payload = claim.get("payload") or {}
        key = str(claim_payload.get("external_dispatch_key") or "")
        auths = [
            row for row in events
            if in_segment(row)
            and row.get("event_type") == "dispatch.launch.authorized"
            and (row.get("payload") or {}).get("external_dispatch_key") == key
        ]
        if len(auths) != 1:
            raise V03DogfoodPostRunFinalizerError("dispatch lacks one exact launch authorization")
        auth = auths[0]
        auth_payload = auth.get("payload") or {}
        if auth.get("operation_generation") != generation or auth_payload.get("role") != dispatch["role"]:
            raise V03DogfoodPostRunFinalizerError("dispatch launch role/generation binding drifted")
        lookups = [
            row for row in events
            if in_segment(row)
            and row.get("event_type") == "dispatch.launch.lookup-recorded"
            and (row.get("payload") or {}).get("external_dispatch_key") == key
            and (row.get("payload") or {}).get("lookup_state") == "LAUNCHED"
        ]
        if len(lookups) != 1 or not str((lookups[0].get("payload") or {}).get("receipt_id") or "").isdigit():
            raise V03DogfoodPostRunFinalizerError("dispatch lacks one exact LAUNCHED runtime receipt")
        lookup = lookups[0]
        chain = {
            "step": dispatch["step"],
            "role": dispatch["role"],
            "selected": next(row for row in events if int(row.get("sequence") or 0) == int(dispatch["selected_sequence"])),
            "claim": claim,
            "authorization": auth,
            "lookup": lookup,
            "run_id": int((lookup.get("payload") or {})["receipt_id"]),
            "events": [auth, lookup],
        }
        if scenario == "session_recovery":
            chains.append(chain)
            continue
        callbacks = [
            row for row in events
            if in_segment(row)
            and row.get("event_type") == "worker.callback.recorded"
            and (row.get("payload") or {}).get("external_dispatch_key") == key
        ]
        if len(callbacks) != 1:
            raise V03DogfoodPostRunFinalizerError("consumed dispatch lacks one exact protected callback")
        callback = callbacks[0]
        callback_id = str((callback.get("payload") or {}).get("callback_id") or "")
        accepted = [
            row for row in events
            if in_segment(row)
            and row.get("event_type") == "worker.result.validated"
            and (row.get("payload") or {}).get("callback_id") == callback_id
        ]
        rejected = [
            row for row in events
            if in_segment(row)
            and row.get("event_type") == "worker.result.rejected"
            and (row.get("payload") or {}).get("callback_id") == callback_id
        ]
        if len(accepted) != 1 or rejected:
            raise V03DogfoodPostRunFinalizerError("callback lacks one accepted role result")
        accepted_payload = accepted[0].get("payload") or {}
        if accepted_payload.get("role") != dispatch["role"] or accepted_payload.get("dispatch_id") != auth_payload.get("dispatch_id"):
            raise V03DogfoodPostRunFinalizerError("accepted Worker result is not bound to exact launch")
        envelope = (callback.get("payload") or {}).get("trusted_callback_envelope")
        worker_payload = (envelope or {}).get("worker_payload") if isinstance(envelope, dict) else None
        if not isinstance(worker_payload, dict):
            raise V03DogfoodPostRunFinalizerError("accepted callback lacks original sealed Worker payload")
        translations = [
            cycle["translated"] for cycle in cycle_by_callback.get(callback_id, [])
            if in_segment(cycle["translated"])
        ]
        owned_cycles = [
            cycle for cycle in cycle_by_callback.get(callback_id, [])
            if in_segment(cycle["translated"])
        ]
        if not owned_cycles or any(cycle["confirmed_sequence"] >= end for cycle in owned_cycles):
            raise V03DogfoodPostRunFinalizerError("accepted Worker result lacks bounded exact Persist authority")
        _verify_result_semantics(scenario, dispatch["step"], dispatch["role"], worker_payload, translations)
        chain.update(
            callback=callback,
            accepted=accepted[0],
            cycles=owned_cycles,
            events=[auth, lookup, callback, accepted[0], *[
                phase
                for cycle in owned_cycles
                for phase in (cycle["translated"], cycle["requested"], cycle["linearized"], cycle["confirmed"])
            ]],
        )
        chains.append(chain)
    return chains


def _reconstruct_release_authority(
    scenario: str,
    snapshot: Any,
    events: list[dict[str, Any]],
    projection: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    repository: str,
    source_run_id: int,
) -> tuple[list[dict[str, Any]], Mapping[str, bool], str, str]:
    profile = SCENARIO_PROFILES.get(scenario)
    expected_roles = SCENARIO_ROLE_SEQUENCES.get(scenario)
    if profile is None or expected_roles is None:
        raise V03DogfoodPostRunFinalizerError("scenario escaped frozen evidence inventory")
    end_state = str(projection.get("status") or "")
    if end_state != profile["end_state"]:
        raise V03DogfoodPostRunFinalizerError("durable final state differs from frozen profile")
    generation = int(projection.get("generation") if projection.get("generation") is not None else -1)
    if generation < 0:
        raise V03DogfoodPostRunFinalizerError("protected Store lacks valid Operation generation")
    if any(
        int(row.get("operation_generation") if row.get("operation_generation") is not None else -1) != generation
        for row in events
    ):
        raise V03DogfoodPostRunFinalizerError("dogfood Operation history crossed an unexpected generation")

    dispatches = _selected_dispatches(events)
    roles = tuple(str(row.get("role") or "") for row in dispatches)
    if roles != expected_roles:
        raise V03DogfoodPostRunFinalizerError(
            f"durable role sequence differs from frozen scenario: expected {expected_roles}, got {roles}"
        )
    expected_steps = {
        "happy_path": ("IMPLEMENTATION_WORK", "CODE_REVIEW", "VERIFICATION_QA"),
        "review_remediation": (
            "IMPLEMENTATION_WORK", "CODE_REVIEW", "CODE_REMEDIATION", "CODE_REREVIEW", "VERIFICATION_QA"
        ),
        "session_recovery": ("IMPLEMENTATION_WORK",),
    }[scenario]
    steps = tuple(str(row.get("step") or "") for row in dispatches)
    if steps != expected_steps:
        raise V03DogfoodPostRunFinalizerError("durable selected-step sequence differs from frozen scenario")

    starts = [row for row in events if row.get("event_type") == "operation.started"]
    if len(starts) != 1:
        raise V03DogfoodPostRunFinalizerError("dogfood history must contain exactly one operation.started")
    cycles = _persist_cycles(events)
    if scenario == "session_recovery" and cycles:
        raise V03DogfoodPostRunFinalizerError("session recovery unexpectedly persisted unconsumed Worker output")
    chains = _dispatch_chains(scenario, events, dispatches, cycles, generation)
    if scenario != "session_recovery":
        all_cycles = [cycle for chain in chains for cycle in chain.get("cycles", [])]
        if len(all_cycles) != len(cycles):
            raise V03DogfoodPostRunFinalizerError("Persisted Feature Event is not owned by one accepted callback")
        previous_revision = None
        for cycle in cycles:
            if cycle["target_ref"] != observation.get("target_ref"):
                raise V03DogfoodPostRunFinalizerError("Feature Persist escaped frozen target ref")
            if previous_revision is not None and cycle["expected_revision"] != previous_revision:
                raise V03DogfoodPostRunFinalizerError("Feature revision chain is discontinuous")
            previous_revision = cycle["result_revision"]
        done_rows = [row for row in events if row.get("event_type") == "operation.done"]
        completion_notifications = [
            row for row in events
            if row.get("event_type") == "notification.created"
            and (row.get("payload") or {}).get("notification_type") == "operation.completed"
        ]
        if (
            len(done_rows) != 1
            or len(completion_notifications) != 1
            or (done_rows[0].get("payload") or {}).get("feature_revision") != cycles[-1]["result_revision"]
            or int(completion_notifications[0].get("sequence") or 0) <= int(done_rows[0].get("sequence") or 0)
        ):
            raise V03DogfoodPostRunFinalizerError("terminal DONE/completion Notification is not bound to final Feature revision")
        notification_id = str((completion_notifications[0].get("payload") or {}).get("notification_id") or "")
        notification = snapshot.get(notification_path(notification_id))
        if (
            not isinstance(notification, dict)
            or notification.get("notification_type") != "operation.completed"
            or notification.get("operation_id") != observation.get("operation_id")
            or int(notification.get("operation_generation") if notification.get("operation_generation") is not None else -1) != generation
        ):
            raise V03DogfoodPostRunFinalizerError("completion Notification immutable record binding differs")
    else:
        done_rows, completion_notifications = [], []

    def event_uris(rows: list[dict[str, Any]]) -> list[str]:
        return [_store_event_uri(repository, snapshot, row) for row in rows]

    expected_profile = {name: (state, set(categories)) for name, state, categories in profile["milestones"]}
    milestones: list[dict[str, Any]] = []
    def add_milestone(name: str, state: str, rows: list[dict[str, Any]], run_ids=(), extra_uris=()):
        expected_state, categories = expected_profile[name]
        if state != expected_state:
            raise V03DogfoodPostRunFinalizerError(
                f"durable milestone {name} state differs: expected {expected_state}, got {state}"
            )
        uris = [_run_uri(repository, source_run_id)]
        uris.extend(_run_uri(repository, int(run_id)) for run_id in run_ids)
        uris.extend(event_uris(rows))
        uris.extend(str(uri) for uri in extra_uris)
        milestones.append({
            "name": name,
            "state_after": state,
            "evidence_categories": sorted(categories),
            "evidence_uris": list(dict.fromkeys(uris)),
        })

    if scenario == "happy_path":
        start_state = _status_at(events, int(starts[0]["sequence"]))
        add_milestone("operation-started", start_state, [starts[0]])
        add_milestone(
            "developer-completed",
            _status_at(events, int(chains[1]["authorization"]["sequence"])),
            chains[0]["events"] + [chains[1]["authorization"]],
            (chains[0]["run_id"],),
        )
        add_milestone(
            "independent-review-passed",
            _status_at(events, int(chains[2]["authorization"]["sequence"])),
            chains[1]["events"] + [chains[2]["authorization"]],
            (chains[1]["run_id"],),
        )
        add_milestone(
            "qa-passed-and-done",
            _status_at(events, int(completion_notifications[0]["sequence"])),
            chains[2]["events"] + [done_rows[0], completion_notifications[0]],
            (chains[2]["run_id"],),
            (_store_object_uri(repository, snapshot, notification_path(notification_id)),),
        )
        independent_review, remediation_round_trip, new_session_discovery = True, False, False
    elif scenario == "review_remediation":
        start_state = _status_at(events, int(starts[0]["sequence"]))
        add_milestone(
            "developer-completed",
            _status_at(events, int(chains[1]["authorization"]["sequence"])),
            chains[0]["events"] + [chains[1]["authorization"]],
            (chains[0]["run_id"],),
        )
        reviewer_confirm = max(int(cycle["confirmed_sequence"]) for cycle in chains[1]["cycles"])
        add_milestone(
            "reviewer-requested-changes",
            _status_at(events, reviewer_confirm),
            chains[1]["events"],
            (chains[1]["run_id"],),
        )
        add_milestone(
            "remediation-completed",
            _status_at(events, int(chains[3]["authorization"]["sequence"])),
            chains[2]["events"] + [chains[3]["authorization"]],
            (chains[2]["run_id"],),
        )
        add_milestone(
            "independent-re-review-passed",
            _status_at(events, int(chains[4]["authorization"]["sequence"])),
            chains[3]["events"] + [chains[4]["authorization"]],
            (chains[3]["run_id"],),
        )
        add_milestone(
            "qa-passed",
            _status_at(events, int(completion_notifications[0]["sequence"])),
            chains[4]["events"] + [done_rows[0], completion_notifications[0]],
            (chains[4]["run_id"],),
            (_store_object_uri(repository, snapshot, notification_path(notification_id)),),
        )
        independent_review, remediation_round_trip, new_session_discovery = True, True, False
    else:
        lookup = chains[0]["lookup"]
        start_state = _status_at(events, int(lookup["sequence"]))
        trace_path = session_trace_path(str(observation.get("operation_id") or ""))
        trace = snapshot.get(trace_path)
        pending = tuple(sorted(str(value) for value in (projection.get("pending_decisions") or [])))
        unread = tuple(sorted(str(value) for value in (projection.get("unread_notifications") or [])))
        if (
            not isinstance(trace, dict)
            or trace.get("schema_version") != SESSION_TRACE_SCHEMA
            or trace.get("scenario") != "session_recovery"
            or trace.get("operation_id") != observation.get("operation_id")
            or int(trace.get("operation_generation") if trace.get("operation_generation") is not None else -1) != generation
            or str(trace.get("repository") or "").lower() != repository.lower()
            or trace.get("feature_id") != observation.get("feature_id")
            or trace.get("target_ref") != observation.get("target_ref")
            or trace.get("original_end_status") != "WAITING_EXTERNAL"
            or trace.get("final_status") != "NEEDS_USER"
            or tuple((trace.get("original_session") or {}).get("response_ids") or ()) != tuple(observation.get("response_ids") or ())
            or tuple((trace.get("original_session") or {}).get("function_call_ids") or ()) != tuple(observation.get("function_call_ids") or ())
            or tuple((trace.get("recovery_session") or {}).get("response_ids") or ()) != tuple(observation.get("recovery_response_ids") or ())
            or tuple((trace.get("recovery_session") or {}).get("function_call_ids") or ()) != tuple(observation.get("recovery_function_call_ids") or ())
            or str(trace.get("decision_id") or "") not in pending
            or str(trace.get("notification_id") or "") not in unread
            or (trace.get("inbox_discovery") or {}).get("decision_id") != trace.get("decision_id")
            or (trace.get("inbox_discovery") or {}).get("notification_id") != trace.get("notification_id")
            or (trace.get("inbox_discovery") or {}).get("call_id") not in tuple(observation.get("recovery_function_call_ids") or ())
        ):
            raise V03DogfoodPostRunFinalizerError("protected session recovery trace does not match exact durable/raw identities")
        decision_id = str(trace["decision_id"])
        notification_id = str(trace["notification_id"])
        decision = snapshot.get(decision_path(decision_id))
        notification = snapshot.get(notification_path(notification_id))
        if (
            not isinstance(decision, dict)
            or not isinstance(notification, dict)
            or decision.get("operation_id") != observation.get("operation_id")
            or notification.get("operation_id") != observation.get("operation_id")
            or int(decision.get("operation_generation") if decision.get("operation_generation") is not None else -1) != generation
            or int(notification.get("operation_generation") if notification.get("operation_generation") is not None else -1) != generation
            or notification.get("decision_id") != decision_id
        ):
            raise V03DogfoodPostRunFinalizerError("session Decision/Notification immutable records differ from recovery trace")
        decision_events = [
            row for row in events
            if row.get("event_type") == "decision.requested"
            and (row.get("payload") or {}).get("decision_id") == decision_id
        ]
        notification_events = [
            row for row in events
            if row.get("event_type") == "notification.created"
            and (row.get("payload") or {}).get("notification_id") == notification_id
        ]
        if len(decision_events) != 1 or len(notification_events) != 1:
            raise V03DogfoodPostRunFinalizerError("session user-item events are not exact")
        trace_uri = _store_object_uri(repository, snapshot, trace_path)
        add_milestone(
            "durable-state-created",
            start_state,
            [chains[0]["selected"], chains[0]["claim"], chains[0]["authorization"], lookup],
            (chains[0]["run_id"],),
        )
        add_milestone(
            "original-session-ended",
            str(trace["original_end_status"]),
            [],
            (),
            (trace_uri,),
        )
        final_sequence = max(int(decision_events[0]["sequence"]), int(notification_events[0]["sequence"]))
        add_milestone(
            "new-session-discovered-operation-and-user-items",
            _status_at(events, final_sequence),
            [decision_events[0], notification_events[0]],
            (),
            (
                trace_uri,
                _store_object_uri(repository, snapshot, decision_path(decision_id)),
                _store_object_uri(repository, snapshot, notification_path(notification_id)),
            ),
        )
        independent_review, remediation_round_trip, new_session_discovery = False, False, True

    assertions = {
        "durable_operation_state": True,
        "independent_review_observed": independent_review,
        "remediation_round_trip_observed": remediation_round_trip,
        "new_session_discovery_observed": new_session_discovery,
    }
    expected_names = [name for name, _state, _categories in profile["milestones"]]
    if [row["name"] for row in milestones] != expected_names:
        raise V03DogfoodPostRunFinalizerError("reconstructed milestone set/order differs from frozen profile")
    return milestones, assertions, start_state, end_state


def _milestone_facts(
    scenario: str,
    repository: str,
    source_run_id: int,
    finalizer_run_id: int,
    worker_run_ids: list[int],
    categories: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    expected_names = [name for name, _state, _categories in SCENARIO_PROFILES[scenario]["milestones"]]
    if set(categories) != set(expected_names):
        raise V03DogfoodPostRunFinalizerError("reconstructed milestone set differs from frozen profile")
    expected_workers = len(SCENARIO_ROLE_SEQUENCES[scenario])
    if len(worker_run_ids) != expected_workers:
        raise V03DogfoodPostRunFinalizerError("milestone evidence lacks exact Worker run sequence")
    source_uri = _run_uri(repository, source_run_id)
    finalizer_uri = _run_uri(repository, finalizer_run_id)
    worker_uris = [_run_uri(repository, value) for value in worker_run_ids]
    if scenario == "happy_path":
        evidence = {
            "operation-started": [source_uri, finalizer_uri],
            "developer-completed": [source_uri, worker_uris[0], finalizer_uri],
            "independent-review-passed": [source_uri, worker_uris[1], finalizer_uri],
            "qa-passed-and-done": [source_uri, worker_uris[2], finalizer_uri],
        }
    elif scenario == "review_remediation":
        evidence = {
            "developer-completed": [source_uri, worker_uris[0], finalizer_uri],
            "reviewer-requested-changes": [source_uri, worker_uris[1], finalizer_uri],
            "remediation-completed": [source_uri, worker_uris[2], finalizer_uri],
            "independent-re-review-passed": [source_uri, worker_uris[3], finalizer_uri],
            "qa-passed": [source_uri, worker_uris[4], finalizer_uri],
        }
    else:
        evidence = {
            "durable-state-created": [source_uri, finalizer_uri],
            "original-session-ended": [source_uri, finalizer_uri],
            "new-session-discovered-operation-and-user-items": [
                source_uri, worker_uris[0], finalizer_uri
            ],
        }
    return [
        {
            "name": name,
            "evidence_categories": sorted(categories[name]),
            "evidence_uris": evidence[name],
        }
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

    snapshot, events, projection = _durable_operation_facts(preflight, observation)
    receipt = _durable_receipt(events, observation)
    milestones, assertions, start_state, end_state = _reconstruct_release_authority(
        scenario,
        snapshot,
        events,
        projection,
        observation,
        repository=repository,
        source_run_id=source_run_id,
    )
    generation = int(projection.get("generation") if projection.get("generation") is not None else -1)
    if generation < 0:
        raise V03DogfoodPostRunFinalizerError("protected Store lacks valid Operation generation")
    categories = {row["name"]: set(row["evidence_categories"]) for row in milestones}

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
        "start_state": start_state,
        "end_state": end_state,
        "milestones": milestones,
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
