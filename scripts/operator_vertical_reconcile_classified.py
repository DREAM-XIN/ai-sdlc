#!/usr/bin/env python3
"""Failure-classified restart reconciliation for exact Vertical Persist effects."""
from __future__ import annotations

from typing import Any

from operator_vertical import VerticalInvariantError
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor

_DETERMINISTIC_PERSIST_ERRORS = frozenset(
    {
        "STALE_REVISION",
        "CONFLICT",
        "UNAUTHORIZED",
        "POLICY_DENIED",
        "INVALID_REQUEST",
        "INTERNAL_FAILURE",
    }
)


class FailureClassifyingTrustedRecoveringVerticalExecutor(TrustedRecoveringVerticalExecutor):
    """Distinguish permanent exact-Persist failures from external uncertainty.

    The parent recovery algorithm remains authoritative for callback/launch and
    Persist ordering. Only the error classification around an already-linearized
    Persist is specialized here.
    """

    def _persist_blocked(self, operation_id: str, reason: str) -> dict[str, Any]:
        current = self._public(operation_id)
        if current["status"] == "CANCELLED":
            # The Store reducer deliberately forbids new loop facts after
            # cancellation. Surface a fail-closed condition without mutating the
            # cancelled journal; an exact persist.confirmed remains legal later
            # if external truth eventually proves successful application.
            raise VerticalInvariantError("BLOCKED", reason)
        return self._stable_stop(operation_id, status="BLOCKED", reason=reason)

    def _persist_wait(self, operation_id: str, reason: str) -> dict[str, Any]:
        current = self._public(operation_id)
        if current["status"] == "CANCELLED":
            # Do not append WAITING_EXTERNAL after cancellation. The caller must
            # retry reconciliation later while the durable cancelled journal
            # remains unchanged.
            raise VerticalInvariantError("EXTERNAL_WAIT", reason)
        return self._persist_waiting(operation_id, reason)

    def _classify_persist_exception(self, operation_id: str, exc: Exception):
        code = str(getattr(exc, "code", "") or "")
        if code in _DETERMINISTIC_PERSIST_ERRORS:
            return self._persist_blocked(
                operation_id,
                f"exact linearized Feature Persist cannot safely reconcile: {code}",
            )
        return self._persist_wait(
            operation_id,
            "exact linearized Feature Persist is awaiting external reconciliation",
        )

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
                return self._persist_blocked(
                    operation_id,
                    "linearized Persist lacks recoverable exact translated Feature Event",
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
            except Exception as exc:
                return self._classify_persist_exception(operation_id, exc)

            if not isinstance(receipt, dict) or receipt.get("event_id") != feature_event_id:
                return self._persist_blocked(
                    operation_id,
                    "Persist reconciliation returned an invalid exact Event receipt",
                )
            result_revision = receipt.get("result_revision")
            if result_revision != expected_revision + 1:
                return self._persist_blocked(
                    operation_id,
                    "Persist reconciliation result revision is not the exact next Feature revision",
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

        # For non-linearized translated Events the accepted parent logic remains
        # unchanged. It rechecks current Feature/candidate truth before crossing
        # Persist linearization and then delegates to the base executor.
        candidate_ids: list[str] = []
        for fact in self._events(operation_id):
            if fact["event_type"] != "feature.event.translated":
                continue
            event_id = (fact.get("payload") or {}).get("feature_event_id")
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
                return self._persist_blocked(
                    operation_id,
                    "translated Feature Event became stale before Persist linearization",
                )
            self.base._persist(operation_id, translated["feature_event"], feature)
            return True
        return None
