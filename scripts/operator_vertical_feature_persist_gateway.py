#!/usr/bin/env python3
"""Durable adapter from Vertical FeaturePersistGateway to exact GitHub Event receipts.

The accepted Vertical executor owns Persist request/linearization/confirmation in
the protected Operator Store. This adapter performs no independent authorization:
it will submit or reconcile an Event only when the exact Event has a unique
durable `feature.event.translated` fact and is already present in
`linearized_persists` for that Operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_github_feature_event_gateway import APPLIED, FeatureEventGatewayError, FeatureEventReceipt
from operator_production_feature_event_gateway import ProductionConfiguredFeatureEventGateway
from operator_store_backends import OperatorStoreRuntime
from operator_store_model import (
    digest_json,
    normalize_repository,
    operation_events,
    operation_ids,
    rebuild_projection,
)


@dataclass(frozen=True)
class DurableTranslatedEvent:
    operation_id: str
    feature_id: str
    expected_revision: int
    target_ref: str
    event_id: str
    event: dict[str, Any]
    event_digest: str


class DurableVerticalFeaturePersistGateway:
    """Implement the accepted Vertical FeaturePersistGateway from durable facts."""

    def __init__(
        self,
        *,
        runtime: OperatorStoreRuntime,
        event_gateway: ProductionConfiguredFeatureEventGateway,
    ):
        if not isinstance(runtime, OperatorStoreRuntime):
            raise ValueError("Vertical Persist gateway requires trusted OperatorStoreRuntime")
        if not isinstance(event_gateway, ProductionConfiguredFeatureEventGateway):
            raise ValueError("Vertical Persist gateway requires production Feature Event gateway")
        self.runtime = runtime
        self.event_gateway = event_gateway

    def _resolve(self, *, event_id: str, target_ref: str) -> DurableTranslatedEvent:
        if not event_id or not target_ref:
            raise FeatureEventGatewayError("INVALID_REQUEST", "exact Event id and target ref are required")
        snapshot = self.runtime.backend.read_snapshot()
        matches: list[tuple[str, dict[str, Any]]] = []
        for operation_id in operation_ids(snapshot):
            for fact in operation_events(snapshot, operation_id):
                if fact.get("event_type") != "feature.event.translated":
                    continue
                payload = fact.get("payload") or {}
                if payload.get("feature_event_id") == event_id:
                    matches.append((operation_id, dict(payload)))
        if not matches:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Event lacks durable translated Feature fact")

        operation_set = {operation_id for operation_id, _ in matches}
        if len(operation_set) != 1:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "Feature Event id is bound to multiple Operations")
        operation_id = matches[0][0]
        canonical = matches[0][1]
        for _, payload in matches[1:]:
            if digest_json(payload) != digest_json(canonical):
                raise FeatureEventGatewayError("INTERNAL_FAILURE", "conflicting durable translated Feature Event facts")

        projection = rebuild_projection(snapshot, operation_id)
        if event_id not in set(projection.get("linearized_persists", [])):
            raise FeatureEventGatewayError("POLICY_DENIED", "Feature Event has not crossed Persist linearization")

        feature_id = str(projection.get("feature_id") or "")
        repository = normalize_repository(str(projection.get("target_repository") or ""))
        configured_repository = normalize_repository(self.event_gateway.configuration.repository)
        if not feature_id or repository != configured_repository:
            raise FeatureEventGatewayError("UNAUTHORIZED", "translated Event is outside trusted Feature Event scope")

        durable_target_ref = str(canonical.get("target_ref") or "")
        if durable_target_ref != target_ref:
            raise FeatureEventGatewayError("UNAUTHORIZED", "Persist target ref differs from durable translated Event")
        configured_target_ref = self.event_gateway.configuration.target_ref(feature_id)
        if configured_target_ref != durable_target_ref:
            raise FeatureEventGatewayError("UNAUTHORIZED", "durable translated Event ref differs from trusted configuration")

        expected_revision = canonical.get("feature_revision")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event lacks exact Feature revision")
        event = canonical.get("feature_event")
        if not isinstance(event, dict):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event lacks exact Feature Event body")
        event_digest = str(canonical.get("feature_event_digest") or "")
        if not event_digest or digest_json(event) != event_digest:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event body/digest binding is invalid")
        if str(event.get("id") or "") != event_id or str(event.get("feature_id") or "") != feature_id:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event identity binding is invalid")
        if event.get("expected_revision") != expected_revision:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event revision binding is invalid")

        return DurableTranslatedEvent(
            operation_id=operation_id,
            feature_id=feature_id,
            expected_revision=expected_revision,
            target_ref=durable_target_ref,
            event_id=event_id,
            event=dict(event),
            event_digest=event_digest,
        )

    @staticmethod
    def _canonical_event_digest(binding: DurableTranslatedEvent) -> str:
        event_id, text = CanonicalExactRevisionGitHubFeatureEventGateway._validate_event(
            binding.event,
            feature_id=binding.feature_id,
            expected_revision=binding.expected_revision,
        )
        if event_id != binding.event_id:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "canonical Event id changed during receipt lookup")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _applied_receipt(binding: DurableTranslatedEvent, receipt: FeatureEventReceipt) -> dict[str, Any]:
        if receipt.state != APPLIED:
            raise FeatureEventGatewayError("TRANSIENT_FAILURE", f"exact Feature Event is not applied: {receipt.state}")
        exact_result_revision = binding.expected_revision + 1
        if receipt.result_revision != exact_result_revision:
            raise FeatureEventGatewayError(
                "INTERNAL_FAILURE",
                "Feature Event receipt does not identify the exact next revision",
            )
        return {
            "event_id": binding.event_id,
            "result_revision": exact_result_revision,
        }

    def lookup_feature_event(self, *, event_id: str, target_ref: str) -> dict[str, Any] | None:
        binding = self._resolve(event_id=event_id, target_ref=target_ref)
        receipt = self.event_gateway.lookup_receipt(
            feature_id=binding.feature_id,
            event_id=binding.event_id,
            expected_revision=binding.expected_revision,
            expected_event_digest=self._canonical_event_digest(binding),
        )
        if receipt.state != APPLIED:
            return None
        return self._applied_receipt(binding, receipt)

    def persist_feature_event(self, *, event: dict[str, Any], target_ref: str) -> dict[str, Any]:
        event_id = str((event or {}).get("id") or "")
        binding = self._resolve(event_id=event_id, target_ref=target_ref)
        if digest_json(event) != binding.event_digest:
            raise FeatureEventGatewayError("CONFLICT", "Persist Event differs from durable translated Event")
        receipt = self.event_gateway.persist_exact_event(
            feature_id=binding.feature_id,
            expected_revision=binding.expected_revision,
            event=dict(binding.event),
        )
        return self._applied_receipt(binding, receipt)
