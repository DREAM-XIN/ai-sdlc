#!/usr/bin/env python3
"""Bind trusted Decision Feature truth to the repository's accepted backend contract."""
from __future__ import annotations

import ast
import inspect

from operator_decision_backends import DecisionRespondBackend
from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway
from operator_vertical_controller import FeatureTruthGateway

FORBIDDEN_EVENT_METHODS = {
    "persist_decision_response",
    "persist_exact_event",
    "submit_event",
    "persist_feature_event",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def backend_gateway_calls():
    source = inspect.getsource(DecisionRespondBackend)
    tree = ast.parse(source)
    reads = []
    forbidden = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Attribute) or owner.attr != "feature_gateway":
            continue
        if node.func.attr == "read_feature":
            require(not node.args, "DecisionRespondBackend uses positional Feature truth authority")
            require(all(keyword.arg is not None for keyword in node.keywords), "DecisionRespondBackend uses **kwargs Feature truth authority")
            reads.append(tuple(keyword.arg for keyword in node.keywords))
        if node.func.attr in FORBIDDEN_EVENT_METHODS:
            forbidden.append(node.func.attr)
    return reads, forbidden


def protocol_methods():
    return {
        name
        for name, value in vars(FeatureTruthGateway).items()
        if callable(value) and not name.startswith("_")
    }


def main():
    protocol = protocol_methods()
    require("read_feature" in protocol, f"FeatureTruthGateway contract drifted: {sorted(protocol)}")

    reads, forbidden = backend_gateway_calls()
    require(reads, "DecisionRespondBackend no longer reads current Feature truth")
    require(all(names == ("operation_id",) for names in reads), f"Decision Feature truth call shape drifted: {reads}")
    require(not forbidden, f"decision.respond directly invokes Feature Event persistence: {forbidden}")

    adapter_signature = inspect.signature(DurableDecisionFeatureTruthGateway.read_feature)
    adapter_signature.bind(object(), operation_id="op-fixture")
    params = set(adapter_signature.parameters)
    require(params == {"self", "operation_id"}, f"trusted Feature truth adapter accepts extra caller authority: {sorted(params)}")

    # Accepted design invariant: resolving a Decision records a Store Decision
    # fact only. Feature Event writes belong to a later bounded authorization-
    # consumption/action path, not decision.respond itself.
    backend_source = inspect.getsource(DecisionRespondBackend)
    require("plan_decision_response" in backend_source, "DecisionRespondBackend no longer uses accepted Store response planner")

    print("Decision Feature gateway protocol binding validation passed")
    print(f"- DecisionRespondBackend read_feature call shapes: {reads}")
    print("- trusted adapter accepts only operation_id")
    print("- decision.respond has no direct Feature Event persistence call")
    print("- response path remains Store plan_decision_response semantics")


if __name__ == "__main__":
    main()
