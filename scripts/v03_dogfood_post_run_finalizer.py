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
from v03_dogfood_full_composition import validate_recovery_execution_seal, recovery_execution_binding, recovery_route, REPLACEMENT_HISTORY_BLOBS, _recovery_document_blob, read_dogfood_handoff, V03DogfoodCompositionError
from v03_dogfood_full_composition import (
    post_handoff_present, validate_post_handoff_reconciliation, POST_HANDOFF_RUN,
    POST_HANDOFF_SOURCE, POST_HANDOFF_CALLBACK, POST_HANDOFF_ADMISSION,
)
from operator_vertical import VerticalInvariantError
from v03_dogfood_runtime_driver import assemble_preflight, _head
from v03_dogfood_scenario_runner import SCENARIO_ROLE_SEQUENCES, STEP_ROLE
from validate_v03_dogfood_evidence import SCENARIO_PROFILES

VERIFIER_IDENTITY = "ai-sdlc/v0.3-production-dogfood-post-run-verifier/v1"
RUNTIME_KIND = "github-actions/gh-aw-production"
RECOVERY_OPERATION_ID = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
RECOVERY_EXTERNAL_KEY = "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1"
RECOVERY_SCHEMA = "ai-sdlc.v03-dogfood-bounded-recovery/v1"
RECOVERY_BASE_PATH = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-bounded-recovery"
RECOVERY_AUTHORIZATION_PATH = RECOVERY_BASE_PATH + "/authorization.json"
RECOVERY_ATTEMPT_PATH = RECOVERY_BASE_PATH + "/create-attempt.json"
RECOVERY_RECEIPT_PATH = RECOVERY_BASE_PATH + "/sealed-receipt.json"
RECOVERY_OBSERVATION_DIGEST = "sha256:a86b7ead37bf96abe9b6e43098b7873b821833c6d93c720ed7409835af18916f"


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


def _validated_recovery_chain(snapshot: Any, *, execution_binding) -> dict[str, Any]:
    try:
        route = recovery_route(snapshot)
    except VerticalInvariantError as exc:
        raise V03DogfoodPostRunFinalizerError("fixed recovery route is incomplete or corrupt") from exc
    authorization, attempt = route["authorization"], route["attempt"]
    sealed = snapshot.get(route["receipt_path"])
    if not all(isinstance(row, dict) for row in (authorization, attempt, sealed)):
        raise V03DogfoodPostRunFinalizerError("finalizer lacks complete recovery fact chain")
    try:
        validate_recovery_execution_seal(snapshot, sealed, execution_binding=execution_binding)
    except VerticalInvariantError as exc:
        raise V03DogfoodPostRunFinalizerError("finalizer recovery continuation/source bridge drifted") from exc
    authorization_digest = "sha256:" + digest_json(authorization)
    attempt_digest = "sha256:" + digest_json(attempt)
    exact = {
        "schema_version": RECOVERY_SCHEMA,
        "operation_id": RECOVERY_OPERATION_ID,
        "external_dispatch_key": RECOVERY_EXTERNAL_KEY,
        "historical_runtime_receipt_identity": "37204777409",
        "historical_observation_digest": RECOVERY_OBSERVATION_DIGEST,
        "authorization_digest": authorization_digest,
        "create_attempt_digest": attempt_digest,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "role": "developer",
        "stage": "implementation",
        "run_status": "completed",
        "run_conclusion": "success",
    }
    if (
        any(sealed.get(key) != value for key, value in exact.items())
        or attempt.get("authorization_digest") != authorization_digest
        or attempt.get("status") != "ARMED"
        or sealed.get("provider_fence_digest") != authorization.get("provider_fence_digest")
        or sealed.get("worker_blobs") != authorization.get("worker_blobs")
        or sealed.get("trusted_context_digest") != authorization.get("trusted_context_digest")
        or sealed.get("source_head_sha") != authorization.get("installation_commit_sha")
        or sealed.get("display_title") != "AI-SDLC gh-aw " + str(sealed.get("recovery_dispatch_key") or "")
        or not str(sealed.get("receipt_id") or "").isdigit()
        or not isinstance(sealed.get("output_candidate_pr_number"), int)
        or sealed.get("output_candidate_pr_number") < 1
        or not str(sealed.get("output_candidate_head_sha") or "")
        or not str(sealed.get("safe_output_uri") or "")
        or sealed.get("safe_output_digest")
           != "sha256:" + digest_json({"trusted_uri": sealed.get("safe_output_uri")})
        or not str(sealed.get("resolved_run_digest") or "").startswith("sha256:")
    ):
        raise V03DogfoodPostRunFinalizerError("finalizer recovery fact-chain digest/identity drifted")
    for key in (
        "operation_id", "operation_generation", "semantic_effect_key", "external_dispatch_key",
        "recovery_dispatch_key", "recovery_dispatch_id", "workflow_file", "installation_commit_sha",
        "source_head_sha", "target_repository", "head_branch", "event", "display_title",
        "trusted_context_digest", "feature_id", "target_ref", "task_id", "task_identity",
        "stage", "role", "expected_revision", "candidate_pr_number", "candidate_head_sha",
        "provider_fence_digest", "historical_observation_digest", "worker_blobs",
    ):
        if attempt.get(key) != authorization.get(key) or sealed.get(key) != authorization.get(key):
            raise V03DogfoodPostRunFinalizerError(f"finalizer recovery chain lost {key} binding")
    return sealed


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


