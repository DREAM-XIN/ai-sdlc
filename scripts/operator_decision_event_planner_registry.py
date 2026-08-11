#!/usr/bin/env python3
"""Trusted code-level registry for bounded Decision outcome translators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from operator_decision_feature_gateway_adapter import DecisionEventPlan
from operator_github_feature_event_gateway import FeatureEventGatewayError


class TypedDecisionEventPlanner(Protocol):
    decision_type: str

    def plan(self, *, trusted_inputs: dict[str, Any], current_feature: dict[str, Any]) -> DecisionEventPlan:
        ...


@dataclass(frozen=True)
class DecisionEventPlannerRegistry:
    planners: tuple[TypedDecisionEventPlanner, ...]

    def __post_init__(self):
        ids = [str(getattr(planner, "decision_type", "")) for planner in self.planners]
        if not ids or any(not value for value in ids):
            raise ValueError("trusted Decision planner registry requires named planners")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate trusted Decision planner type")

    def require(self, decision_type: str) -> TypedDecisionEventPlanner:
        matches = [planner for planner in self.planners if planner.decision_type == decision_type]
        if len(matches) != 1:
            raise FeatureEventGatewayError(
                "INVALID_REQUEST",
                f"Decision type has no trusted Feature Event translator: {decision_type}",
            )
        return matches[0]


class RegistryDecisionEventPlanner:
    """Dispatch only by exact durable Decision type; no fallback translator exists."""

    def __init__(self, registry: DecisionEventPlannerRegistry):
        self.registry = registry

    def plan(self, *, trusted_inputs: dict[str, Any], current_feature: dict[str, Any]) -> DecisionEventPlan:
        decision_type = str(trusted_inputs.get("decision_type") or "")
        if not decision_type:
            raise FeatureEventGatewayError("INVALID_REQUEST", "durable Decision type is required")
        planner = self.registry.require(decision_type)
        return planner.plan(
            trusted_inputs=dict(trusted_inputs),
            current_feature=dict(current_feature),
        )
