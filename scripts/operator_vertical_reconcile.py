#!/usr/bin/env python3
"""Bounded restart reconciliation for the approved v0.3 vertical Operator loop."""
from __future__ import annotations

from typing import Any

from operator_store import plan_launch_lookup
from operator_store_model import digest_json, operation_events
from operator_vertical import FeatureSnapshot, TrustedDispatchContext, VerticalInvariantError
from operator_vertical_callback import process_recorded_callback
from operator_vertical_controller import VerticalAction, select_vertical_action
from operator_vertical_recovery import recover_vertical_callback
from operator_vertical_store import plan_vertical_persist_confirmed


_STABLE = {"WAITING_EXTERNAL", "BLOCKED", "NEEDS_USER", "DONE", "CANCELLED"}


class TrustedRecoveringVerticalExecutor:
    """Add only the approved vertical recovery cases over the existing executor.

    An already recorded UNKNOWN launch is never re-probed or cleared here; UNKNOWN
    resolution remains outside this Feature.
    """

    def __init__(
        self,
        *,
        base_executor,
        content_loader,
        trusted_role_policy: str,
        collector_namespace_policy: str,
    ):
        if not callable(content_loader):
            raise ValueError("trusted collector content loader is required")
        if not trusted_role_policy or not collector_namespace_policy:
            raise ValueError("trusted vertical recovery policy is incomplete")
        self.base = base_executor
        self.content_loader = content_loader
        self.trusted_role_policy = trusted_role_policy
        self.collector_namespace_policy = collector_namespace_policy

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _events(self, operation_id: str) -> list[dict[str, Any]]:
        return operation_events(self.runtime.backend.read_snapshot(), operation_id)

    def _translated_payload(self, operation_id: str, feature_event_id: str) -> dict[str, Any] | None:
        matches = []
        for event in self._events(operation_id):
            if event["event_type"] != "feature.event.translated":
                continue
            payload = event.get("payload") or {}
            if payload.get("feature_event_id") == feature_event_id:
                matches.append(payload)
        if not matches:
            return None
        canonical = matches[0]
        for payload in matches[1:]:
            if digest_json(payload) != digest_json(canonical):
                raise VerticalInvariantError("BLOCKED", "conflicting durable translated Feature Event facts")
        feature_event = canonical.get("feature_event")
        if not isinstance(feature_event, dict):
            return None
        if digest_json(feature_event) != canonical.get("feature_event_digest"):
            raise VerticalInvariantError("BLOCKED", "translated Feature Event digest mismatch")
        return canonical

    def _record_translated(
        self,
        *,
        operation_id: str,
        event: dict[str, Any],
        feature: FeatureSnapshot,
        callback_id: str | None = None,
    ) -> None:
        payload = {
            "feature_event_id": event["id"],
            "feature_event_digest": digest_json(event),
            "feature_event": event,
            "feature_revision": feature.revision,
            "feature_stage": feature.current_stage,
            "feature_manifest_digest": feature.manifest_digest,
            "candidate_head_sha": feature.candidate_head_sha,
            "target_ref": feature.target_ref,
        }
        if callback_id:
            payload["callback_id"] = callback_id
        self._record_fact(operation_id, "feature.event.translated", payload)

    def _confirm_persist(
        self,
        *,
        operation_id: str,
        feature_event_id: str,
        expected_revision: int,
        target_ref: str,
        candidate_head_sha: str | None,
        result_revision: int,
    ) -> None:
        projection = self._projection(operation_id)
        self._commit(
            lambda snapshot: plan_vertical_persist_confirmed(
                snapshot,
                operation_id=operation_id,
                generation=projection["generation"],
                feature_event_id=feature_event_id,
                expected_revision=expected_revision,
                target_ref=target_ref,
                candidate_head_sha=candidate_head_sha,
                occurred_at=self.runtime.clock(),
                trusted_context_digest=self.config.trusted_context_digest,
                result_revision=result_revision,
            )
        )

    def _persist_waiting(self, operation_id: str, reason: str) -> dict[str, Any]:
        self._record_fact(
            operation_id,
            "loop.stable-stop",
            {"status": "WAITING_EXTERNAL", "reason": reason[:512]},
        )
        return self._public(operation_id)

    def _reconcile_persist(self, operation_id: str) -> dict[str, Any] | bool | None:
        projection = self._projection(operation_id)
        requested = list(projection.get("requested_persists", []))
        linearized = set(projection.get("linearized_persists", []))
        confirmed = set(projection.get("confirmed_persists", []))

        for feature_event_id in requested:
            if feature_event_id not in linearized or feature_event_id in confirmed:
                continue
            translated = self._translated_payload(operation_id, feature_event_id)
            if translated is None:
                return self._stable_stop(
                    operation_id,
                    status="BLOCKED",
                    reason="linearized Persist lacks recoverable exact translated Feature Event",
                )
            event = translated["feature_event"]
            expected_revision = int(translated["feature_revision"])
            target_ref = str(translated["target_ref"])
            candidate = translated.get("candidate_head_sha")
            try:
                receipt = self.persist_gateway.lookup_feature_event(
                    event_id=feature_event_id,
                    target_ref=target_ref,
                )
                if receipt is None:
                    receipt = self.persist_gateway.persist_feature_event(
                        event=event,
                        target_ref=target_ref,
                    )
            except Exception:
                return self._persist_waiting(
                    operation_id,
                    "exact linearized Feature Persist is awaiting reconciliation",
                )
            if not isinstance(receipt, dict) or receipt.get("event_id") != feature_event_id:
                return self._stable_stop(
                    operation_id,
                    status="BLOCKED",
                    reason="Persist reconciliation returned an invalid exact Event receipt",
                )
            result_revision = receipt.get("result_revision")
            if result_revision != expected_revision + 1:
                return self._stable_stop(
                    operation_id,
                    status="BLOCKED",
                    reason="Persist reconciliation result revision is not the exact next Feature revision",
                )
            self._confirm_persist(
                operation_id=operation_id,
                feature_event_id=feature_event_id,
                expected_revision=expected_revision,
                target_ref=target_ref,
                candidate_head_sha=candidate,
                result_revision=result_revision,
            )
            return True

        candidate_ids: list[str] = []
        for event in self._events(operation_id):
            if event["event_type"] != "feature.event.translated":
                continue
            event_id = (event.get("payload") or {}).get("feature_event_id")
            if event_id and event_id not in confirmed and event_id not in linearized:
                candidate_ids.append(str(event_id))
        for feature_event_id in candidate_ids:
            translated = self._translated_payload(operation_id, feature_event_id)
            if translated is None:
                continue
            current = self._public(operation_id)
            if current["status"] in {"CANCELLED", "BLOCKED", "NEEDS_USER"}:
                return None
            feature, _ = self.feature_gateway.read_feature(operation_id=operation_id)
            if (
                feature.revision != translated["feature_revision"]
                or feature.current_stage != translated["feature_stage"]
                or feature.manifest_digest != translated["feature_manifest_digest"]
                or feature.candidate_head_sha != translated.get("candidate_head_sha")
                or feature.target_ref != translated["target_ref"]
            ):
                return self._stable_stop(
                    operation_id,
                    status="BLOCKED",
                    reason="translated Feature Event became stale before Persist linearization",
                )
            self.base._persist(operation_id, translated["feature_event"], feature)
            return True
        return None

    def _reconcile_callback(self, operation_id: str) -> bool | None:
        current = self._public(operation_id)
        if current["status"] in {"CANCELLED", "DONE", "NEEDS_USER"}:
            return None
        events = self._events(operation_id)
        rejected: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["event_type"] != "worker.result.rejected":
                continue
            payload = event.get("payload") or {}
            callback_id = str(payload.get("callback_id") or "")
            if callback_id:
                rejected[callback_id] = dict(payload)
        translated = {
            str((event.get("payload") or {}).get("callback_id"))
            for event in events
            if event["event_type"] == "feature.event.translated"
            and (event.get("payload") or {}).get("callback_id")
        }
        validated = {
            str((event.get("payload") or {}).get("callback_id"))
            for event in events
            if event["event_type"] == "worker.result.validated"
            and (event.get("payload") or {}).get("callback_id")
        }
        generation = self._projection(operation_id)["generation"]
        for event in events:
            if event["event_type"] != "worker.callback.recorded":
                continue
            if int(event["operation_generation"]) != generation:
                continue
            payload = event.get("payload") or {}
            callback_id = str(payload.get("callback_id") or "")
            if not callback_id or callback_id in translated:
                continue
            rejection = rejected.get(callback_id)
            if rejection is not None:
                code = str(rejection.get("code") or "")
                reason = str(rejection.get("reason") or "durable callback result rejection")
                if code == "NEEDS_USER":
                    if current["status"] != "NEEDS_USER":
                        self._stable_stop(operation_id, status="NEEDS_USER", reason=reason)
                        return True
                    continue
                if code in {"BLOCKED", "POLICY_DENIED", "STALE_REVISION"}:
                    if current["status"] != "BLOCKED":
                        self._stable_stop(operation_id, status="BLOCKED", reason=reason)
                        return True
                    continue
                continue
            if callback_id in validated and current["status"] in {"BLOCKED", "NEEDS_USER"}:
                continue
            envelope = recover_vertical_callback(
                self.runtime.backend.read_snapshot(),
                operation_id=operation_id,
                callback_id=callback_id,
            )
            context = TrustedDispatchContext(**dict(envelope["trusted_context"]))
            process_recorded_callback(
                self,
                context=context,
                callback_id=callback_id,
                worker_payload=dict(envelope["worker_payload"]),
                receipts=list(envelope["collected_outputs"]),
                trusted_role_policy=self.trusted_role_policy,
                collector_namespace_policy=self.collector_namespace_policy,
                content_loader=self.content_loader,
                continue_after=False,
            )
            return True
        return None

    def _reconcile_launch(self, operation_id: str) -> bool | None:
        projection = self._projection(operation_id)
        generation = projection["generation"]
        events = self._events(operation_id)
        looked_up = {
            str((event.get("payload") or {}).get("external_dispatch_key"))
            for event in events
            if event["event_type"] == "dispatch.launch.lookup-recorded"
            and int(event["operation_generation"]) == generation
        }
        for event in events:
            if event["event_type"] != "dispatch.launch.authorized":
                continue
            if int(event["operation_generation"]) != generation:
                continue
            payload = event.get("payload") or {}
            external_key = str(payload.get("external_dispatch_key") or "")
            if not external_key or external_key in looked_up:
                continue
            try:
                receipt = self.dispatch_gateway.lookup(external_dispatch_key=external_key)
            except Exception:
                receipt = {"lookup_state": "UNKNOWN", "receipt_id": None}
            if not isinstance(receipt, dict):
                receipt = {"lookup_state": "UNKNOWN", "receipt_id": None}
            state = str(receipt.get("lookup_state", "UNKNOWN"))
            if state not in {"NOT_LAUNCHED", "LAUNCHED", "UNKNOWN"}:
                state = "UNKNOWN"
            self._commit(
                lambda snapshot: plan_launch_lookup(
                    snapshot,
                    operation_id=operation_id,
                    generation=generation,
                    external_dispatch_key_value=external_key,
                    lookup_state=state,
                    receipt_id=receipt.get("receipt_id"),
                    occurred_at=self.runtime.clock(),
                    trusted_context_digest=self.config.trusted_context_digest,
                )
            )
            return True
        return None

    def advance_action(self, *, operation_id: str, action: VerticalAction) -> dict[str, Any]:
        if action.kind != "persist":
            return self.base.advance_action(operation_id=operation_id, action=action)
        feature, _ = self.feature_gateway.read_feature(operation_id=operation_id)
        self._record_fact(
            operation_id,
            "loop.step.selected",
            {
                "step": action.step,
                "kind": action.kind,
                "feature_revision": feature.revision,
                "task_identity": action.task_identity,
            },
        )
        if not action.feature_event:
            raise VerticalInvariantError("INTERNAL_FAILURE", "Persist action lacks bounded Feature Event")
        self._record_translated(
            operation_id=operation_id,
            event=action.feature_event,
            feature=feature,
        )
        return self.base._persist(operation_id, action.feature_event, feature)

    def advance_until_stop(self, *, operation_id: str) -> dict[str, Any]:
        for _ in range(self.config.max_auto_steps):
            persist = self._reconcile_persist(operation_id)
            if isinstance(persist, dict):
                return persist
            if persist:
                continue
            if self._reconcile_callback(operation_id):
                continue
            if self._reconcile_launch(operation_id):
                continue

            current = self._public(operation_id)
            if current["status"] in _STABLE:
                return current
            feature, manifest = self.feature_gateway.read_feature(operation_id=operation_id)
            try:
                action = select_vertical_action(
                    feature=feature,
                    manifest=manifest,
                    occurred_at=self.runtime.clock(),
                )
            except VerticalInvariantError as exc:
                if exc.code in {"BLOCKED", "NEEDS_USER"}:
                    return self._stable_stop(operation_id, status=exc.code, reason=str(exc))
                raise
            self.advance_action(operation_id=operation_id, action=action)
        return self._stable_stop(
            operation_id,
            status="BLOCKED",
            reason="vertical recovery/auto-step bound exceeded",
        )
