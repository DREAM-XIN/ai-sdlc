#!/usr/bin/env python3
"""Bounded production runner for the three frozen v0.3 real dogfood scenarios.

The client-facing start crosses the reviewed OpenAI Responses adapter/host only.
After a durable external stop, server-side recovery consumes the exact production
gh-aw collector on the same protected Operator Store runtime. This runner emits
raw observations only; it is deliberately not a release-evidence authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time

from operator_vertical_gh_aw_github_source import _current_launch_binding
from typing import Any

from operator_store_model import operation_events, digest_json
from operator_vertical_store import vertical_projection
from v03_dogfood_full_composition import (recovery_route, validate_recovery_execution_seal, recovery_execution_binding,
    post_handoff_present, validate_post_handoff_reconciliation)
from v03_dogfood_fixture_pool import DogfoodSlot, task_text
from v03_dogfood_openai_host import V03DogfoodOpenAIResponsesHost, V03DogfoodResponsesTrace

SCENARIO_ROLE_SEQUENCES = {
    "happy_path": ("developer", "reviewer", "qa"),
    "review_remediation": ("developer", "reviewer", "developer", "reviewer", "qa"),
    "session_recovery": ("developer",),
}
STEP_ROLE = {
    "IMPLEMENTATION_WORK": "developer",
    "CODE_REVIEW": "reviewer",
    "CODE_REMEDIATION": "developer",
    "CODE_REREVIEW": "reviewer",
    "VERIFICATION_QA": "qa",
}
TERMINAL = {"DONE", "BLOCKED", "CANCELLED", "NEEDS_USER"}
RECOVERY_OPERATION_ID = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
RECOVERY_EXTERNAL_KEY = "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1"
RECOVERY_SCHEMA = "ai-sdlc.v03-dogfood-bounded-recovery/v1"
RECOVERY_RECEIPT_PATH = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-bounded-recovery/sealed-receipt.json"


class V03DogfoodScenarioRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DogfoodScenarioObservation:
    scenario: str
    operation_id: str
    start_status: str
    final_status: str
    dispatch_roles: tuple[str, ...]
    workflow_run_ids: tuple[int, ...]
    runtime_receipt_identity: str
    response_ids: tuple[str, ...]
    function_call_ids: tuple[str, ...]
    recovery_response_ids: tuple[str, ...] = ()
    recovery_function_call_ids: tuple[str, ...] = ()
    recovery_discovery_decision_ids: tuple[str, ...] = ()
    recovery_discovery_notification_ids: tuple[str, ...] = ()
    new_session_discovery_observed: bool = False
    repeated_continue_messages: int = 0
    release_eligible: bool = False
    worker_results_consumed: int = 0


def _decode_output(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("type") != "function_call_output":
        raise V03DogfoodScenarioRunnerError("Responses trace contains non-function output")
    try:
        payload = json.loads(str(item.get("output") or ""))
    except Exception as exc:
        raise V03DogfoodScenarioRunnerError("Responses function output is not JSON") from exc
    if not isinstance(payload, dict):
        raise V03DogfoodScenarioRunnerError("Responses function output is not an object")
    return payload


def _operation_start(trace: V03DogfoodResponsesTrace) -> tuple[str, str]:
    if len(trace.function_call_names) != len(trace.function_outputs):
        raise V03DogfoodScenarioRunnerError("Responses trace lost function tool/output correlation")
    starts: list[tuple[str, str]] = []
    for name, item in zip(trace.function_call_names, trace.function_outputs):
        if name != "aisdlc_v1_operation_start":
            continue
        payload = _decode_output(item)
        result = payload.get("result") if payload.get("ok") is True else None
        if not isinstance(result, dict):
            continue
        operation_id = str(result.get("operation_id") or "")
        status = str(result.get("status") or "")
        if operation_id and status:
            starts.append((operation_id, status))
    if len(starts) != 1:
        raise V03DogfoodScenarioRunnerError("dogfood client session must create exactly one Operation")
    return starts[0]


def _events(preflight: Any, operation_id: str) -> list[dict[str, Any]]:
    return operation_events(preflight.composition.runtime.backend.read_snapshot(), operation_id)


def _projection(preflight: Any, operation_id: str) -> dict[str, Any]:
    return vertical_projection(preflight.composition.runtime.backend.read_snapshot(), operation_id)


def _dispatch_rows(preflight: Any, operation_id: str) -> list[dict[str, Any]]:
    """Return one logical dispatch claim per external effect.

    dispatch.claimed intentionally contains no role. The Vertical executor first
    records loop.step.selected, then creates the semantic reservation/claim for
    that exact action. Reconstructing role from that durable sequence avoids
    trusting a field that does not exist in the closed dispatch-claim schema.

    A generation takeover may replay the same semantic effect under the same
    external_dispatch_key. That is one logical external dispatch, not concurrent
    work. Collapse only that exact cross-generation replay, keeping the newest
    claim. Any role/semantic drift, same-generation duplicate, or generation
    regression still fails closed.
    """
    rows = _events(preflight, operation_id)
    selected: tuple[int, str] | None = None
    claims: list[dict[str, Any]] = []
    claim_index_by_external_key: dict[str, int] = {}
    for row in rows:
        event_type = row.get("event_type")
        sequence = int(row.get("sequence", -1))
        if event_type == "loop.step.selected":
            step = str((row.get("payload") or {}).get("step") or "")
            role = STEP_ROLE.get(step)
            selected = (sequence, role) if role else None
            continue
        if event_type != "dispatch.claimed":
            continue
        if selected is None or selected[0] >= sequence:
            raise V03DogfoodScenarioRunnerError("dispatch claim lacks preceding trusted role-bearing selected step")
        enriched = dict(row)
        enriched["_dogfood_role"] = selected[1]
        payload = row.get("payload") or {}
        external_key = str(payload.get("external_dispatch_key") or "")
        existing_index = claim_index_by_external_key.get(external_key) if external_key else None
        if existing_index is None:
            if external_key:
                claim_index_by_external_key[external_key] = len(claims)
            claims.append(enriched)
        else:
            existing = claims[existing_index]
            existing_payload = existing.get("payload") or {}
            semantic_key = str(payload.get("semantic_effect_key") or "")
            existing_semantic_key = str(existing_payload.get("semantic_effect_key") or "")
            if (
                not semantic_key
                or not existing_semantic_key
                or existing_semantic_key != semantic_key
                or _dispatch_role(existing) != selected[1]
            ):
                raise V03DogfoodScenarioRunnerError(
                    "cross-generation dispatch replay changed logical effect identity"
                )
            try:
                previous_generation = int(existing.get("operation_generation", -1))
                generation = int(row.get("operation_generation", -1))
            except (TypeError, ValueError) as exc:
                raise V03DogfoodScenarioRunnerError(
                    "dispatch replay lacks valid generation identity"
                ) from exc
            if previous_generation < 0 or generation <= previous_generation:
                raise V03DogfoodScenarioRunnerError(
                    "repeated dispatch claim did not advance operation generation"
                )
            claims[existing_index] = enriched
        selected = None
    return claims


def _dispatch_role(row: dict[str, Any]) -> str:
    role = str(row.get("_dogfood_role") or "").lower()
    if role not in {"developer", "reviewer", "qa"}:
        raise V03DogfoodScenarioRunnerError("durable dispatch sequence lacks frozen role identity")
    return role


def _external_key(row: dict[str, Any]) -> str:
    value = str((row.get("payload") or {}).get("external_dispatch_key") or "")
    if not value:
        raise V03DogfoodScenarioRunnerError("durable dispatch claim lacks external dispatch key")
    return value


def _sealed_recovery_receipt(preflight, snapshot):
    route = recovery_route(snapshot)
    sealed = snapshot.get(route["receipt_path"])
    validate_recovery_execution_seal(snapshot, sealed, execution_binding=recovery_execution_binding(
        preflight.composition.policy_authority))
    return sealed


def _launch_receipts(preflight: Any, operation_id: str) -> tuple[tuple[int, ...], str]:
    rows = [row for row in _events(preflight, operation_id) if row.get("event_type") == "dispatch.launch.lookup-recorded"]
    run_ids: list[int] = []
    receipts: list[str] = []
    for row in rows:
        payload = row.get("payload") or {}
        if payload.get("lookup_state") != "LAUNCHED":
            continue
        receipt = str(payload.get("receipt_id") or "")
        external_key = str(payload.get("external_dispatch_key") or "")
        if operation_id == RECOVERY_OPERATION_ID and external_key == RECOVERY_EXTERNAL_KEY:
            if receipt != "37204777409":
                raise V03DogfoodScenarioRunnerError("historical launch receipt identity drifted")
            continue
        if not receipt.isdigit() or int(receipt) < 1:
            raise V03DogfoodScenarioRunnerError("LAUNCHED dispatch lacks exact Actions receipt")
        run_ids.append(int(receipt))
        receipts.append(receipt)
    if operation_id == RECOVERY_OPERATION_ID:
        sealed = _sealed_recovery_receipt(preflight, preflight.composition.runtime.backend.read_snapshot())
        if (
            not isinstance(sealed, dict)
            or sealed.get("schema_version") != RECOVERY_SCHEMA
            or sealed.get("external_dispatch_key") != RECOVERY_EXTERNAL_KEY
            or not str(sealed.get("receipt_id") or "").isdigit()
        ):
            raise V03DogfoodScenarioRunnerError("historical recovery lacks sealed canonical receipt")
        receipt = str(sealed["receipt_id"])
        run_ids.insert(0, int(receipt))
        receipts.insert(0, receipt)
    if not run_ids:
        raise V03DogfoodScenarioRunnerError("real dogfood produced no trusted Actions run receipt")
    if len(run_ids) != len(set(run_ids)):
        raise V03DogfoodScenarioRunnerError("real dogfood repeated one Actions run as multiple launches")
    return tuple(run_ids), receipts[-1]


def wait_for_worker_run(*, read_run, receipt, workflow, installation_sha, external_dispatch_key,
                        max_attempts=180, poll_seconds=5.0, sleeper=time.sleep):
    if not str(receipt).isdigit() or int(receipt) < 1 or not 1 <= max_attempts <= 240:
        raise V03DogfoodScenarioRunnerError("invalid bounded Worker receipt/wait")
    for attempt in range(max_attempts):
        run = read_run(int(receipt))
        if not isinstance(run, dict) or (
            run.get("id"), run.get("event"), run.get("head_branch"), run.get("head_sha"),
            str(run.get("path") or "").removeprefix(".github/workflows/"), run.get("display_title")
        ) != (int(receipt), "workflow_dispatch", "main", installation_sha, workflow,
              "AI-SDLC gh-aw " + external_dispatch_key) or run.get("run_attempt") != 1:
            raise V03DogfoodScenarioRunnerError("Worker identity drifted during read-only wait")
        if run.get("status") == "completed":
            if run.get("conclusion") != "success":
                raise V03DogfoodScenarioRunnerError("exact Worker completed unsuccessfully")
            return run
        if run.get("status") not in {"queued", "in_progress", "waiting", "requested", "pending"}:
            raise V03DogfoodScenarioRunnerError("Worker entered unsupported pending state")
        if attempt + 1 < max_attempts:
            sleeper(poll_seconds)
    raise V03DogfoodScenarioRunnerError("exact Worker completion wait exhausted")


def _wait_current_dispatch(preflight, operation_id, external_dispatch_key):
    snapshot = preflight.composition.runtime.backend.read_snapshot()
    _projection, launch, receipt = _current_launch_binding(
        snapshot, operation_id=operation_id, external_dispatch_key=external_dispatch_key
    )
    source = preflight.composition.result_source
    workflow = preflight.workflows.workflow_for(str(launch["role"]))
    lookup_key = external_dispatch_key
    if operation_id == RECOVERY_OPERATION_ID and external_dispatch_key == RECOVERY_EXTERNAL_KEY:
        sealed = _sealed_recovery_receipt(preflight, snapshot)
        if (
            not isinstance(sealed, dict)
            or sealed.get("schema_version") != RECOVERY_SCHEMA
            or sealed.get("external_dispatch_key") != external_dispatch_key
            or not str(sealed.get("receipt_id") or "").isdigit()
        ):
            raise V03DogfoodScenarioRunnerError("recovery wait lacks sealed exact receipt")
        source = preflight.composition.recovery_result_source
        workflow = str(sealed.get("workflow_file") or "")
        lookup_key = str(sealed.get("recovery_dispatch_key") or "")
        receipt = str(sealed["receipt_id"])
    return wait_for_worker_run(
        read_run=lambda run_id: source._json(source.config.control_repository,
                                            f"/actions/runs/{run_id}", source.config.control_token),
        receipt=receipt, workflow=workflow,
        installation_sha=preflight.execution.installation_commit_sha,
        external_dispatch_key=lookup_key,
    )


def _collect_next(preflight: Any, operation_id: str, consumed: int) -> int:
    claims = _dispatch_rows(preflight, operation_id)
    if len(claims) <= consumed:
        raise V03DogfoodScenarioRunnerError("WAITING_EXTERNAL has no fresh durable dispatch claim")
    if len(claims) != consumed + 1:
        raise V03DogfoodScenarioRunnerError("multiple unconsumed dispatch claims appeared concurrently")
    external_key = _external_key(claims[-1])
    _wait_current_dispatch(preflight, operation_id, external_key)
    collector = preflight.composition.collector
    if operation_id == RECOVERY_OPERATION_ID and external_key == RECOVERY_EXTERNAL_KEY:
        collector = preflight.composition.recovery_collector
    collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
    return consumed + 1


def scenario_instruction(slot: DogfoodSlot, *, expected_revision: int) -> str:
    if expected_revision < 0:
        raise ValueError("dogfood expected revision must be non-negative")
    return (
        task_text(slot)
        + "\n\nTrusted release-controller instruction:\n"
        + "Use the exposed AI-SDLC function tools. Start exactly one Operation for "
        + f"feature `{slot.feature_id}` at expected revision {expected_revision} in ASSISTED mode. "
        + "Your first tool response must contain exactly one function call: operation.start. "
        + "Do not call system.capabilities, feature.status, operator.inbox, operation.status, "
        + "decision.list, or notification.list before operation.start, and do not place any other "
        + "tool call beside operation.start in the same response. "
        + "Do not invent ids, do not retry operation.start, and do not claim lifecycle or Gate authority. "
        + "After the first durable external stop, return control to the trusted server runner."
    )


def recovery_instruction(slot: DogfoodSlot, *, operation_id: str) -> str:
    return (
        "This is a fresh client session with no prior Responses conversation context. "
        "Do not call operation.start, operation.cancel, decision.respond, or notification.ack. "
        "Use the read-only AI-SDLC operator.inbox tool to rediscover durable work for "
        + f"feature `{slot.feature_id}`. Confirm that Operation `{operation_id}` and its pending Decision "
        + "and Notification are all present. Do not mutate anything."
    )


def _verify_fresh_session_discovery(
    trace: V03DogfoodResponsesTrace, *, operation_id: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matches: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for item in trace.function_outputs:
        payload = _decode_output(item)
        result = payload.get("result") if payload.get("ok") is True else None
        if not isinstance(result, dict):
            continue
        operations = result.get("operations")
        decisions = result.get("decisions")
        notifications = result.get("notifications")
        if not isinstance(operations, list) or not isinstance(decisions, list) or not isinstance(notifications, list):
            continue
        same_operation = any(isinstance(row, dict) and str(row.get("operation_id") or "") == operation_id for row in operations)
        same_decision = any(isinstance(row, dict) and str(row.get("operation_id") or "") == operation_id and row.get("status") == "PENDING" for row in decisions)
        same_notification = any(isinstance(row, dict) and str(row.get("operation_id") or "") == operation_id for row in notifications)
        if same_operation and same_decision and same_notification:
            decision_ids = tuple(sorted(
                str(row.get("decision_id") or row.get("id") or "")
                for row in decisions
                if isinstance(row, dict)
                and str(row.get("operation_id") or "") == operation_id
                and row.get("status") == "PENDING"
                and str(row.get("decision_id") or row.get("id") or "")
            ))
            notification_ids = tuple(sorted(
                str(row.get("notification_id") or row.get("id") or "")
                for row in notifications
                if isinstance(row, dict)
                and str(row.get("operation_id") or "") == operation_id
                and str(row.get("notification_id") or row.get("id") or "")
            ))
            if decision_ids and notification_ids:
                matches.append((decision_ids, notification_ids))
    if len(matches) != 1:
        raise V03DogfoodScenarioRunnerError(
            "fresh session must contain exactly one inbox result for the same Operation plus pending Decision/Notification"
        )
    return matches[0]



def _resume_post_handoff(preflight, host):
    """Discover the existing fixed Operation, then use its original server backend."""
    runtime = preflight.composition.runtime
    snapshot = runtime.backend.read_snapshot()
    attestation, _, _ = validate_post_handoff_reconciliation(snapshot,
        consumer_binding=recovery_execution_binding(preflight.composition.policy_authority))
    rows = operation_events(snapshot, RECOVERY_OPERATION_ID)
    if any(row.get("event_type") == "worker.result.rejected"
           and (row.get("payload") or {}).get("callback_id") == attestation["observation_callback_id"]
           for row in rows[16:]):
        raise V03DogfoodScenarioRunnerError("single reconciled observation was rejected")
    original = _projection(preflight, RECOVERY_OPERATION_ID)
    instruction = (
        "Read the existing authenticated Operation " + RECOVERY_OPERATION_ID + ". "
        "Call operation.status exactly once and then return control. Do not call operation.start, "
        "do not request a new execution, and do not choose a lifecycle action."
    )
    trace = host.run(scenario_instruction=instruction)
    if (tuple(trace.function_call_names) != ("aisdlc_v1_operation_status",)
            or len(trace.function_outputs) != 1):
        raise V03DogfoodScenarioRunnerError("reconciliation host did not observe the exact existing Operation")
    payload = _decode_output(trace.function_outputs[0])
    result = payload.get("result") if payload.get("ok") is True else None
    if result != {key: original[key] for key in ("operation_id", "generation", "status")}:
        raise V03DogfoodScenarioRunnerError("reconciliation status observation differs from protected state")
    bundle = preflight.composition.bundle
    backend = bundle.vertical_bundle.api_backends["operation.resume"]
    if backend.runtime is not runtime or backend.executor is not bundle.executor:
        raise V03DogfoodScenarioRunnerError("reconciliation resume split the protected authority graph")
    pending_projection = _projection(preflight, RECOVERY_OPERATION_ID)
    requested = set(pending_projection.get("requested_persists", []))
    linearized = set(pending_projection.get("linearized_persists", []))
    confirmed = set(pending_projection.get("confirmed_persists", []))
    pending = linearized - confirmed
    if not pending.issubset(requested):
        raise V03DogfoodScenarioRunnerError("pending reconciliation Persist lacks original request")
    remaining = set(pending)
    for _ in range(len(pending)):
        before = _projection(preflight, RECOVERY_OPERATION_ID)
        if (set(before.get("requested_persists", [])) != requested
                or set(before.get("linearized_persists", [])) != linearized
                or set(before.get("confirmed_persists", [])) - confirmed != pending - remaining):
            raise V03DogfoodScenarioRunnerError("pending Persist identity changed before reconciliation")
        progressed = bundle.executor._reconcile_persist(RECOVERY_OPERATION_ID)
        after = _projection(preflight, RECOVERY_OPERATION_ID)
        now = linearized - set(after.get("confirmed_persists", []))
        if (progressed is not True or not now < remaining or len(remaining - now) != 1
                or set(after.get("requested_persists", [])) != requested
                or set(after.get("linearized_persists", [])) != linearized):
            raise V03DogfoodScenarioRunnerError("exact pending Persist remains stopped or changed identity")
        remaining = now
    if remaining:
        raise V03DogfoodScenarioRunnerError("exact pending Persist did not converge")
    feature, _ = bundle.executor.feature_gateway.read_feature(operation_id=RECOVERY_OPERATION_ID)
    resumed = backend.invoke({"context": {"operation_id": RECOVERY_OPERATION_ID,
        "expected_feature_revision": feature.revision}},
        preflight.composition.responses.registration.trusted_context)
    current = _projection(preflight, RECOVERY_OPERATION_ID)
    if (resumed.get("operation_id") != RECOVERY_OPERATION_ID or resumed.get("generation") != 1
            or resumed.get("status") != current["status"]):
        raise V03DogfoodScenarioRunnerError("reconciliation resume changed Operation identity")
    return trace, RECOVERY_OPERATION_ID, str(current["status"])



def _notify_completed(preflight, operation_id):
    rows = _events(preflight, operation_id)
    projection = _projection(preflight, operation_id)
    completed = [row for row in rows if row.get("event_type") == "operation.done"]
    if (projection.get("status") != "DONE" or len(completed) != 1
            or completed[0].get("operation_generation") != projection.get("generation")
            or not isinstance(completed[0].get("event_id"), str) or not completed[0]["event_id"]):
        raise V03DogfoodScenarioRunnerError("completion Notification lacks one exact durable DONE event")
    expected_id = "operation-done-" + digest_json({
        "feature_revision": projection["expected_feature_revision"], "operation_id": operation_id,
        "generation": projection["generation"], "event_type": "operation.done"})[:32]
    if (completed[0]["event_id"] != expected_id
            or (completed[0].get("payload") or {}).get("feature_revision") != projection["expected_feature_revision"]):
        raise V03DogfoodScenarioRunnerError("completion Notification DONE identity/revision differs")
    coordinator = preflight.composition.bundle.decision_notification_coordinator
    if coordinator.runtime is not preflight.composition.runtime:
        raise V03DogfoodScenarioRunnerError("completion Notification split protected runtime")
    return coordinator.notify_operation(operation_id=operation_id,
        notification_type="operation.completed", trigger_identity=completed[0]["event_id"],
        summary="The trusted dogfood Operation completed its canonical lifecycle.")


def run_scenario(
    *,
    preflight: Any,
    host: V03DogfoodOpenAIResponsesHost,
    recovery_host: V03DogfoodOpenAIResponsesHost | None = None,
) -> DogfoodScenarioObservation:
    scenario = preflight.slot.scenario
    expected_roles = SCENARIO_ROLE_SEQUENCES.get(scenario)
    if expected_roles is None:
        raise V03DogfoodScenarioRunnerError("scenario escaped frozen dogfood inventory")
    manifest = preflight.composition.feature_event_gateway.read_feature(
        feature_id=preflight.slot.feature_id,
    )
    snapshot = preflight.composition.runtime.backend.read_snapshot()
    reconciliation = None
    if scenario == "happy_path" and post_handoff_present(snapshot):
        if (preflight.slot.feature_id != "F-OPERATOR-V03-DOGFOOD-HAPPY-0001"
                or preflight.slot.target_ref != "dogfood/v0.3-happy-path-0001"):
            raise V03DogfoodScenarioRunnerError("fixed reconciliation escaped its happy-path slot")
        reconciliation, _, _ = validate_post_handoff_reconciliation(snapshot,
            consumer_binding=recovery_execution_binding(preflight.composition.policy_authority))
    expected_manifest_revision = (int(_projection(preflight, RECOVERY_OPERATION_ID)["expected_feature_revision"])
                                  if reconciliation is not None else 1)
    if not isinstance(manifest, dict) or (reconciliation is None and int(manifest.get("revision", -1)) != expected_manifest_revision):
        raise V03DogfoodScenarioRunnerError("dogfood fixture is not the exact active revision-1 slot")

    if reconciliation is not None:
        trace, operation_id, start_status = _resume_post_handoff(preflight, host)
        converged = preflight.composition.feature_event_gateway.read_feature(feature_id=preflight.slot.feature_id)
        if int(converged.get("revision", -1)) != int(_projection(preflight, operation_id)["expected_feature_revision"]):
            raise V03DogfoodScenarioRunnerError("reconciled canonical Persist and Feature revision have not converged")
    else:
        trace = host.run(scenario_instruction=scenario_instruction(preflight.slot, expected_revision=expected_manifest_revision))
        operation_id, start_status = _operation_start(trace)
    projection = _projection(preflight, operation_id)
    status = str(projection.get("status") or "")
    if status != start_status:
        raise V03DogfoodScenarioRunnerError("Responses start result differs from durable Operation projection")

    consumed = 0
    if reconciliation is not None:
        events = _events(preflight, operation_id)
        accepted = [e for e in events if e["event_type"] == "worker.result.validated"]
        confirmed = {e["payload"]["feature_event_id"] for e in events if e["event_type"] == "persist.confirmed"}
        translated = {e["payload"].get("callback_id"): e["payload"].get("feature_event_id")
                      for e in events if e["event_type"] == "feature.event.translated" and e["payload"].get("callback_id")}
        for event in accepted:
            if translated.get(event["payload"].get("callback_id")) not in confirmed:
                raise V03DogfoodScenarioRunnerError("accepted reconciled-prefix callback lacks canonical Persist")
        consumed = len(accepted)
        if consumed and accepted[0]["payload"].get("callback_id") != reconciliation["observation_callback_id"]:
            raise V03DogfoodScenarioRunnerError("scenario consumed an unbound replacement observation")
    recovery_trace: V03DogfoodResponsesTrace | None = None
    recovery_decision_ids: tuple[str, ...] = ()
    recovery_notification_ids: tuple[str, ...] = ()
    if scenario == "session_recovery":
        if status != "WAITING_EXTERNAL":
            raise V03DogfoodScenarioRunnerError("session recovery must first stop durably at WAITING_EXTERNAL")
        if recovery_host is None or recovery_host is host:
            raise V03DogfoodScenarioRunnerError("session recovery requires a distinct fresh Responses host session")
        claims = _dispatch_rows(preflight, operation_id)
        if len(claims) != 1:
            raise V03DogfoodScenarioRunnerError("session recovery must retain one pending external dispatch")
        preflight.composition.bundle.decision_notification_coordinator.request_decision(
            operation_id=operation_id, decision_type="NEEDS_AUTHORIZATION",
            request_key="v03-session-recovery:" + operation_id,
            requested_by="trusted-v03-release-dogfood-controller",
            summary="Await the owner choice for this exact durable session-recovery Operation.",
        )
        # This scenario demonstrates unfinished work, so observe the exact real
        # Worker completion without accepting a callback or progressing lifecycle.
        _wait_current_dispatch(preflight, operation_id, _external_key(claims[0]))
        status = str(_projection(preflight, operation_id).get("status") or "")
        if status != "NEEDS_USER":
            raise V03DogfoodScenarioRunnerError("session recovery must converge to NEEDS_USER after original session ends")
        starts_before = len([row for row in _events(preflight, operation_id) if row.get("event_type") == "operation.started"])
        recovery_trace = recovery_host.run(
            scenario_instruction=recovery_instruction(preflight.slot, operation_id=operation_id)
        )
        starts_after = len([row for row in _events(preflight, operation_id) if row.get("event_type") == "operation.started"])
        if starts_before != 1 or starts_after != 1:
            raise V03DogfoodScenarioRunnerError("fresh session replayed or altered operation.start authority")
        recovery_decision_ids, recovery_notification_ids = _verify_fresh_session_discovery(
            recovery_trace, operation_id=operation_id
        )
    else:
        for _ in range(8):
            status = str(_projection(preflight, operation_id).get("status") or "")
            if status in TERMINAL:
                break
            if status != "WAITING_EXTERNAL":
                raise V03DogfoodScenarioRunnerError(f"dogfood runner encountered unsupported durable state: {status}")
            consumed = _collect_next(preflight, operation_id, consumed)
        status = str(_projection(preflight, operation_id).get("status") or "")
        if status != "DONE":
            raise V03DogfoodScenarioRunnerError(f"{scenario} did not finish DONE")
        _notify_completed(preflight, operation_id)

    claims = _dispatch_rows(preflight, operation_id)
    roles = tuple(_dispatch_role(row) for row in claims)
    if roles != expected_roles:
        raise V03DogfoodScenarioRunnerError(
            f"{scenario} dispatch role sequence drifted: expected {expected_roles}, got {roles}"
        )
    if scenario != "session_recovery" and consumed != len(claims):
        raise V03DogfoodScenarioRunnerError("not every durable dispatch was consumed exactly once")
    run_ids, receipt = _launch_receipts(preflight, operation_id)
    if len(run_ids) != len(expected_roles):
        raise V03DogfoodScenarioRunnerError("real Worker run count differs from frozen scenario role sequence")

    return DogfoodScenarioObservation(
        scenario=scenario,
        operation_id=operation_id,
        worker_results_consumed=consumed,
        start_status=start_status,
        final_status=status,
        dispatch_roles=roles,
        workflow_run_ids=run_ids,
        runtime_receipt_identity=receipt,
        response_ids=trace.response_ids,
        function_call_ids=trace.function_call_ids,
        recovery_response_ids=recovery_trace.response_ids if recovery_trace else (),
        recovery_function_call_ids=recovery_trace.function_call_ids if recovery_trace else (),
        recovery_discovery_decision_ids=recovery_decision_ids,
        recovery_discovery_notification_ids=recovery_notification_ids,
        new_session_discovery_observed=recovery_trace is not None,
    )
