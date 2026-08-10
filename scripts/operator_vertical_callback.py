#!/usr/bin/env python3
"""Recoverable trusted callback entrypoint for the v0.3 vertical Operator loop."""
from __future__ import annotations

from operator_vertical import RoleIndependencePolicy, TrustedDispatchContext, VerticalInvariantError, translate_result
from operator_vertical_recovery import plan_vertical_callback_record


class TrustedVerticalCallbackCoordinator:
    """Durably adopts one normalized callback before translating lifecycle effects.

    The wrapped executor owns Store CAS, Feature/Persist and automatic continuation. This
    coordinator is the production vertical callback entrypoint; the legacy Store callback
    primitive remains available for compatibility but is not sufficient for restart recovery.
    """

    def __init__(self, *, executor):
        self.executor = executor

    def handle(
        self,
        *,
        context: TrustedDispatchContext,
        callback_id: str,
        worker_payload: dict,
        receipts: list[dict],
        independence_policy: RoleIndependencePolicy,
        content_loader=None,
    ) -> dict:
        projection = self.executor._projection(context.operation_id)
        if projection["generation"] != context.operation_generation:
            raise VerticalInvariantError("SUPERSEDED_GENERATION", "callback belongs to a superseded generation")

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
        feature, _ = self.executor.feature_gateway.read_feature(operation_id=context.operation_id)
        try:
            event = translate_result(
                context=context,
                feature=feature,
                worker_payload=worker_payload,
                receipts=receipts,
                independence_policy=independence_policy,
                occurred_at=self.executor.runtime.clock(),
                content_loader=content_loader,
            )
        except VerticalInvariantError as exc:
            self.executor._record_fact(
                context.operation_id,
                "worker.result.rejected",
                {"code": exc.code, "reason": str(exc)[:512], "callback_id": callback_id},
            )
            if exc.code == "NEEDS_USER":
                return self.executor._stable_stop(context.operation_id, status="NEEDS_USER", reason=str(exc))
            if exc.code in {"BLOCKED", "POLICY_DENIED", "STALE_REVISION"}:
                return self.executor._stable_stop(context.operation_id, status="BLOCKED", reason=str(exc))
            raise

        self.executor._record_fact(
            context.operation_id,
            "worker.result.validated",
            {"role": context.role, "dispatch_id": context.dispatch_id, "callback_id": callback_id},
        )
        if event is None:
            worker_state = worker_payload.get("status") or worker_payload.get("verdict")
            if worker_state == "NEEDS_USER":
                return self.executor._stable_stop(
                    context.operation_id,
                    status="NEEDS_USER",
                    reason=str(worker_payload.get("summary", "Worker needs user input")),
                )
            return self.executor._stable_stop(
                context.operation_id,
                status="BLOCKED",
                reason=str(worker_payload.get("summary", "Worker blocked")),
            )

        self.executor._record_fact(
            context.operation_id,
            "feature.event.translated",
            {
                "feature_event_id": event["id"],
                "feature_revision": feature.revision,
                "callback_id": callback_id,
            },
        )
        self.executor._persist(context.operation_id, event, feature)
        return self.executor.advance_until_stop(operation_id=context.operation_id)
