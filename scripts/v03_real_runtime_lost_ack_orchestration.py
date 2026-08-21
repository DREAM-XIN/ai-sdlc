#!/usr/bin/env python3
"""Two-process orchestration contract for the v0.3 lost-ACK takeover scenario.

The helpers in this module do not invent Store facts or external identities.
They derive the exact first dispatch from the accepted Vertical selector, invoke
an already-scoped production `operation.start`, and use the accepted trusted
`plan_vertical_takeover` + recovery executor for generation G+1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_store_model import (
    external_dispatch_key,
    operation_events,
    operation_id_for,
    rebuild_projection,
    semantic_effect_key,
    semantic_effect_material,
)
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE
from operator_vertical_controller import select_vertical_action
from operator_vertical_recovery import plan_vertical_takeover
from v03_real_runtime_fault_injection import (
    InjectedRunnerCrash,
    LostAckCrashAfterLaunchDispatchGateway,
)


class LostAckOrchestrationError(ValueError):
    pass


@dataclass(frozen=True)
class LostAckDispatchBinding:
    repository: str
    feature_id: str
    target_ref: str
    feature_revision: int
    current_stage: str
    candidate_pr_number: int
    candidate_head_sha: str
    role: str
    task_id: str
    task_identity: str
    semantic_effect_key: str
    external_dispatch_key: str
    operation_id: str
    idempotency_key: str


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LostAckOrchestrationError(message)


def derive_lost_ack_dispatch_binding(
    *,
    repository: str,
    feature_id: str,
    target_ref: str,
    manifest: dict[str, Any],
    candidate_pr_number: int,
    candidate_head_sha: str,
    idempotency_key: str,
    occurred_at: str,
) -> LostAckDispatchBinding:
    """Derive the exact first dispatch key through the accepted selector."""
    _require(bool(idempotency_key), "lost-ACK fixture requires an explicit idempotency key")
    feature = FeatureSnapshot.from_manifest(
        repository=repository,
        target_ref=target_ref,
        manifest=manifest,
        candidate_pr_number=candidate_pr_number,
        candidate_head_sha=candidate_head_sha,
    )
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=occurred_at)
    _require(action.kind == "dispatch", "lost-ACK fixture must be immediately dispatch-ready; stage-start Persist is unsupported")
    _require(bool(action.role and action.task_id and action.task_identity), "selected dispatch lacks exact role/task identity")
    _require(action.candidate_head_sha == candidate_head_sha, "selected dispatch candidate binding differs from trusted candidate head")

    material = semantic_effect_material(
        target_repository=repository,
        feature_id=feature_id,
        expected_revision=feature.revision,
        current_stage=feature.current_stage,
        task_identity=str(action.task_identity),
        role=str(action.role),
        candidate_head_sha=action.candidate_head_sha,
    )
    effect_key = semantic_effect_key(**material)
    dispatch_key = external_dispatch_key(effect_key)
    return LostAckDispatchBinding(
        repository=repository,
        feature_id=feature_id,
        target_ref=target_ref,
        feature_revision=feature.revision,
        current_stage=feature.current_stage,
        candidate_pr_number=candidate_pr_number,
        candidate_head_sha=candidate_head_sha,
        role=str(action.role),
        task_id=str(action.task_id),
        task_identity=str(action.task_identity),
        semantic_effect_key=effect_key,
        external_dispatch_key=dispatch_key,
        operation_id=operation_id_for(repository, feature_id, idempotency_key),
        idempotency_key=idempotency_key,
    )


def _matching_events(runtime, operation_id: str, event_type: str, generation: int, external_key: str) -> list[dict[str, Any]]:
    matches = []
    for event in operation_events(runtime.backend.read_snapshot(), operation_id):
        if event.get("event_type") != event_type or int(event.get("operation_generation", -1)) != generation:
            continue
        payload = event.get("payload") or {}
        if payload.get("external_dispatch_key") == external_key:
            matches.append(event)
    return matches


def _start_request(binding: LostAckDispatchBinding, *, adapter_id: str) -> dict[str, Any]:
    return {
        "idempotency_key": binding.idempotency_key,
        "client_identity": {"adapter_id": adapter_id},
        "target": {
            "repository": binding.repository,
            "feature_id": binding.feature_id,
        },
        "context": {"expected_feature_revision": binding.feature_revision},
    }


def run_phase1_start_and_crash(
    *,
    bundle: Any,
    binding: LostAckDispatchBinding,
    adapter_id: str,
) -> dict[str, Any]:
    """Create G0 normally and require the exact crash-after-LAUNCHED window."""
    start = bundle.backends.get("operation.start")
    _require(start is not None and callable(getattr(start, "invoke", None)), "production bundle lacks operation.start")
    dispatch_gateway = getattr(getattr(bundle.executor, "base", bundle.executor), "dispatch_gateway", None)
    _require(
        isinstance(dispatch_gateway, LostAckCrashAfterLaunchDispatchGateway),
        "phase 1 executor is not bound to the verification-only lost-ACK gateway",
    )
    _require(
        dispatch_gateway.expected_external_dispatch_key == binding.external_dispatch_key,
        "phase 1 fault gateway key differs from selector-derived external identity",
    )

    trusted_context = bundle.write_bundle.read_bundle.trusted_context_provider.for_request(
        {"repository": binding.repository, "feature_id": binding.feature_id}
    )
    try:
        start.invoke(_start_request(binding, adapter_id=adapter_id), trusted_context)
    except InjectedRunnerCrash as crash:
        _require(crash.external_dispatch_key == binding.external_dispatch_key, "crash escaped for a different external dispatch key")
    else:
        raise LostAckOrchestrationError("phase 1 did not terminate in the exact crash-after-launch window")

    projection = rebuild_projection(bundle.runtime.backend.read_snapshot(), binding.operation_id)
    _require(projection.get("generation") == 0, "phase 1 Operation is not generation 0")
    _require(projection.get("operation_profile") == VERTICAL_PROFILE, "phase 1 Operation lost vertical profile")
    authorized = _matching_events(
        bundle.runtime,
        binding.operation_id,
        "dispatch.launch.authorized",
        0,
        binding.external_dispatch_key,
    )
    looked_up = _matching_events(
        bundle.runtime,
        binding.operation_id,
        "dispatch.launch.lookup-recorded",
        0,
        binding.external_dispatch_key,
    )
    _require(len(authorized) == 1, "phase 1 must durably contain exactly one G0 launch authorization")
    _require(not looked_up, "phase 1 crash window already contains local launch lookup evidence")
    return {
        "operation_id": binding.operation_id,
        "generation": 0,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "launch_authorized_event_id": authorized[0]["event_id"],
        "local_launch_lookup_recorded": False,
    }


def run_phase2_takeover_and_adopt(
    *,
    bundle: Any,
    binding: LostAckDispatchBinding,
) -> dict[str, Any]:
    """Take over into G+1 and re-enter accepted executor for same-key adoption."""
    dispatch_gateway = getattr(getattr(bundle.executor, "base", bundle.executor), "dispatch_gateway", None)
    _require(dispatch_gateway is not None, "phase 2 executor lacks trusted dispatch gateway")
    _require(
        not isinstance(dispatch_gateway, LostAckCrashAfterLaunchDispatchGateway),
        "phase 2 must use a fresh normal trusted dispatch gateway, not the crash injector",
    )

    before = rebuild_projection(bundle.runtime.backend.read_snapshot(), binding.operation_id)
    _require(before.get("generation") == 0, "phase 2 expected durable G0 before takeover")
    _require(before.get("status") not in {"DONE", "CANCELLED", "NEEDS_USER"}, "phase 2 cannot take over terminal/user-blocked Operation")
    _require(
        len(_matching_events(bundle.runtime, binding.operation_id, "dispatch.launch.authorized", 0, binding.external_dispatch_key)) == 1,
        "phase 2 lacks exact G0 launch authorization",
    )
    _require(
        not _matching_events(bundle.runtime, binding.operation_id, "dispatch.launch.lookup-recorded", 0, binding.external_dispatch_key),
        "phase 2 G0 already has durable launch lookup evidence",
    )

    trusted_digest = str(bundle.executor.base.config.trusted_context_digest)
    bundle.runtime.commit_replanned(
        lambda snapshot: plan_vertical_takeover(
            snapshot,
            operation_id=binding.operation_id,
            occurred_at=bundle.runtime.clock(),
            trusted_context_digest=trusted_digest,
        )
    )
    after_takeover = rebuild_projection(bundle.runtime.backend.read_snapshot(), binding.operation_id)
    _require(after_takeover.get("generation") == 1, "trusted takeover did not create generation 1")

    result = bundle.executor.advance_until_stop(operation_id=binding.operation_id)
    after = rebuild_projection(bundle.runtime.backend.read_snapshot(), binding.operation_id)
    _require(after.get("generation") == 1, "phase 2 executor changed away from takeover generation")

    authorized = _matching_events(
        bundle.runtime,
        binding.operation_id,
        "dispatch.launch.authorized",
        1,
        binding.external_dispatch_key,
    )
    looked_up = _matching_events(
        bundle.runtime,
        binding.operation_id,
        "dispatch.launch.lookup-recorded",
        1,
        binding.external_dispatch_key,
    )
    _require(len(authorized) == 1, "phase 2 must authorize the same exact external key once in G1")
    _require(len(looked_up) == 1, "phase 2 must durably record exactly one same-key lookup/adoption in G1")
    payload = looked_up[0].get("payload") or {}
    _require(payload.get("lookup_state") == "LAUNCHED", "phase 2 did not adopt a trusted LAUNCHED receipt")
    receipt_id = str(payload.get("runtime_receipt_identity") or payload.get("receipt_id") or "")
    _require(bool(receipt_id), "phase 2 durable launch lookup lacks runtime receipt identity")

    return {
        "operation_id": binding.operation_id,
        "generation": 1,
        "semantic_effect_key": binding.semantic_effect_key,
        "external_dispatch_key": binding.external_dispatch_key,
        "runtime_receipt_identity": receipt_id,
        "result_status": result.get("status") if isinstance(result, dict) else None,
        "g1_launch_authorized_event_id": authorized[0]["event_id"],
        "g1_launch_lookup_event_id": looked_up[0]["event_id"],
    }
