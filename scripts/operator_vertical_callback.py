#!/usr/bin/env python3
"""Recoverable trusted callback entrypoint for the v0.3 vertical Operator loop."""
from __future__ import annotations

from operator_store_model import digest_json
from operator_vertical import TrustedDispatchContext, VerticalInvariantError, translate_result
from operator_vertical_recovery import derive_role_independence_policy, plan_vertical_callback_record


def process_recorded_callback(
    executor,
    *,
    context: TrustedDispatchContext,
    callback_id: str,
    worker_payload: dict,
    receipts: list[dict],
    trusted_role_policy: str,
    collector_namespace_policy: str,
    content_loader,
    continue_after: bool,
) -> dict:
    """Translate one already-durable callback using only trusted runtime policy/dependencies."""
    if not trusted_role_policy or not collector_namespace_policy or not callable(content_loader):
        raise VerticalInvariantError("INTERNAL_FAILURE", "trusted callback policy/collector backing is incomplete")
    if context.target_ref != executor.config.target_ref:
        raise VerticalInvariantError("STALE_REVISION", "callback target ref is outside trusted vertical runtime")

    snapshot = executor.runtime.backend.read_snapshot()
    policy = derive_role_independence_policy(
        snapshot,
        operation_id=context.operation_id,
        exclude_callback_id=callback_id,
    )
    try:
        # The callback envelope is already durable before this function is entered.
        # Fresh Feature/candidate binding failures are therefore deterministic
        # callback-result rejections, not transient callback-adoption failures.
        feature, _ = executor.feature_gateway.read_feature(operation_id=context.operation_id)
        if feature.target_ref != context.target_ref:
            raise VerticalInvariantError("STALE_REVISION", "callback Feature ref binding changed")
        if feature.revision != context.expected_revision or feature.current_stage != context.feature_stage:
            raise VerticalInvariantError("STALE_REVISION", "callback Feature revision/stage binding changed")
        if feature.candidate_head_sha != context.candidate_head_sha:
            raise VerticalInvariantError("STALE_REVISION", "callback candidate head binding changed")
        event = translate_result(
            context=context,
            feature=feature,
            worker_payload=worker_payload,
            receipts=receipts,
            independence_policy=policy,
            occurred_at=executor.runtime.clock(),
            content_loader=content_loader,
        )
    except VerticalInvariantError as exc:
        executor._record_fact(
            context.operation_id,
            "worker.result.rejected",
            {"code": exc.code, "reason": str(exc)[:512], "callback_id": callback_id},
        )
        if exc.code == "NEEDS_USER":
            return executor._stable_stop(context.operation_id, status="NEEDS_USER", reason=str(exc))
        if exc.code in {"BLOCKED", "POLICY_DENIED", "STALE_REVISION"}:
            return executor._stable_stop(context.operation_id, status="BLOCKED", reason=str(exc))
        raise

    executor._record_fact(
        context.operation_id,
        "worker.result.validated",
        {
            "role": context.role,
            "dispatch_id": context.dispatch_id,
            "callback_id": callback_id,
            "trusted_role_policy": trusted_role_policy,
            "collector_namespace_policy": collector_namespace_policy,
        },
    )
    if event is None:
        worker_state = worker_payload.get("status") or worker_payload.get("verdict")
        if worker_state == "NEEDS_USER":
            return executor._stable_stop(
                context.operation_id,
                status="NEEDS_USER",
                reason=str(worker_payload.get("summary", "Worker needs user input")),
            )
        return executor._stable_stop(
            context.operation_id,
            status="BLOCKED",
            reason=str(worker_payload.get("summary", "Worker blocked")),
        )

    translated = {
        "feature_event_id": event["id"],
        "feature_event_digest": digest_json(event),
        "feature_event": event,
        "feature_revision": feature.revision,
        "feature_stage": feature.current_stage,
        "feature_manifest_digest": feature.manifest_digest,
        "candidate_head_sha": feature.candidate_head_sha,
        "target_ref": feature.target_ref,
        "callback_id": callback_id,
    }
    executor._record_fact(context.operation_id, "feature.event.translated", translated)
    executor._persist(context.operation_id, event, feature)
    if continue_after:
        return executor.advance_until_stop(operation_id=context.operation_id)
    return executor._public(context.operation_id)


class TrustedVerticalCallbackCoordinator:
    """Durably adopt a normalized collector callback before lifecycle translation."""

    def __init__(
        self,
        *,
        executor,
        trusted_role_policy: str,
        collector_namespace_policy: str,
        content_loader,
    ):
        if not trusted_role_policy or not collector_namespace_policy or not callable(content_loader):
            raise ValueError("trusted callback coordinator dependencies are incomplete")
        self.executor = executor
        self.trusted_role_policy = trusted_role_policy
        self.collector_namespace_policy = collector_namespace_policy
        self.content_loader = content_loader

    def handle(
        self,
        *,
        context: TrustedDispatchContext,
        callback_id: str,
        worker_payload: dict,
        receipts: list[dict],
    ) -> dict:
        projection = self.executor._projection(context.operation_id)
        if projection["generation"] != context.operation_generation:
            raise VerticalInvariantError("SUPERSEDED_GENERATION", "callback belongs to a superseded generation")
        if context.target_ref != self.executor.config.target_ref:
            raise VerticalInvariantError("STALE_REVISION", "callback target ref is outside trusted vertical runtime")

        self.executor._commit(
            lambda snapshot: plan_vertical_callback_record(
                snapshot,
                context=context,
                callback_id=callback_id,
                worker_payload=worker_payload,
                receipts=receipts,
                occurred_at=self.executor.runtime.clock(),
                trusted_context_digest=self.executor.config.trusted_context_digest,
            )
        )
        return process_recorded_callback(
            self.executor,
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
            trusted_role_policy=self.trusted_role_policy,
            collector_namespace_policy=self.collector_namespace_policy,
            content_loader=self.content_loader,
            continue_after=True,
        )
