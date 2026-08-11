#!/usr/bin/env python3
"""Durable adapter from Vertical FeaturePersistGateway to exact GitHub Event receipts.

The accepted Vertical executor owns Persist request/linearization/confirmation in
the protected Operator Store. This adapter performs no independent authorization:
it submits or reconciles an Event only when one exact durable translated Event and
its Persist request/linearization all belong to the same Operation generation.
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
    operation_generation: int
    feature_id: str
    expected_revision: int
    target_ref: str
    event_id: str
    event: dict[str, Any]
    event_digest: str
    candidate_head_sha: str | None


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

    @staticmethod
    def _fact_generation(fact: dict[str, Any]) -> int:
        generation = fact.get("operation_generation")
        if not isinstance(generation, int) or generation < 0:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "durable Operation fact lacks valid generation")
        return generation

    @staticmethod
    def _persist_binding_matches(
        payload: dict[str, Any],
        *,
        expected_revision: int,
        target_ref: str,
        candidate_head_sha: str | None,
    ) -> bool:
        return (
            payload.get("expected_revision") == expected_revision
            and payload.get("target_ref") == target_ref
            and payload.get("candidate_head_sha") == candidate_head_sha
        )

    def _resolve(self, *, event_id: str, target_ref: str) -> DurableTranslatedEvent:
        if not event_id or not target_ref:
            raise FeatureEventGatewayError("INVALID_REQUEST", "exact Event id and target ref are required")
        snapshot = self.runtime.backend.read_snapshot()

        translated: list[tuple[str, int, dict[str, Any]]] = []
        requested: list[tuple[str, int, dict[str, Any]]] = []
        linearized: list[tuple[str, int, dict[str, Any]]] = []
        for operation_id in operation_ids(snapshot):
            for fact in operation_events(snapshot, operation_id):
                payload = fact.get("payload") or {}
                if payload.get("feature_event_id") != event_id:
                    continue
                event_type = fact.get("event_type")
                if event_type not in {"feature.event.translated", "persist.requested", "persist.linearized"}:
                    continue
                row = (operation_id, self._fact_generation(fact), dict(payload))
                if event_type == "feature.event.translated":
                    translated.append(row)
                elif event_type == "persist.requested":
                    requested.append(row)
                else:
                    linearized.append(row)

        if not translated:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Event lacks durable translated Feature fact")

        translated_identities = {(operation_id, generation) for operation_id, generation, _ in translated}
        if len(translated_identities) != 1:
            raise FeatureEventGatewayError(
                "INTERNAL_FAILURE",
                "Feature Event id is translated across multiple Operation generations",
            )
        operation_id, operation_generation = next(iter(translated_identities))
        canonical = translated[0][2]
        for _, _, payload in translated[1:]:
            if digest_json(payload) != digest_json(canonical):
                raise FeatureEventGatewayError("INTERNAL_FAILURE", "conflicting durable translated Feature Event facts")

        expected_revision = canonical.get("feature_revision")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event lacks exact Feature revision")
        durable_target_ref = str(canonical.get("target_ref") or "")
        if durable_target_ref != target_ref:
            raise FeatureEventGatewayError("UNAUTHORIZED", "Persist target ref differs from durable translated Event")
        candidate_head_sha = canonical.get("candidate_head_sha")
        if candidate_head_sha is not None and not isinstance(candidate_head_sha, str):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "translated Event candidate binding is invalid")

        expected_identity = (operation_id, operation_generation)
        for label, rows in (("request", requested), ("linearization", linearized)):
            identities = {(row_operation, row_generation) for row_operation, row_generation, _ in rows}
            if any(identity != expected_identity for identity in identities):
                raise FeatureEventGatewayError(
                    "INTERNAL_FAILURE",
                    f"Feature Event Persist {label} spans conflicting Operation generations",
                )
            matching = [
                payload
                for row_operation, row_generation, payload in rows
                if (row_operation, row_generation) == expected_identity
            ]
            if not matching:
                raise FeatureEventGatewayError(
                    "POLICY_DENIED",
                    f"Feature Event lacks same-generation Persist {label}",
                )
            for payload in matching:
                if not self._persist_binding_matches(
                    payload,
                    expected_revision=expected_revision,
                    target_ref=durable_target_ref,
                    candidate_head_sha=candidate_head_sha,
                ):
                    raise FeatureEventGatewayError(
                        "INTERNAL_FAILURE",
                        f"Feature Event Persist {label} binding conflicts with durable translation",
                    )

        # Projection membership remains an invariant cross-check only. Generation
        # authority comes from the exact durable fact stream above.
        projection = rebuild_projection(snapshot, operation_id)
        if event_id not in set(projection.get("requested_persists", [])):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "Persist request fact is missing from Store projection")
        if event_id not in set(projection.get("linearized_persists", [])):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "Persist linearization fact is missing from Store projection")

        feature_id = str(projection.get("feature_id") or "")
        repository = normalize_repository(str(projection.get("target_repository") or ""))
        configured_repository = normalize_repository(self.event_gateway.configuration.repository)
        if not feature_id or repository != configured_repository:
            raise FeatureEventGatewayError("UNAUTHORIZED", "translated Event is outside trusted Feature Event scope")

        configured_target_ref = self.event_gateway.configuration.target_ref(feature_id)
        if configured_target_ref != durable_target_ref:
            raise FeatureEventGatewayError("UNAUTHORIZED", "durable translated Event ref differs from trusted configuration")

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
            operation_generation=operation_generation,
            feature_id=feature_id,
            expected_revision=expected_revision,
            target_ref=durable_target_ref,
            event_id=event_id,
            event=dict(event),
            event_digest=event_digest,
            candidate_head_sha=candidate_head_sha,
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
        return {"event_id": binding.event_id, "result_revision": exact_result_revision}

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
