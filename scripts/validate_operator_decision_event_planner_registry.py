#!/usr/bin/env python3
"""Validate trusted Decision type routing has no fallback or ambiguity."""
from __future__ import annotations

from dataclasses import dataclass

from operator_decision_event_planner_registry import (
    DecisionEventPlannerRegistry,
    RegistryDecisionEventPlanner,
)
from operator_decision_feature_gateway_adapter import DecisionEventPlan
from operator_github_feature_event_gateway import FeatureEventGatewayError

FEATURE = "F-PLANNER-REGISTRY-FI"
REV = 4


@dataclass
class FixturePlanner:
    decision_type: str

    def __post_init__(self):
        self.calls = []

    def plan(self, *, trusted_inputs, current_feature):
        self.calls.append((dict(trusted_inputs), dict(current_feature)))
        return DecisionEventPlan(
            feature_id=FEATURE,
            expected_revision=REV,
            event={"id": f"EVT-{self.decision_type}-FI"},
        )


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    acceptance = FixturePlanner("ACCEPTANCE")
    authorization = FixturePlanner("AUTHORIZATION")
    registry = DecisionEventPlannerRegistry((acceptance, authorization))
    planner = RegistryDecisionEventPlanner(registry)

    plan = planner.plan(
        trusted_inputs={"decision_type": "ACCEPTANCE", "decision_id": "dec-1"},
        current_feature={"revision": REV},
    )
    require(plan.event["id"] == "EVT-ACCEPTANCE-FI", plan)
    require(len(acceptance.calls) == 1 and not authorization.calls, "Decision type routed to wrong translator")

    for unknown in ("", "CLARIFICATION", "acceptance"):
        try:
            planner.plan(
                trusted_inputs={"decision_type": unknown, "decision_id": "dec-x"},
                current_feature={"revision": REV},
            )
            raise AssertionError(f"unregistered Decision type unexpectedly used fallback: {unknown!r}")
        except FeatureEventGatewayError as exc:
            require(exc.code == "INVALID_REQUEST", (unknown, exc.code))

    try:
        DecisionEventPlannerRegistry((FixturePlanner("ACCEPTANCE"), FixturePlanner("ACCEPTANCE")))
        raise AssertionError("duplicate Decision planner type unexpectedly accepted")
    except ValueError:
        pass

    try:
        DecisionEventPlannerRegistry(())
        raise AssertionError("empty Decision planner registry unexpectedly accepted")
    except ValueError:
        pass

    print("Decision Event planner registry validation passed")
    print("- exact code-level Decision type routing")
    print("- unknown/case-mismatched type: INVALID_REQUEST")
    print("- duplicate/empty registry: rejected")
    print("- no fallback translator")


if __name__ == "__main__":
    main()
