#!/usr/bin/env python3
"""Validate Decision callers cannot acquire arbitrary Feature Event authority."""
from __future__ import annotations

from dataclasses import dataclass

from operator_decision_feature_gateway_adapter import (
    BoundedDecisionFeatureGatewayAdapter,
    DecisionEventPlan,
)
from operator_github_feature_event_gateway import FeatureEventGatewayError

REPO = "DREAM-XIN/ai-sdlc"
FEATURE = "F-DECISION-BRIDGE-FI"
REF = "feature/F-DECISION-BRIDGE-FI"
REV = 9


class FakeConfiguration:
    repository = REPO

    def target_ref(self, feature_id):
        if feature_id != FEATURE:
            raise FeatureEventGatewayError("UNAUTHORIZED", "outside fixture Feature")
        return REF


class FakeEventGateway:
    def __init__(self):
        self.configuration = FakeConfiguration()
        self.persist_calls = []
        self.feature = {"feature": {"id": FEATURE}, "revision": REV, "workflow": {"current_stage": "acceptance"}}

    def read_feature(self, *, feature_id):
        if feature_id != FEATURE:
            raise FeatureEventGatewayError("UNAUTHORIZED", "outside fixture Feature")
        return dict(self.feature)

    def persist_exact_event(self, *, feature_id, expected_revision, event):
        self.persist_calls.append((feature_id, expected_revision, dict(event)))
        return {"state": "APPLIED", "event_id": event["id"], "result_revision": expected_revision + 1}


@dataclass
class RecordingPlanner:
    drift_feature: bool = False
    drift_revision: bool = False

    def __post_init__(self):
        self.calls = []

    def plan(self, *, trusted_inputs, current_feature):
        self.calls.append((dict(trusted_inputs), dict(current_feature)))
        feature_id = "F-OTHER" if self.drift_feature else FEATURE
        revision = REV + 1 if self.drift_revision else REV
        return DecisionEventPlan(
            feature_id=feature_id,
            expected_revision=revision,
            event={
                "version": "0.1.0",
                "id": "EVT-DECISION-BRIDGE-FI",
                "feature_id": feature_id,
                "expected_revision": revision,
                "changes": [{"kind": "fixture"}],
            },
        )


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def trusted_kwargs():
    return {
        "repository": REPO,
        "feature_id": FEATURE,
        "target_ref": REF,
        "expected_revision": REV,
        "decision_id": "dec-fi-1",
        "decision_type": "ACCEPTANCE",
        "choice": "ACCEPT",
        "operation_id": "op-fi-1",
        "operation_generation": 3,
        "responder_identity": "human-fixture",
        "occurred_at": "2026-08-11T06:00:00Z",
    }


def main():
    gateway = FakeEventGateway()
    planner = RecordingPlanner()
    adapter = BoundedDecisionFeatureGatewayAdapter(event_gateway=gateway, planner=planner)

    feature = adapter.read_feature(repository=REPO, feature_id=FEATURE, target_ref=REF)
    require(feature["revision"] == REV, feature)

    result = adapter.persist_decision_response(**trusted_kwargs())
    require(result["state"] == "APPLIED", result)
    require(len(planner.calls) == 1, planner.calls)
    require(len(gateway.persist_calls) == 1, gateway.persist_calls)
    planned_inputs = planner.calls[0][0]
    require(planned_inputs["decision_id"] == "dec-fi-1", planned_inputs)
    require(planned_inputs["operation_generation"] == 3, planned_inputs)

    for forbidden_key in ("event", "feature_event", "changes", "feature_event_changes"):
        values = trusted_kwargs()
        values[forbidden_key] = {"attacker": True}
        before_plan = len(planner.calls)
        before_persist = len(gateway.persist_calls)
        try:
            adapter.persist_decision_response(**values)
            raise AssertionError(f"raw Feature Event authority {forbidden_key} unexpectedly accepted")
        except FeatureEventGatewayError as exc:
            require(exc.code == "UNAUTHORIZED", exc)
        require(len(planner.calls) == before_plan, "raw Event reached trusted planner")
        require(len(gateway.persist_calls) == before_persist, "raw Event reached persistence")

    for mutation, code in (
        ({"repository": "DREAM-XIN/other"}, "UNAUTHORIZED"),
        ({"target_ref": "feature/other"}, "UNAUTHORIZED"),
        ({"expected_revision": REV - 1}, "STALE_REVISION"),
    ):
        values = trusted_kwargs()
        values.update(mutation)
        try:
            adapter.persist_decision_response(**values)
            raise AssertionError(f"invalid binding unexpectedly accepted: {mutation}")
        except FeatureEventGatewayError as exc:
            require(exc.code == code, (mutation, exc.code))

    for drift in (RecordingPlanner(drift_feature=True), RecordingPlanner(drift_revision=True)):
        bounded = BoundedDecisionFeatureGatewayAdapter(event_gateway=FakeEventGateway(), planner=drift)
        try:
            bounded.persist_decision_response(**trusted_kwargs())
            raise AssertionError("trusted planner binding drift unexpectedly reached Event transport")
        except FeatureEventGatewayError as exc:
            require(exc.code == "INTERNAL_FAILURE", exc)

    print("Bounded Decision Feature gateway adapter validation passed")
    print("- Decision binding fields reach only server-owned planner")
    print("- raw event/changes input: UNAUTHORIZED before planner/persist")
    print("- repository/ref/revision mismatch: fail closed")
    print("- planner cannot change bound Feature/revision")


if __name__ == "__main__":
    main()
