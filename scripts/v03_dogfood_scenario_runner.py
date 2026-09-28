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
from typing import Any

from operator_store_model import operation_events
from operator_vertical_store import vertical_projection
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
    new_session_discovery_observed: bool = False
    repeated_continue_messages: int = 0
    release_eligible: bool = False


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
    starts: list[tuple[str, str]] = []
    for item in trace.function_outputs:
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
    """Attach role from the immediately preceding trusted selected-step fact.

    dispatch.claimed intentionally contains no role. The Vertical executor first
    records loop.step.selected, then creates the semantic reservation/claim for
    that exact action. Reconstructing role from that durable sequence avoids
    trusting a field that does not exist in the closed dispatch-claim schema.
    """
    rows = _events(preflight, operation_id)
    selected: tuple[int, str] | None = None
    claims: list[dict[str, Any]] = []
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
        claims.append(enriched)
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


def _launch_receipts(preflight: Any, operation_id: str) -> tuple[tuple[int, ...], str]:
    rows = [row for row in _events(preflight, operation_id) if row.get("event_type") == "dispatch.launch.lookup-recorded"]
    run_ids: list[int] = []
    receipts: list[str] = []
    for row in rows:
        payload = row.get("payload") or {}
        if payload.get("lookup_state") != "LAUNCHED":
            continue
        receipt = str(payload.get("receipt_id") or "")
        if not receipt.isdigit() or int(receipt) < 1:
            raise V03DogfoodScenarioRunnerError("LAUNCHED dispatch lacks exact Actions receipt")
        run_ids.append(int(receipt))
        receipts.append(receipt)
    if not run_ids:
        raise V03DogfoodScenarioRunnerError("real dogfood produced no trusted Actions run receipt")
    if len(run_ids) != len(set(run_ids)):
        raise V03DogfoodScenarioRunnerError("real dogfood repeated one Actions run as multiple launches")
    return tuple(run_ids), receipts[-1]


def _collect_next(preflight: Any, operation_id: str, consumed: int) -> int:
    claims = _dispatch_rows(preflight, operation_id)
    if len(claims) <= consumed:
        raise V03DogfoodScenarioRunnerError("WAITING_EXTERNAL has no fresh durable dispatch claim")
    if len(claims) != consumed + 1:
        raise V03DogfoodScenarioRunnerError("multiple unconsumed dispatch claims appeared concurrently")
    preflight.composition.collector.handle(
        operation_id=operation_id,
        external_dispatch_key=_external_key(claims[-1]),
    )
    return consumed + 1


def scenario_instruction(slot: DogfoodSlot, *, expected_revision: int) -> str:
    if expected_revision < 0:
        raise ValueError("dogfood expected revision must be non-negative")
    return (
        task_text(slot)
        + "\n\nTrusted release-controller instruction:\n"
        + "Use the exposed AI-SDLC function tools. Start exactly one Operation for "
        + f"feature `{slot.feature_id}` at expected revision {expected_revision} in ASSISTED mode. "
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


def _verify_fresh_session_discovery(trace: V03DogfoodResponsesTrace, *, operation_id: str) -> None:
    matched = False
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
            matched = True
    if not matched:
        raise V03DogfoodScenarioRunnerError("fresh session did not rediscover same Operation plus pending Decision/Notification")


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
        repository=preflight.execution.repository,
        feature_id=preflight.slot.feature_id,
        target_ref=preflight.slot.target_ref,
    )
    if not isinstance(manifest, dict) or int(manifest.get("revision", -1)) != 1:
        raise V03DogfoodScenarioRunnerError("dogfood fixture is not the exact active revision-1 slot")

    trace = host.run(scenario_instruction=scenario_instruction(preflight.slot, expected_revision=1))
    operation_id, start_status = _operation_start(trace)
    projection = _projection(preflight, operation_id)
    status = str(projection.get("status") or "")
    if status != start_status:
        raise V03DogfoodScenarioRunnerError("Responses start result differs from durable Operation projection")

    consumed = 0
    recovery_trace: V03DogfoodResponsesTrace | None = None
    if scenario == "session_recovery":
        if status != "WAITING_EXTERNAL":
            raise V03DogfoodScenarioRunnerError("session recovery must first stop durably at WAITING_EXTERNAL")
        if recovery_host is None or recovery_host is host:
            raise V03DogfoodScenarioRunnerError("session recovery requires a distinct fresh Responses host session")
        consumed = _collect_next(preflight, operation_id, consumed)
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
        _verify_fresh_session_discovery(recovery_trace, operation_id=operation_id)
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

    claims = _dispatch_rows(preflight, operation_id)
    roles = tuple(_dispatch_role(row) for row in claims)
    if roles != expected_roles:
        raise V03DogfoodScenarioRunnerError(
            f"{scenario} dispatch role sequence drifted: expected {expected_roles}, got {roles}"
        )
    if consumed != len(claims):
        raise V03DogfoodScenarioRunnerError("not every durable dispatch was consumed exactly once")
    run_ids, receipt = _launch_receipts(preflight, operation_id)
    if len(run_ids) != len(expected_roles):
        raise V03DogfoodScenarioRunnerError("real Worker run count differs from frozen scenario role sequence")

    return DogfoodScenarioObservation(
        scenario=scenario,
        operation_id=operation_id,
        start_status=start_status,
        final_status=status,
        dispatch_roles=roles,
        workflow_run_ids=run_ids,
        runtime_receipt_identity=receipt,
        response_ids=trace.response_ids,
        function_call_ids=trace.function_call_ids,
        recovery_response_ids=recovery_trace.response_ids if recovery_trace else (),
        recovery_function_call_ids=recovery_trace.function_call_ids if recovery_trace else (),
        new_session_discovery_observed=recovery_trace is not None,
    )
