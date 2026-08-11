#!/usr/bin/env python3
"""Trusted code-level registry for bounded Decision outcome translators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from operator_decision_feature_gateway_adapter import DecisionEventPlan
from operator_github_feature_event_gateway import FeatureEventGatewayError


class TypedDecisionEventPlanner(Protocol):
    decision_type: str
    authorized_action: str

    def plan(self, *, trusted_inputs: dict[str, Any], current_feature: dict[str, Any]) -> DecisionEventPlan:
        ...


@dataclass(frozen=True)
class DecisionEventPlannerRegistry:
    planners: tuple[TypedDecisionEventPlanner, ...]

    def __post_init__(self):
        keys = [
            (
                str(getattr(planner, "decision_type", "")),
                str(getattr(planner, "authorized_action", "")),
            )
            for planner in self.planners
        ]
        if not keys or any(not decision_type or not action for decision_type, action in keys):
            raise ValueError("trusted Decision planner registry requires exact type/action keys")
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate trusted Decision planner type/action key")

    def require(self, decision_type: str, authorized_action: str) -> TypedDecisionEventPlanner:
        key = (str(decision_type), str(authorized_action))
        matches = [
            planner
            for planner in self.planners
            if (planner.decision_type, planner.authorized_action) == key
        ]
        if len(matches) != 1:
            raise FeatureEventGatewayError(
                "INVALID_REQUEST",
                "Decision outcome has no trusted Feature Event translator: "
                f"decision_type={decision_type!r}, authorized_action={authorized_action!r}",
            )
        return matches[0]


class RegistryDecisionEventPlanner:
    """Dispatch by exact durable Decision type + protected-policy action only."""

    def __init__(self, registry: DecisionEventPlannerRegistry):
        self.registry = registry

    def plan(self, *, trusted_inputs: dict[str, Any], current_feature: dict[str, Any]) -> DecisionEventPlan:
        decision_type = str(trusted_inputs.get("decision_type") or "")
        authorized_action = str(trusted_inputs.get("authorized_action") or "")
        if not decision_type or not authorized_action:
            raise FeatureEventGatewayError(
                "INVALID_REQUEST",
                "durable Decision type and authorized action are required",
            )
        planner = self.registry.require(decision_type, authorized_action)
        return planner.plan(
            trusted_inputs=dict(trusted_inputs),
            current_feature=dict(current_feature),
        )
