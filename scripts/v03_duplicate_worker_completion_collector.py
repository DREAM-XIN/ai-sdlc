#!/usr/bin/env python3
"""Verification-only replay fence for duplicate Worker completion in Issue #221.

The first completion is always delegated to ProductionGhAwVerticalResultCollector.
After that exact callback has been durably adopted, a repeated collection of the
same first-attempt run may be recognized read-only even after Persist advanced the
Vertical Feature revision. No generic production launch/reservation predicate is
weakened by this verification-only wrapper.
"""
from __future__ import annotations

from typing import Any

from operator_store_model import canonical_json, digest_json, operation_events
from operator_vertical import validate_worker_result, VerticalInvariantError
from operator_vertical_recovery import recover_vertical_callback


class DuplicateWorkerCompletionReplayError(RuntimeError):
    pass


class ReplaySafeProductionGhAwCollector:
    verification_only = True

    def __init__(self, *, delegate: Any):
        if delegate is None or not callable(getattr(delegate, "handle", None)):
            raise ValueError("production collector delegate is required")
        if getattr(delegate, "result_source", None) is None:
            raise ValueError("production collector result source is required")
        if getattr(delegate, "callback_coordinator", None) is None:
            raise ValueError("production collector callback coordinator is required")
        self.delegate = delegate
        self.result_source = delegate.result_source
        self.callback_coordinator = delegate.callback_coordinator
        self.replay_callback_ids: list[str] = []

    @staticmethod
    def _callback_rows(snapshot, operation_id: str, external_dispatch_key: str):
        rows = []
        for event in operation_events(snapshot, operation_id):
            if event.get("event_type") != "worker.callback.recorded":
                continue
            payload = event.get("payload") or {}
            if payload.get("external_dispatch_key") == external_dispatch_key:
                rows.append(payload)
        return rows

    @staticmethod
    def _validated_count(snapshot, operation_id: str, callback_id: str) -> int:
        return sum(
            1
            for event in operation_events(snapshot, operation_id)
            if event.get("event_type") == "worker.result.validated"
            and (event.get("payload") or {}).get("callback_id") == callback_id
        )

    @staticmethod
    def _translated_count(snapshot, operation_id: str, callback_id: str) -> int:
        return sum(
            1
            for event in operation_events(snapshot, operation_id)
            if event.get("event_type") == "feature.event.translated"
            and (event.get("payload") or {}).get("callback_id") == callback_id
        )

    @staticmethod
    def _resolved_output_shape(resolved) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            sorted(
                (
                    str(row.label),
                    str(row.kind),
                    str(row.media_type),
                    str(row.trusted_uri),
                )
                for row in resolved.outputs
            )
        )

    @staticmethod
    def _durable_output_shape(envelope: dict[str, Any]) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            sorted(
                (
                    str(row.get("label") or ""),
                    str(row.get("kind") or ""),
                    str(row.get("media_type") or ""),
                    str(row.get("trusted_uri") or ""),
                )
                for row in (envelope.get("collected_outputs") or [])
                if isinstance(row, dict)
            )
        )

    def _read_only_exact_replay(self, *, operation_id: str, external_dispatch_key: str):
        executor = self.callback_coordinator.executor
        snapshot = executor.runtime.backend.read_snapshot()
        rows = self._callback_rows(snapshot, operation_id, external_dispatch_key)
        if not rows:
            return None
        if len(rows) != 1:
            raise DuplicateWorkerCompletionReplayError(
                "duplicate-completion replay requires exactly one durable callback for the dispatch"
            )
        callback_id = str(rows[0].get("callback_id") or "")
        if not callback_id:
            raise DuplicateWorkerCompletionReplayError("durable callback lacks exact callback identity")
        envelope = recover_vertical_callback(
            snapshot,
            operation_id=operation_id,
            callback_id=callback_id,
        )
        trusted = envelope.get("trusted_context") or {}
        if (
            trusted.get("operation_id") != operation_id
            or trusted.get("external_dispatch_key") != external_dispatch_key
            or not trusted.get("runtime_receipt_identity")
        ):
            raise DuplicateWorkerCompletionReplayError(
                "durable callback envelope does not bind the requested Operation/dispatch"
            )
        resolved = self.result_source.resolve(
            external_dispatch_key=external_dispatch_key,
            expected_receipt_identity=str(trusted["runtime_receipt_identity"]),
            trusted_context=dict(trusted),
        )
        worker_payload = validate_worker_result(str(trusted.get("role") or ""), resolved.role_payload)
        if canonical_json(worker_payload) != canonical_json(envelope.get("worker_payload") or {}):
            raise VerticalInvariantError("STALE_REVISION", "replayed Worker completion payload differs from durable callback")
        if self._resolved_output_shape(resolved) != self._durable_output_shape(envelope):
            raise VerticalInvariantError("STALE_REVISION", "replayed Worker completion outputs differ from durable callback")
        expected_callback_id = "gh-aw-callback-" + digest_json(
            {
                "operation_id": operation_id,
                "generation": int(trusted["operation_generation"]),
                "external_dispatch_key": external_dispatch_key,
                "runtime_receipt_identity": str(trusted["runtime_receipt_identity"]),
                "run_id": resolved.run.run_id,
            }
        )[:24]
        if expected_callback_id != callback_id:
            raise VerticalInvariantError("STALE_REVISION", "replayed first-attempt run does not derive durable callback identity")
        if self._validated_count(snapshot, operation_id, callback_id) != 1:
            raise DuplicateWorkerCompletionReplayError(
                "duplicate COMPLETED replay requires exactly one durable validated result"
            )
        if self._translated_count(snapshot, operation_id, callback_id) != 1:
            raise DuplicateWorkerCompletionReplayError(
                "duplicate COMPLETED replay requires exactly one durable translated Feature Event"
            )
        self.replay_callback_ids.append(callback_id)
        return executor._public(operation_id)

    def handle(self, *, operation_id: str, external_dispatch_key: str):
        replay = self._read_only_exact_replay(
            operation_id=operation_id,
            external_dispatch_key=external_dispatch_key,
        )
        if replay is not None:
            return replay
        return self.delegate.handle(
            operation_id=operation_id,
            external_dispatch_key=external_dispatch_key,
        )