def _reviewer_route(preflight):
    from v03_dogfood_full_composition import reviewer_replacement_route
    return reviewer_replacement_route(preflight.composition.runtime.backend.read_snapshot(),
        consumer_binding=recovery_execution_binding(preflight.composition.policy_authority))


def _durable_receipt(preflight: Any, events: list[dict[str, Any]], observation: Mapping[str, Any]) -> Mapping[str, Any]:
    run_ids: list[int] = []
    operation_id = str(observation.get("operation_id") or "")
    for row in events:
        if row.get("event_type") != "dispatch.launch.lookup-recorded":
            continue
        payload = row.get("payload") or {}
        if payload.get("lookup_state") != "LAUNCHED":
            continue
        receipt = str(payload.get("receipt_id") or "")
        key = str(payload.get("external_dispatch_key") or "")
        if operation_id == RECOVERY_OPERATION_ID and key == RECOVERY_EXTERNAL_KEY:
            if receipt != "37204777409":
                raise V03DogfoodPostRunFinalizerError("historical launch receipt identity drifted")
            continue
        from v03_dogfood_full_composition import REVIEWER_OLD_KEY, REVIEWER_FAILED_RUN
        if operation_id == RECOVERY_OPERATION_ID and key == REVIEWER_OLD_KEY:
            if receipt != str(REVIEWER_FAILED_RUN):
                raise V03DogfoodPostRunFinalizerError("Reviewer historical receipt changed")
            receipt = _reviewer_route(preflight)["receipt_id"]
        if not receipt.isdigit() or int(receipt) < 1:
            raise V03DogfoodPostRunFinalizerError("durable LAUNCHED lookup lacks exact Actions receipt")
        run_ids.append(int(receipt))
    if operation_id == RECOVERY_OPERATION_ID:
        sealed = _validated_recovery_chain(preflight.composition.runtime.backend.read_snapshot(), execution_binding=recovery_execution_binding(
            preflight.composition.policy_authority,
        ))
        run_ids.insert(0, int(sealed["receipt_id"]))
    declared = [int(value) for value in (observation.get("workflow_run_ids") or [])]
    if run_ids != declared or not run_ids or len(set(run_ids)) != len(run_ids):
        raise V03DogfoodPostRunFinalizerError("protected Store runtime receipt sequence differs from raw observation")
    receipt_identity = str(observation.get("runtime_receipt_identity") or "")
    if receipt_identity != str(run_ids[-1]):
        raise V03DogfoodPostRunFinalizerError("runtime receipt identity is not the final durable launch receipt")
    return {"receipt_identity": receipt_identity, "workflow_run_ids": run_ids}


