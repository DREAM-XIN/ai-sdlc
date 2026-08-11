#!/usr/bin/env python3
"""Bind the new Event bridge to the repository's actual DecisionCoordinator call shape."""
from __future__ import annotations

import ast
import inspect

from operator_decision_backends import DecisionCoordinator, DecisionFeatureTruthGateway
from operator_decision_feature_gateway_adapter import BoundedDecisionFeatureGatewayAdapter

TARGET_METHODS = {"read_feature", "persist_decision_response"}
FORBIDDEN_RAW_AUTHORITY = {"event", "feature_event", "changes", "feature_event_changes"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def coordinator_gateway_calls():
    source = inspect.getsource(DecisionCoordinator)
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in TARGET_METHODS:
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Attribute) or owner.attr != "feature_gateway":
            continue
        require(not node.args, f"DecisionCoordinator.{node.func.attr} uses positional gateway authority")
        require(all(keyword.arg is not None for keyword in node.keywords), f"DecisionCoordinator.{node.func.attr} uses **kwargs gateway authority")
        names = tuple(keyword.arg for keyword in node.keywords)
        found.setdefault(node.func.attr, []).append(names)
    return found


def protocol_methods():
    names = {
        name
        for name, value in vars(DecisionFeatureTruthGateway).items()
        if callable(value) and not name.startswith("_")
    }
    return names


def main():
    protocol = protocol_methods()
    require(TARGET_METHODS.issubset(protocol), f"DecisionFeatureTruthGateway contract drifted: {sorted(protocol)}")

    calls = coordinator_gateway_calls()
    require("read_feature" in calls, "DecisionCoordinator no longer calls feature_gateway.read_feature")
    require("persist_decision_response" in calls, "DecisionCoordinator no longer calls feature_gateway.persist_decision_response")

    adapter_read = inspect.signature(BoundedDecisionFeatureGatewayAdapter.read_feature)
    adapter_persist = inspect.signature(BoundedDecisionFeatureGatewayAdapter.persist_decision_response)
    for method_name, invocations in calls.items():
        signature = adapter_read if method_name == "read_feature" else adapter_persist
        for keyword_names in invocations:
            fake = {name: object() for name in keyword_names}
            signature.bind(object(), **fake)
            if method_name == "persist_decision_response":
                require(
                    not (set(keyword_names) & FORBIDDEN_RAW_AUTHORITY),
                    f"DecisionCoordinator passes raw Feature Event authority into gateway: {keyword_names}",
                )

    print("Decision Feature gateway protocol binding validation passed")
    for method_name in sorted(calls):
        print(f"- {method_name}: named call shapes {calls[method_name]}")
    print("- raw Feature Event/changes authority is not passed by DecisionCoordinator")
    print("- bounded adapter signature accepts the repository's current named call contract")


if __name__ == "__main__":
    main()
