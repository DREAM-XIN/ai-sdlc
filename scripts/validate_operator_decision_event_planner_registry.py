#!/usr/bin/env python3
"""Validate trusted Decision outcome routing has no fallback or ambiguity."""
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
    authorized_action: str

    def __post_init__(self):
        self.calls = []

    def plan(self, *, trusted_inputs, current_feature):
        self.calls.append((dict(trusted_inputs), dict(current_feature)))
        safe_action = self.authorized_action.replace(".", "-")
        return DecisionEventPlan(
            feature_id=FEATURE,
            expected_revision=REV,
            event={"id": f"EVT-{self.decision_type}-{safe_action}-FI"},
        )


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_invalid(planner, decision_type, authorized_action):
    try:
        planner.plan(
            trusted_inputs={
                "decision_type": decision_type,
                "authorized_action": authorized_action,
                "decision_id": "dec-x",
            },
            current_feature={"revision": REV},
        )
        raise AssertionError(
            "unregistered Decision outcome unexpectedly used fallback: "
            f"{decision_type!r}/{authorized_action!r}"
        )
    except FeatureEventGatewayError as exc:
        require(exc.code == "INVALID_REQUEST", (decision_type, authorized_action, exc.code))


def main():
    approve = FixturePlanner("NEEDS_AUTHORIZATION", "resume-exact-operation")
    deny = FixturePlanner("NEEDS_AUTHORIZATION", "remain-blocked")
    acceptance = FixturePlanner("NEEDS_ACCEPTANCE", "request-product-acceptance")
    registry = DecisionEventPlannerRegistry((approve, deny, acceptance))
    planner = RegistryDecisionEventPlanner(registry)

    plan = planner.plan(
        trusted_inputs={
            "decision_type": "NEEDS_AUTHORIZATION",
            "authorized_action": "resume-exact-operation",
            "decision_id": "dec-1",
        },
        current_feature={"revision": REV},
    )
    require(plan.event["id"] == "EVT-NEEDS_AUTHORIZATION-resume-exact-operation-FI", plan)
    require(len(approve.calls) == 1 and not deny.calls and not acceptance.calls, "Decision outcome routed to wrong translator")

    # Same Decision type but another protected-policy action must route to a
    # distinct planner, not share implicit branching authority.
    planner.plan(
        trusted_inputs={
            "decision_type": "NEEDS_AUTHORIZATION",
            "authorized_action": "remain-blocked",
            "decision_id": "dec-2",
        },
        current_feature={"revision": REV},
    )
    require(len(deny.calls) == 1, "same-type alternate action did not use exact planner")

    for decision_type, action in (
        ("", "resume-exact-operation"),
        ("NEEDS_AUTHORIZATION", ""),
        ("NEEDS_AUTHORIZATION", "unknown-action"),
        ("needs_authorization", "resume-exact-operation"),
        ("NEEDS_ACCEPTANCE", "resume-exact-operation"),
    ):
        assert_invalid(planner, decision_type, action)

    try:
        DecisionEventPlannerRegistry(
            (
                FixturePlanner("NEEDS_AUTHORIZATION", "resume-exact-operation"),
                FixturePlanner("NEEDS_AUTHORIZATION", "resume-exact-operation"),
            )
        )
        raise AssertionError("duplicate Decision type/action planner unexpectedly accepted")
    except ValueError:
        pass

    try:
        DecisionEventPlannerRegistry(())
        raise AssertionError("empty Decision planner registry unexpectedly accepted")
    except ValueError:
        pass

    print("Decision Event planner registry validation passed")
    print("- exact code-level Decision type + authorized-action routing")
    print("- different choices/actions of one Decision type cannot share implicit fallback")
    print("- unknown/case-mismatched combinations: INVALID_REQUEST")
    print("- duplicate/empty registry: rejected")
    print("- no fallback translator")


if __name__ == "__main__":
    main()
