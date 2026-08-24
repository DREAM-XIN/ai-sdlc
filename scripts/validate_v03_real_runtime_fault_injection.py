#!/usr/bin/env python3
"""Validate the release-only lost-ACK crash injection boundary."""
from __future__ import annotations

import ast
import inspect
import textwrap

from operator_vertical_executor import TrustedVerticalExecutor
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from v03_real_runtime_fault_injection import (
    InjectedRunnerCrash,
    LostAckCrashAfterLaunchDispatchGateway,
)

EXTERNAL_KEY = "dispatch-fi-exact-key"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Delegate:
    def __init__(self, *, launch_state="LAUNCHED", receipt_id="run-1"):
        self.launch_state = launch_state
        self.receipt_id = receipt_id
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        self.launch_calls.append(dict(dispatch))
        return {"lookup_state": self.launch_state, "receipt_id": self.receipt_id}

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(external_dispatch_key)
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}


def _tree(function):
    return ast.parse(textwrap.dedent(inspect.getsource(function)))


def _attribute_calls(function, owner, method):
    matches = []
    for node in ast.walk(_tree(function)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != method:
            continue
        value = node.func.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == owner
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
        ):
            matches.append(node)
    return matches


def validate_fault_gateway():
    require(issubclass(InjectedRunnerCrash, BaseException), "injected crash is not process-level")
    require(not issubclass(InjectedRunnerCrash, Exception), "injected crash would be swallowed by executor Exception recovery")

    delegate = Delegate()
    gateway = LostAckCrashAfterLaunchDispatchGateway(
        delegate=delegate,
        expected_external_dispatch_key=EXTERNAL_KEY,
    )
    try:
        gateway.launch(dispatch={"external_dispatch_key": EXTERNAL_KEY, "dispatch_id": "d-1"})
    except InjectedRunnerCrash as exc:
        require(exc.external_dispatch_key == EXTERNAL_KEY, "crash signal lost exact external key")
        require(not hasattr(exc, "receipt_id"), "crash signal retained the deliberately lost runtime receipt")
    else:
        raise AssertionError("trusted LAUNCHED receipt did not trigger process-level lost-ACK crash")
    require(gateway.injected is True, "fault gateway did not arm one-shot post-launch fence")
    require(len(delegate.launch_calls) == 1, "fault gateway launched external effect more than once")
    require(delegate.lookup_calls == [], "same process looked up receipt before the injected crash escaped")

    try:
        gateway.launch(dispatch={"external_dispatch_key": EXTERNAL_KEY, "dispatch_id": "d-2"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("same fault gateway allowed a second launch after crash injection")
    require(len(delegate.launch_calls) == 1, "post-crash retry reached external launch")

    lookup = gateway.lookup(external_dispatch_key=EXTERNAL_KEY)
    require(lookup == {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}, "lookup delegation changed trusted receipt")
    require(delegate.lookup_calls == [EXTERNAL_KEY], "lookup did not preserve exact external dispatch identity")

    wrong = Delegate()
    wrong_gateway = LostAckCrashAfterLaunchDispatchGateway(
        delegate=wrong,
        expected_external_dispatch_key=EXTERNAL_KEY,
    )
    try:
        wrong_gateway.launch(dispatch={"external_dispatch_key": "dispatch-foreign"})
    except ValueError:
        pass
    else:
        raise AssertionError("cross-key fault injection reached trusted external gateway")
    require(wrong.launch_calls == [], "cross-key fault injection launched an external effect")

    unknown = Delegate(launch_state="UNKNOWN", receipt_id=None)
    unknown_gateway = LostAckCrashAfterLaunchDispatchGateway(
        delegate=unknown,
        expected_external_dispatch_key=EXTERNAL_KEY,
    )
    result = unknown_gateway.launch(dispatch={"external_dispatch_key": EXTERNAL_KEY})
    require(result["lookup_state"] == "UNKNOWN", "UNKNOWN launch observation was changed by injector")
    require(unknown_gateway.injected is False, "injector claimed crash-after-success without trusted LAUNCHED receipt")


def validate_executor_crash_window():
    tree = _tree(TrustedVerticalExecutor._dispatch)
    launch_try = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        launch_calls = [
            child
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "launch"
        ]
        if launch_calls:
            launch_try.append(node)
    require(len(launch_try) == 1, "Vertical executor launch recovery shape drifted")
    handlers = launch_try[0].handlers
    require(len(handlers) == 1, "Vertical executor launch has unexpected exception handlers")
    handler_type = handlers[0].type
    require(isinstance(handler_type, ast.Name) and handler_type.id == "Exception", "Vertical executor no longer catches only ordinary launch Exceptions")

    source = textwrap.dedent(inspect.getsource(TrustedVerticalExecutor._dispatch))
    require(
        source.index("plan_authorize_launch") < source.index("dispatch_gateway.launch"),
        "external launch occurs before durable launch authorization",
    )
    require(
        source.index("dispatch_gateway.launch") < source.index("plan_launch_lookup"),
        "local launch lookup fact is recorded before the external launch call",
    )


def validate_restart_lookup_only():
    require(len(_attribute_calls(TrustedRecoveringVerticalExecutor._reconcile_launch, "dispatch_gateway", "lookup")) == 1, "restart launch reconciliation must use one trusted lookup path")
    require(len(_attribute_calls(TrustedRecoveringVerticalExecutor._reconcile_launch, "dispatch_gateway", "launch")) == 0, "restart launch reconciliation must not re-launch an authorized unresolved effect")
    source = textwrap.dedent(inspect.getsource(TrustedRecoveringVerticalExecutor._reconcile_launch))
    require("dispatch.launch.authorized" in source, "restart reconciliation no longer scans durable launch authorization")
    require("dispatch.launch.lookup-recorded" in source, "restart reconciliation no longer fences already-looked-up launch state")
    require("plan_launch_lookup" in source, "restart reconciliation does not durably record trusted lookup result")


def main():
    validate_fault_gateway()
    validate_executor_crash_window()
    validate_restart_lookup_only()
    print("v0.3 real-runtime lost-ACK crash injection validation passed")
    print("- injected crash is outside Exception and occurs only after exact trusted LAUNCHED receipt")
    print("- same injector instance cannot re-launch after the crash window")
    print("- cross-key injection fails before external launch; UNKNOWN does not masquerade as successful launch")
    print("- accepted executor durably authorizes launch before gateway call and records lookup only after it returns")
    print("- accepted restart reconciliation performs same-key lookup only and never launches the unresolved authorized effect")


if __name__ == "__main__":
    main()
