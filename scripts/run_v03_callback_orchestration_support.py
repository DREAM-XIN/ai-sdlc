#!/usr/bin/env python3
"""Run callback orchestration at the highest support level present in this checkout.

Duplicate and superseded-generation callbacks are fully supported by the current
accepted coordinator. Stale-candidate durable convergence additionally requires
Issue #254 / PR #255. Until that production remediation is present in the
checkout, this runner proves the known fail-closed gap and reports the scenario
as PENDING rather than manufacturing the production fix in the verification PR.
"""
from __future__ import annotations

import json
from pathlib import Path

from operator_store_model import operation_events, rebuild_projection
from operator_vertical import VerticalInvariantError
from v03_real_runtime_prerequisites import _stale_callback_reconciliation_ready
from validate_v03_callback_orchestration import (
    CANDIDATE_B,
    _blocked_payload,
    _events,
    _setup_waiting_external,
    scenario_duplicate_callback,
    scenario_out_of_order_callback,
    scenario_stale_candidate_result,
)

ROOT = Path(__file__).resolve().parents[1]
CALLBACK_SOURCE = ROOT / "scripts" / "operator_vertical_callback.py"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _runtime_has_stale_convergence() -> bool:
    return _stale_callback_reconciliation_ready(CALLBACK_SOURCE.read_text(encoding="utf-8"))


def _pending_stale_candidate_dependency() -> dict:
    binding, backend, _clock, feature_gateway, _executor, coordinator, context = _setup_waiting_external(
        idempotency_key="fi-stale-candidate-result-pending-255",
        receipt_id="run-stale-candidate-pending-255",
    )
    feature_gateway.candidate = CANDIDATE_B
    callback_id = "callback-stale-candidate-pending-255"
    try:
        coordinator.handle(
            context=context,
            callback_id=callback_id,
            worker_payload=_blocked_payload("candidate A result after candidate B became current"),
            receipts=[],
        )
    except VerticalInvariantError as exc:
        require(exc.code == "STALE_REVISION", f"pre-#255 stale callback failed with unexpected code: {exc.code}")
    else:
        raise AssertionError("checkout lacks #255 convergence shape but stale callback unexpectedly converged")

    callbacks = [
        event
        for event in _events(backend, binding.operation_id, "worker.callback.recorded")
        if (event.get("payload") or {}).get("callback_id") == callback_id
    ]
    rejected = [
        event
        for event in _events(backend, binding.operation_id, "worker.result.rejected")
        if (event.get("payload") or {}).get("callback_id") == callback_id
    ]
    translated = _events(backend, binding.operation_id, "feature.event.translated")
    persists = [
        event
        for event in operation_events(backend.read_snapshot(), binding.operation_id)
        if event["event_type"].startswith("persist.")
    ]
    require(len(callbacks) == 1, "known stale-callback gap did not preserve one durable callback envelope")
    require(len(rejected) == 0, "checkout without #255 unexpectedly contains durable stale-callback rejection")
    require(len(translated) == 0 and len(persists) == 0, "known stale-callback gap gained translation/Persist authority")
    projection = rebuild_projection(backend.read_snapshot(), binding.operation_id)
    return {
        "scenario_id": "stale-candidate-result",
        "support_status": "PENDING_RUNTIME_REMEDIATION",
        "runtime_remediation_issue": 254,
        "runtime_remediation_pr": 255,
        "operation_id": binding.operation_id,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "callback_id": callback_id,
        "old_candidate_head_sha": context.candidate_head_sha,
        "current_candidate_head_sha": CANDIDATE_B,
        "durable_callback_count": 1,
        "durable_rejection_count": 0,
        "translation_count": 0,
        "persist_authority_count": 0,
        "final_status": projection["status"],
    }


def run_callback_support() -> dict:
    duplicate = scenario_duplicate_callback()
    out_of_order = scenario_out_of_order_callback()
    if _runtime_has_stale_convergence():
        stale = scenario_stale_candidate_result()
        stale["support_status"] = "PASS"
    else:
        stale = _pending_stale_candidate_dependency()
    return {
        "evidence_kind": "deterministic-callback-orchestration-support",
        "release_eligible": False,
        "runtime_has_stale_callback_convergence": _runtime_has_stale_convergence(),
        "scenarios": {
            "duplicate-callback": {**duplicate, "support_status": "PASS"},
            "out-of-order-callback": {**out_of_order, "support_status": "PASS"},
            "stale-candidate-result": stale,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run_callback_support(), indent=2, sort_keys=True))