def _verify_consumed_result(*, events, trusted, resolved, result_source, lookup_sequence, recovery_run=False, reconciliation=None):
    """Verify the original accepted callback; never mint replacement receipts."""
    key, generation = trusted["external_dispatch_key"], trusted["operation_generation"]
    callbacks = [row for row in events if row.get("event_type") == "worker.callback.recorded"
                 and row.get("operation_generation") == generation
                 and (row.get("payload") or {}).get("external_dispatch_key") == key]
    if reconciliation is not None:
        if (trusted["role"] != "developer" or resolved.run.run_id != POST_HANDOFF_RUN
                or len(callbacks) != 2
                or [e["payload"].get("callback_id") for e in callbacks] != [
                    POST_HANDOFF_CALLBACK, reconciliation["observation_callback_id"]]):
            raise V03DogfoodPostRunFinalizerError("run lacks exact rejected/reconciled observation pair")
        callbacks = callbacks[1:]
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
    if recovery_run:
        callback_id = "gh-aw-recovery-callback-" + digest_json({
            "operation_id": trusted["operation_id"],
            "external_dispatch_key": key,
            "recovery_dispatch_key": recovery_run["recovery_dispatch_key"],
            "runtime_receipt_identity": str(resolved.run.run_id),
            "run_id": resolved.run.run_id,
        })[:24]
    else:
        callback_id = "gh-aw-callback-" + digest_json({
            "operation_id": trusted["operation_id"], "generation": generation,
            "external_dispatch_key": key, "runtime_receipt_identity": str(resolved.run.run_id),
            "run_id": resolved.run.run_id,
        })[:24]
    if reconciliation is not None:
        callback_id = reconciliation["observation_callback_id"]
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
    reconciliation = None
    if post_handoff_present(snapshot):
        reconciliation, _, _ = validate_post_handoff_reconciliation(snapshot,
            consumer_binding=recovery_execution_binding(preflight.composition.policy_authority))
    bindings = {}
    ordered: list[dict[str, Any]] = []
    for row in events:
        if row.get("event_type") != "dispatch.launch.lookup-recorded" or (row.get("payload") or {}).get("lookup_state") != "LAUNCHED":
            continue
        lookup = row["payload"]
        key = str(lookup.get("external_dispatch_key") or "")
        run_id = int(lookup["receipt_id"])
        recovery_sealed = None
        reviewer_route = None
        from v03_dogfood_full_composition import REVIEWER_OLD_KEY, REVIEWER_FAILED_RUN, reviewer_trusted_context
        if observation["operation_id"] == RECOVERY_OPERATION_ID and key == REVIEWER_OLD_KEY:
            if run_id != REVIEWER_FAILED_RUN:
                raise V03DogfoodPostRunFinalizerError("Reviewer historical execution changed")
            reviewer_route = _reviewer_route(preflight)
            run_id = int(reviewer_route["receipt_id"])
        if observation["operation_id"] == RECOVERY_OPERATION_ID and key == RECOVERY_EXTERNAL_KEY:
            recovery_sealed = _validated_recovery_chain(snapshot, execution_binding=recovery_execution_binding(
                preflight.composition.policy_authority,
            ))
            if str(run_id) != "37204777409" or recovery_sealed.get("external_dispatch_key") != key:
                raise V03DogfoodPostRunFinalizerError("recovery binding is not separated from historical receipt")
            run_id = int(recovery_sealed["receipt_id"])
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
        result_source = preflight.composition.result_source
        resolve_key = key
        resolve_trusted = trusted
        if recovery_sealed is not None:
            result_source = preflight.composition.recovery_result_source
            resolve_key = str(recovery_sealed["recovery_dispatch_key"])
            resolve_trusted = dict(trusted)
            resolve_trusted["external_dispatch_key"] = resolve_key
            resolve_trusted["dispatch_id"] = str(recovery_sealed["collector_dispatch_id"])
            resolve_trusted["execution_dispatch_id"] = str(recovery_sealed["recovery_dispatch_id"])
            resolve_trusted["task_id"] = str(recovery_sealed["task_id"])
            resolve_trusted["source_head_sha"] = str(recovery_sealed["execution_source_head_sha"])
        if reviewer_route is not None:
            resolve_key = reviewer_route["physical_key"]
            resolve_trusted = reviewer_trusted_context(reviewer_route["authorization"])
        resolved = result_source.resolve(
            external_dispatch_key=resolve_key,
            expected_receipt_identity=str(run_id),
            trusted_context=resolve_trusted,
        )
        if resolved.run.run_id != run_id or resolved.run.role != trusted["role"]:
            raise V03DogfoodPostRunFinalizerError("production result source differs from durable launch")
        if recovery_sealed is not None:
            resolved_digest = "sha256:" + digest_json({
                "run_id": resolved.run.run_id,
                "receipt_identity": resolved.run.receipt_identity,
                "workflow_file": resolved.run.workflow_file,
                "workflow_ref": resolved.run.workflow_ref,
                "event": resolved.run.event,
                "status": resolved.run.status,
                "conclusion": resolved.run.conclusion,
                "display_title": resolved.run.display_title,
                "external_dispatch_key": resolved.run.external_dispatch_key,
                "role": resolved.run.role,
                "task_id": resolved.run.task_id,
                "worker_identity": resolved.run.worker_identity,
                "candidate_pr_number": resolved.run.candidate_pr_number,
                "candidate_head_sha": resolved.run.candidate_head_sha,
            })
            if (
                result_source.safe_output_proof(run_id=resolved.run.run_id) != recovery_sealed.get("safe_output_artifact_proof")
                or "sha256:" + digest_json(recovery_sealed.get("safe_output_artifact_proof")) != recovery_sealed.get("safe_output_artifact_digest")
                or resolved.run.candidate_pr_number != recovery_sealed["output_candidate_pr_number"]
                or resolved.run.candidate_head_sha != recovery_sealed["output_candidate_head_sha"]
                or len(resolved.outputs) != 1
                or resolved.outputs[0].trusted_uri != recovery_sealed["safe_output_uri"]
                or resolved_digest != recovery_sealed["resolved_run_digest"]
            ):
                raise V03DogfoodPostRunFinalizerError("finalizer resolver differs from sealed successful recovery proof")
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
                result_source=result_source,
                lookup_sequence=int(row.get("sequence") or 0),
                recovery_run=recovery_sealed or False,
                reconciliation=reconciliation if run_id == POST_HANDOFF_RUN else None,
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
            reconciled_id = None
            if reconciliation is not None and run_id == POST_HANDOFF_RUN:
                if len(callbacks) != 2 or [e["payload"].get("callback_id") for e in callbacks] != [
                        POST_HANDOFF_CALLBACK, reconciliation["observation_callback_id"]]:
                    raise V03DogfoodPostRunFinalizerError("Developer handoff observation relation differs")
                reconciled_id = reconciliation["observation_callback_id"]
                callbacks = callbacks[:1]  # The actual original applied handoff, never a second PATCH.
            if len(callbacks) != 1:
                raise V03DogfoodPostRunFinalizerError("Developer run lacks one sealed callback for candidate handoff")
            callback = callbacks[0]
            callback_id = str((callback.get("payload") or {}).get("callback_id") or "")

            try:
                fact = read_dogfood_handoff(snapshot, observation["operation_id"], callback_id, require_applied=True)
            except V03DogfoodCompositionError as exc:
                raise V03DogfoodPostRunFinalizerError("Developer handoff sidecar proof differs") from exc
            handoff_payload, applied = fact["intent"], fact["applied"]
            validated = [event for event in events if event.get("event_type") == "worker.result.validated"
                         and event.get("operation_generation") == row.get("operation_generation")
                         and (event.get("payload") or {}).get("callback_id") == (reconciled_id or callback_id)]
            if (len(validated) != 1
                    or handoff_payload["source_candidate_pr_number"] != output_pr
                    or handoff_payload["source_candidate_head_sha"] != output_head
                    or handoff_payload["prior_candidate_head_sha"] != launch.get("candidate_head_sha")
                    or handoff_payload["dispatch_id"] != launch.get("dispatch_id")
                    or handoff_payload["fixture_candidate_pr_number"] != preflight.candidate_pr_number
                    or not (row["sequence"] < callback["sequence"]
                            <= handoff_payload["observed_last_sequence"]
                            <= applied["observed_last_sequence"] < validated[0]["sequence"])):
                raise V03DogfoodPostRunFinalizerError("Developer candidate handoff identity/order differs")
        binding = {
            "repository": preflight.execution.repository, "feature_id": preflight.slot.feature_id,
            "target_ref": preflight.slot.target_ref, "candidate_pr_number": preflight.candidate_pr_number,
            "candidate_input_head_sha": launch.get("candidate_head_sha"),
            "candidate_output_pr_number": output_pr,
            "candidate_output_head_sha": output_head,
            "role": trusted["role"], "workflow": (
                str(recovery_sealed["workflow_file"]) if recovery_sealed is not None
                else preflight.workflows.workflow_for(trusted["role"])
            ),
            "external_dispatch_key": resolve_key, "logical_external_dispatch_key": key,
            "lookup_sequence": int(row.get("sequence") or 0),
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
    inherited_claim = "dispatch-claimed-687520874a948a5c4534e4e30d97b366"
    inherited = any(row.get("event_id") == inherited_claim for row in events)
    if inherited and (
        len(events) < 7
        or [_recovery_document_blob(row) for row in events[:7]] != REPLACEMENT_HISTORY_BLOBS[:7]
    ):
        raise V03DogfoodPostRunFinalizerError("frozen inherited-generation journal changed")
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
        # Generation zero reserved the same logical task/key. Its exact frozen
        # NOT_LAUNCHED/superseded prefix is retained as history, not counted as
        # a second successfully executed Developer role.
        if not (inherited and row.get("event_id") == inherited_claim):
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


def _accepted_callback_facts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    callbacks: dict[str, dict[str, Any]] = {}
    accepted: list[dict[str, Any]] = []
    for row in events:
        payload = row.get("payload") or {}
        if row.get("event_type") == "worker.callback.recorded":
            callback_id = str(payload.get("callback_id") or "")
            envelope = payload.get("trusted_callback_envelope")
            if callback_id and isinstance(envelope, dict):
                if callback_id in callbacks:
                    raise V03DogfoodPostRunFinalizerError("duplicate durable callback identity")
                callbacks[callback_id] = row
        elif row.get("event_type") == "worker.result.validated":
            callback_id = str(payload.get("callback_id") or "")
            callback = callbacks.get(callback_id)
            envelope = (callback.get("payload") or {}).get("trusted_callback_envelope") if callback else None
            if not callback_id or not isinstance(envelope, dict):
                raise V03DogfoodPostRunFinalizerError("accepted result lacks its original protected callback")
            context = envelope.get("trusted_context")
            worker_payload = envelope.get("worker_payload")
            if not isinstance(context, dict) or not isinstance(worker_payload, dict):
                raise V03DogfoodPostRunFinalizerError("accepted callback lacks protected role/result facts")
            role = str(context.get("role") or "")
            if role != str(payload.get("role") or role):
                raise V03DogfoodPostRunFinalizerError("accepted result role differs from protected callback")
            accepted.append({
                "callback_id": callback_id,
                "role": role,
                "worker_payload": worker_payload,
                "callback_sequence": int(callback.get("sequence") or 0),
                "accepted_sequence": int(row.get("sequence") or 0),
            })
    return accepted


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



def _canonical_persist_roles(events, cycles, accepted_callbacks, observation):
    """Classify every real controller Event; no additional Persist is ignored."""
    translated = [e for e in events if e.get("event_type") == "feature.event.translated"
                  and isinstance((e.get("payload") or {}).get("feature_event"), dict)]
    by_id = {}
    for row in translated:
        payload = row["payload"]
        key = payload.get("feature_event_id")
        if key in by_id:
            raise V03DogfoodPostRunFinalizerError("duplicate translated Event")
        by_id[key] = row
    if set(by_id) != {c["feature_event_id"] for c in cycles}:
        raise V03DogfoodPostRunFinalizerError("translated Events and complete Persist cycles differ")
    accepted = {row["callback_id"]: row for row in accepted_callbacks}
    callback_events = {row["payload"]["callback_id"]: row["payload"]["trusted_callback_envelope"]
                       for row in events if row.get("event_type") == "worker.callback.recorded"}
    primary, artifacts = {}, {}
    for cycle in cycles:
        row = by_id[cycle["feature_event_id"]]
        payload, sequence = row["payload"], int(row["sequence"])
        event = payload["feature_event"]
        if (payload.get("feature_event_digest") != digest_json(event)
                or event.get("id") != cycle["feature_event_id"]
                or event.get("feature_id") != observation["feature_id"]
                or event.get("expected_revision") != cycle["expected_revision"]
                or payload.get("feature_revision") != cycle["expected_revision"]
                or cycle["result_revision"] != cycle["expected_revision"] + 1
                or payload.get("target_ref") != cycle["target_ref"]
                or payload.get("candidate_head_sha") != cycle["candidate_head_sha"]
                or sequence >= cycle["requested_sequence"]):
            raise V03DogfoodPostRunFinalizerError("canonical translated/Persist binding differs")
        callback_id = payload.get("callback_id")
        changes = event.get("changes")
        if not isinstance(changes, list) or not changes:
            raise V03DogfoodPostRunFinalizerError("canonical Event lacks changes")
        if callback_id:
            if callback_id not in accepted or accepted[callback_id]["accepted_sequence"] >= sequence:
                raise V03DogfoodPostRunFinalizerError("translated callback is not an accepted observation")
            if payload.get("purpose") == "remediation_artifact_supersession":
                envelope = callback_events[callback_id]
                context = envelope["trusted_context"]
                old_id, new_id = payload.get("superseded_artifact_id"), payload.get("replacement_artifact_id")
                original, replacement = artifacts.get(old_id), artifacts.get(new_id)
                expected_id = "EVT-" + observation["feature_id"] + "-VERTICAL-REMEDIATION-SUPERSEDE-" + digest_json({
                    "callback_id": callback_id, "previous": old_id, "replacement": new_id,
                    "revision": cycle["expected_revision"]})[:12].upper()
                if (callback_id not in primary or context.get("role") != "developer"
                        or context.get("feature_stage") != "code-review"
                        or not original or not replacement or old_id == new_id
                        or original.get("status", "draft") != "draft" or replacement.get("status", "draft") != "draft"
                        or original.get("type") != "implementation" or replacement.get("type") != "implementation"
                        or replacement.get("uri") != envelope["collected_outputs"][0]["trusted_uri"]
                        or changes != [{"kind": "artifact", "id": old_id, "status": "superseded"}]
                        or event["id"] != expected_id):
                    raise V03DogfoodPostRunFinalizerError("remediation supersession differs from exact accepted artifacts")
            elif payload.get("purpose") is not None or callback_id in primary:
                raise V03DogfoodPostRunFinalizerError("accepted callback has extra primary Persist")
            else:
                primary[callback_id] = cycle
        else:
            selected = [e for e in events if e.get("event_type") == "loop.step.selected" and int(e["sequence"]) < sequence]
            selected = selected[-1]["payload"] if selected else {}
            step, stage = selected.get("step"), payload.get("feature_stage")
            if step == "CODE_REVIEW" and stage == "code-review":
                purpose, expected_changes = "CODE-REVIEW-START", [{"kind":"stage","id":"code-review","status":"WORKING"}]
            elif step == "VERIFICATION_QA" and stage == "verification":
                purpose, expected_changes = "VERIFICATION-START", [{"kind":"stage","id":"verification","status":"WORKING"}]
            elif step == "CODE_REMEDIATION" and stage == "code-review" and len(changes) == 1:
                purpose, expected_changes = "CODE-REMEDIATION-START", [{"kind":"task","id":changes[0].get("id"),"status":"WORKING"}]
                if not expected_changes[0]["id"]:
                    raise V03DogfoodPostRunFinalizerError("remediation start lacks task")
            else:
                raise V03DogfoodPostRunFinalizerError("Persist is not an accepted result or exact controller start")
            expected_id = "EVT-" + observation["feature_id"] + "-VERTICAL-" + purpose + "-" + digest_json({
                "revision": cycle["expected_revision"], "changes": expected_changes})[:12].upper()
            if (selected.get("kind") != "persist" or selected.get("feature_revision") != cycle["expected_revision"]
                    or changes != expected_changes or event["id"] != expected_id):
                raise V03DogfoodPostRunFinalizerError("controller stage/task start Event differs")
            dependents = [d for d in _selected_dispatches(events)
                          if d["claim_sequence"] > cycle["requested_sequence"]]
            dependent = dependents[0] if dependents else None
            role = {"CODE_REVIEW": "reviewer", "VERIFICATION_QA": "qa", "CODE_REMEDIATION": "developer"}[step]
            consumers = [callback_events[a["callback_id"]]["trusted_context"] for a in accepted_callbacks
                         if a["callback_sequence"] > cycle["confirmed_sequence"]
                         and callback_events[a["callback_id"]]["trusted_context"].get("expected_revision")
                             == cycle["result_revision"]]
            if (dependent is None or dependent["step"] != step
                    or cycle["confirmed_sequence"] >= dependent["claim_sequence"]
                    or len(consumers) != 1 or consumers[0].get("role") != role
                    or consumers[0].get("feature_stage") != stage
                    or consumers[0].get("target_ref") != cycle["target_ref"]
                    or (step == "CODE_REMEDIATION" and consumers[0].get("task_id") != expected_changes[0]["id"])):
                raise V03DogfoodPostRunFinalizerError("controller start lacks exact dependent dispatch/task")
        for change in changes:
            if change.get("kind") == "artifact":
                identity = change.get("id")
                artifacts[identity] = dict(artifacts.get(identity, {}), **change)
    if set(primary) != set(accepted):
        raise V03DogfoodPostRunFinalizerError("accepted callback lacks exactly one primary Persist")
    return [primary[row["callback_id"]] for row in accepted_callbacks]


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
    accepted_callbacks = _accepted_callback_facts(events)
    validated = [int(row.get("sequence") or 0) for row in validated_rows]
    launched = [
        int(row.get("sequence") or 0)
        for row in events
        if row.get("event_type") == "dispatch.launch.lookup-recorded"
        and (row.get("payload") or {}).get("lookup_state") == "LAUNCHED"
    ]
    expected_validated = 0 if scenario == "session_recovery" else len(expected_roles)
    if (
        len(validated) != expected_validated
        or len(accepted_callbacks) != expected_validated
        or len(launched) != len(expected_roles)
    ):
        raise V03DogfoodPostRunFinalizerError("durable worker validation/launch count differs from frozen role sequence")
    if tuple(row["role"] for row in accepted_callbacks) != expected_roles[:expected_validated]:
        raise V03DogfoodPostRunFinalizerError("accepted callback role sequence differs from frozen scenario")
    persist_cycles = _persist_cycles(events)
    if scenario == "session_recovery":
        if persist_cycles:
            raise V03DogfoodPostRunFinalizerError("session recovery unexpectedly persisted an unconsumed Worker result")
    else:
        canonical = any(isinstance((row.get("payload") or {}).get("feature_event"), dict)
                        for row in events if row.get("event_type") == "feature.event.translated")
        result_cycles = (_canonical_persist_roles(events, persist_cycles, accepted_callbacks, observation)
                         if canonical else persist_cycles)
        if len(result_cycles) != len(validated_rows):
            raise V03DogfoodPostRunFinalizerError("accepted Worker results do not each have one complete Feature Persist")
        previous_revision = None
        for cycle in persist_cycles:
            if previous_revision is not None and cycle["expected_revision"] != previous_revision:
                raise V03DogfoodPostRunFinalizerError("Feature revision chain is discontinuous across milestones")
            previous_revision = cycle["result_revision"]
            if cycle["target_ref"] != observation.get("target_ref"):
                raise V03DogfoodPostRunFinalizerError("Feature Persist escaped frozen target ref")
        for index, (accepted, cycle) in enumerate(zip(validated_rows, result_cycles)):
            accepted_sequence = int(accepted.get("sequence") or 0)
            next_claim = int(dispatches[index + 1]["claim_sequence"]) if index + 1 < len(dispatches) else None
            if cycle["requested_sequence"] <= accepted_sequence:
                raise V03DogfoodPostRunFinalizerError("Feature Persist began before exact Worker acceptance")
            if next_claim is not None and cycle["confirmed_sequence"] >= next_claim:
                raise V03DogfoodPostRunFinalizerError("next dispatch began before prior Feature Persist confirmation")
        done_rows = [row for row in events if row.get("event_type") == "operation.done"]
        if len(done_rows) != 1 or (done_rows[0].get("payload") or {}).get("feature_revision") != persist_cycles[-1]["result_revision"]:
            raise V03DogfoodPostRunFinalizerError("terminal DONE is not bound to final confirmed Feature revision")
    for index, seq in enumerate(validated[:-1]):
        next_claim = int(dispatches[index + 1]["claim_sequence"])
        claim = next(row for row in events if int(row["sequence"]) == next_claim)
        key = claim["payload"]["external_dispatch_key"]
        authorized = [row for row in events if row.get("event_type") == "dispatch.launch.authorized"
                      and row.get("operation_generation") == claim.get("operation_generation")
                      and (row.get("payload") or {}).get("external_dispatch_key") == key]
        observed = [row for row in events if row.get("event_type") == "dispatch.launch.lookup-recorded"
                    and row.get("operation_generation") == claim.get("operation_generation")
                    and (row.get("payload") or {}).get("external_dispatch_key") == key
                    and (row.get("payload") or {}).get("lookup_state") == "LAUNCHED"]
        next_callback = accepted_callbacks[index + 1]["callback_sequence"]
        if (len(authorized) != 1 or len(observed) != 1
                or not seq < next_claim < int(authorized[0]["sequence"])
                    < int(observed[0]["sequence"]) < next_callback
                or not observed[0]["payload"].get("receipt_id")):
            raise V03DogfoodPostRunFinalizerError("next dispatch lacks exact protected WAITING_EXTERNAL launch boundary")

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

    categories: dict[str, set[str]] = {}
    if scenario == "happy_path":
        payloads = [row["worker_payload"] for row in accepted_callbacks]
        if payloads[0].get("status") != "COMPLETED":
            raise V03DogfoodPostRunFinalizerError("Developer milestone lacks exact COMPLETED result")
        if payloads[1].get("verdict") != "PASS":
            raise V03DogfoodPostRunFinalizerError("independent review milestone lacks exact PASS verdict")
        if payloads[2].get("verdict") != "PASS":
            raise V03DogfoodPostRunFinalizerError("QA milestone lacks exact PASS verdict")
        categories = {
            "operation-started": {"operation", "persisted_state"},
            "developer-completed": {"candidate", "runtime_receipt", "persisted_state"},
            "independent-review-passed": {"candidate", "independent_review", "persisted_state"},
            "qa-passed-and-done": {"verification", "notification", "persisted_state"},
        }
    elif scenario == "review_remediation":
        payloads = [row["worker_payload"] for row in accepted_callbacks]
        if (
            payloads[0].get("status") != "COMPLETED"
            or payloads[1].get("verdict") != "REWORK"
            or payloads[2].get("status") != "COMPLETED"
            or payloads[3].get("verdict") != "PASS"
            or payloads[4].get("verdict") != "PASS"
        ):
            raise V03DogfoodPostRunFinalizerError("review/remediation milestone verdict sequence differs from durable callbacks")
        categories = {
            "developer-completed": {"candidate", "runtime_receipt", "persisted_state"},
            "reviewer-requested-changes": {"independent_review", "decision", "persisted_state"},
            "remediation-completed": {"remediation", "candidate", "runtime_receipt", "persisted_state"},
            "independent-re-review-passed": {"candidate", "independent_review", "persisted_state"},
            "qa-passed": {"verification", "notification", "persisted_state"},
        }
    else:
        original_responses = tuple(str(value) for value in (observation.get("response_ids") or []))
        original_calls = tuple(str(value) for value in (observation.get("function_call_ids") or []))
        if not original_responses or not original_calls:
            raise V03DogfoodPostRunFinalizerError("original session lacks durable client trace identity")
        categories = {
            "durable-state-created": {"operation", "persisted_state"},
            "original-session-ended": {"persisted_state"},
            "new-session-discovered-operation-and-user-items": {
                "operation", "decision", "notification", "persisted_state", "session_recovery"
            },
        }

    required_categories = {name: set(required) for name, _state, required in profile["milestones"]}
    if categories != required_categories:
        raise V03DogfoodPostRunFinalizerError("fact-derived milestone evidence categories differ from frozen requirements")
    return categories, assertions


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


def _archived_producer_source(preflight, run_id):
    if run_id != POST_HANDOFF_RUN:
        return preflight.execution.installation_commit_sha
    snapshot = preflight.composition.runtime.backend.read_snapshot()
    attestation, _, _ = validate_post_handoff_reconciliation(snapshot,
        consumer_binding=recovery_execution_binding(preflight.composition.policy_authority))
    return attestation["producer_execution_binding"]["execution_source_head_sha"]


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
    receipt = _durable_receipt(preflight, events, observation)
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
        runtime_receipt_resolver=lambda record: _durable_receipt(preflight, events, observation),
        runtime_binding_resolver=lambda record: _durable_run_bindings(preflight, observation, events),
        milestone_resolver=lambda record: categories,
        archived_producer_source_resolver=lambda run_id: _archived_producer_source(preflight, run_id),
    )
    human_interventions = 0
    if observation.get("operation_id") == RECOVERY_OPERATION_ID:
        route = recovery_route(preflight.composition.runtime.backend.read_snapshot())
        if route["ordinal"] == 1:
            accounting = route["authorization"]["observed_accounting"]
            human_interventions = accounting["human_interventions"]
            # This count covers the explicit observed ledger, not all historical
            # chat. The evidence URI carries that limitation. Runtime measured
            # repeated_continue_messages remains unchanged and separately gated.
            evidence_uris.extend([
                route["authorization"]["observed_accounting_uri"],
                "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6076638838",
                "https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37897902667",
                "https://github.com/DREAM-XIN/ai-sdlc/pull/574",
            ])
    if post_handoff_present(preflight.composition.runtime.backend.read_snapshot()):
        evidence_uris.append(POST_HANDOFF_ADMISSION["uri"])
    from v03_dogfood_full_composition import reviewer_replacement_present, REVIEWER_REPLACEMENT_ADMISSION, REVIEWER_FAILED_RUN
    if reviewer_replacement_present(preflight.composition.runtime.backend.read_snapshot()):
        route = _reviewer_route(preflight)
        from v03_dogfood_runtime_driver import _observe_reviewer_pre_model_failure, _observe_reviewer_post_model_failure
        from v03_dogfood_full_composition import REVIEWER_AUTH_PATH, REVIEWER_POST_MODEL_FAILED_RUN
        if route["ordinal"] == 3:
            from v03_dogfood_runtime_driver import _observe_reviewer_structured_predecessor
            from v03_dogfood_full_composition import REVIEWER_POST_MODEL_AUTH_PATH, REVIEWER_STRUCTURED_PRIOR_RUN
            historical = preflight.composition.runtime.backend.read_snapshot().get(REVIEWER_POST_MODEL_AUTH_PATH)
            if (_observe_reviewer_structured_predecessor(preflight) != route["authorization"]["predecessor_proof"]
                    or _observe_reviewer_post_model_failure(preflight) != historical["post_model_failure_proof"]):
                raise V03DogfoodPostRunFinalizerError("corrected Reviewer historical observations changed")
            evidence_uris.extend([route["authorization"]["admission"]["uri"],
                _run_uri(repository, REVIEWER_STRUCTURED_PRIOR_RUN),
                f"https://github.com/{repository}/pull/552#issuecomment-6092979158",
                _run_uri(repository, REVIEWER_POST_MODEL_FAILED_RUN), f"https://github.com/{repository}/issues/580"])
        elif route["ordinal"] == 2:
            historical=preflight.composition.runtime.backend.read_snapshot().get(REVIEWER_AUTH_PATH)
            if (_observe_reviewer_post_model_failure(preflight)!=route["authorization"]["post_model_failure_proof"]
                    or _observe_reviewer_pre_model_failure(preflight)!=historical["pre_model_failure_proof"]):
                raise V03DogfoodPostRunFinalizerError("Reviewer failed predecessor observations changed")
            evidence_uris.extend([route["authorization"]["admission"]["uri"],
                _run_uri(repository,REVIEWER_POST_MODEL_FAILED_RUN),
                f"https://github.com/{repository}/issues/580"])
        elif _observe_reviewer_pre_model_failure(preflight) != route["authorization"]["pre_model_failure_proof"]:
            raise V03DogfoodPostRunFinalizerError("Reviewer failed predecessor observation changed")
        evidence_uris.extend([REVIEWER_REPLACEMENT_ADMISSION["uri"], _run_uri(repository, REVIEWER_FAILED_RUN)])
    if scenario == "review_remediation":
        authority = getattr(preflight.composition.bundle.executor, "remediation_rereview_authority", None)
        if authority is None:
            raise V03DogfoodPostRunFinalizerError("remediation lacks its scoped rereview authority")
        binding = authority.validate_historical(operation_id=observation["operation_id"])
        from v03_dogfood_full_composition import dogfood_rereview_paths
        paths = dogfood_rereview_paths(observation["operation_id"])
        current_snapshot = preflight.composition.runtime.backend.read_snapshot()
        if paths[1] in current_snapshot.files:
            raise V03DogfoodPostRunFinalizerError("nonpassing rereview cannot finalize")
        evidence_uris.extend([binding["admission"]["uri"],
            f"https://github.com/{repository}/blob/{current_snapshot.ref_sha}/{paths[0]}"])
    trusted_facts = {
        "release_run_id": str(finalizer_run_id),
        "operation_generation": generation,
        "human_interventions": human_interventions,
        "milestones": _milestone_facts(
            scenario, repository, source_run_id, finalizer_run_id, worker_run_ids, categories
        ),
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
