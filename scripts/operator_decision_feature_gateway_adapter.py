#!/usr/bin/env python3
"""Bounded adapter from accepted Decision runtime to release-safe Feature Events.

This bridge deliberately does not define Product/authorization Decision
semantics. A server-owned `DecisionEventPlanner` is responsible for translating
the already-validated durable Decision outcome. AI/client input cannot supply a
raw Feature Event or arbitrary `changes` through this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from operator_github_feature_event_gateway import FeatureEventGatewayError
from operator_production_feature_event_gateway import ProductionConfiguredFeatureEventGateway


@dataclass(frozen=True)
class DecisionEventPlan:
    feature_id: str
    expected_revision: int
    event: dict[str, Any]


class DecisionEventPlanner(Protocol):
    def plan(self, *, trusted_inputs: dict[str, Any], current_feature: dict[str, Any]) -> DecisionEventPlan:
        ...


class BoundedDecisionFeatureGatewayAdapter:
    """Expose Feature truth + bounded Decision response persistence to Coordinator."""

    def __init__(
        self,
        *,
        event_gateway: ProductionConfiguredFeatureEventGateway,
        planner: DecisionEventPlanner,
    ):
        if planner is None:
            raise ValueError("trusted Decision Event planner is required")
        self.event_gateway = event_gateway
        self.planner = planner

    def _bound_target(self, values: dict[str, Any]) -> tuple[str, int]:
        feature_id = str(values.get("feature_id") or "")
        if not feature_id:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Decision Feature id is required")
        target_ref = values.get("target_ref")
        if target_ref is not None and str(target_ref) != self.event_gateway.configuration.target_ref(feature_id):
            raise FeatureEventGatewayError("UNAUTHORIZED", "Decision target ref differs from trusted Feature binding")
        repository = values.get("repository") or values.get("target_repository")
        if repository is not None and str(repository).lower() != self.event_gateway.configuration.repository.lower():
            raise FeatureEventGatewayError("UNAUTHORIZED", "Decision repository differs from trusted Event gateway")
        revision = values.get("expected_revision")
        if revision is None:
            revision = values.get("expected_feature_revision")
        if not isinstance(revision, int) or revision < 0:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Decision expected Feature revision is required")
        return feature_id, revision

    @staticmethod
    def _deny_raw_event_authority(values: dict[str, Any]) -> None:
        forbidden = {"event", "feature_event", "changes", "feature_event_changes"} & set(values)
        if forbidden:
            raise FeatureEventGatewayError(
                "UNAUTHORIZED",
                f"raw Feature Event authority is not accepted from Decision caller: {sorted(forbidden)}",
            )

    def read_feature(self, *args, **kwargs) -> dict[str, Any]:
        # Protocol compatibility: accepted Coordinator uses trusted named fields;
        # a single positional Feature id is tolerated for local integration, but
        # no positional repository/ref authority is accepted.
        values = dict(kwargs)
        if args:
            if len(args) != 1 or "feature_id" in values:
                raise FeatureEventGatewayError("INVALID_REQUEST", "unsupported positional Feature truth arguments")
            values["feature_id"] = args[0]
        feature_id = str(values.get("feature_id") or "")
        if not feature_id:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Decision Feature id is required")
        if "target_ref" in values and str(values["target_ref"]) != self.event_gateway.configuration.target_ref(feature_id):
            raise FeatureEventGatewayError("UNAUTHORIZED", "Feature truth target ref differs from trusted binding")
        repository = values.get("repository") or values.get("target_repository")
        if repository is not None and str(repository).lower() != self.event_gateway.configuration.repository.lower():
            raise FeatureEventGatewayError("UNAUTHORIZED", "Feature truth repository differs from trusted binding")
        return self.event_gateway.read_feature(feature_id=feature_id)

    def persist_decision_response(self, *args, **kwargs):
        if args:
            raise FeatureEventGatewayError(
                "INVALID_REQUEST",
                "Decision response persistence requires named trusted binding fields",
            )
        values = dict(kwargs)
        self._deny_raw_event_authority(values)
        feature_id, expected_revision = self._bound_target(values)
        current_feature = self.event_gateway.read_feature(feature_id=feature_id)
        current_revision = current_feature.get("revision")
        if current_revision != expected_revision:
            raise FeatureEventGatewayError(
                "STALE_REVISION",
                f"Decision Feature revision {current_revision} != expected {expected_revision}",
            )

        # Copy the trusted Coordinator fields before handing them to the planner;
        # the planner cannot mutate the caller's object and its output is checked
        # against the same immutable Feature/revision binding.
        trusted_inputs = dict(values)
        plan = self.planner.plan(
            trusted_inputs=trusted_inputs,
            current_feature=dict(current_feature),
        )
        if not isinstance(plan, DecisionEventPlan):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Decision Event planner returned invalid plan")
        if plan.feature_id != feature_id or plan.expected_revision != expected_revision:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Decision Event plan changed Feature/revision binding")
        return self.event_gateway.persist_exact_event(
            feature_id=feature_id,
            expected_revision=expected_revision,
            event=dict(plan.event),
        )
