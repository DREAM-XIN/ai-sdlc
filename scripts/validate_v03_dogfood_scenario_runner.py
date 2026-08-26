#!/usr/bin/env python3
"""Deterministic anti-overclaim validation for the real dogfood scenario runner."""
from __future__ import annotations

import json
from types import SimpleNamespace

import v03_dogfood_scenario_runner as runner
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_openai_host import V03DogfoodResponsesTrace


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


class FakeHost:
    def __init__(self, operation_id="op-dogfood-1", status="WAITING_EXTERNAL", duplicate=False):
        self.operation_id = operation_id
        self.status = status
        self.duplicate = duplicate
        self.instructions = []

    def run(self, *, scenario_instruction):
        self.instructions.append(scenario_instruction)
        output = {
            "type": "function_call_output",
            "call_id": "call-start",
            "output": json.dumps({"ok": True, "result": {"operation_id": self.operation_id, "generation": 0, "status": self.status}}),
        }
        outputs = (output, output) if self.duplicate else (output,)
        return V03DogfoodResponsesTrace(
            response_ids=("resp_1",),
            function_call_ids=("call-start",),
            function_outputs=outputs,
            terminal_response={"id": "resp_1", "status": "completed", "output": []},
        )


class FakeRecoveryHost:
    def __init__(self, operation_id="op-dogfood-1", include_all=True):
        self.operation_id = operation_id
        self.include_all = include_all
        self.instructions = []

    def run(self, *, scenario_instruction):
        self.instructions.append(scenario_instruction)
        result = {
            "operations": [{"operation_id": self.operation_id, "status": "NEEDS_USER"}],
            "decisions": [{"operation_id": self.operation_id, "decision_id": "decision-1", "status": "PENDING"}],
            "notifications": [{"operation_id": self.operation_id, "notification_id": "notification-1", "status": "PENDING"}],
        }
        if not self.include_all:
            result["notifications"] = []
        output = {
            "type": "function_call_output",
            "call_id": "call-inbox",
            "output": json.dumps({"ok": True, "result": result}),
        }
        return V03DogfoodResponsesTrace(
            response_ids=("resp_recovery",),
            function_call_ids=("call-inbox",),
            function_outputs=(output,),
            terminal_response={"id": "resp_recovery", "status": "completed", "output": []},
        )


def fake_preflight(scenario):
    slot = require_slot(scenario)
    gateway = SimpleNamespace(read_feature=lambda **kwargs: {"revision": 1})
    composition = SimpleNamespace(feature_event_gateway=gateway, collector=SimpleNamespace(handle=lambda **kwargs: None))
    return SimpleNamespace(
        slot=slot,
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc"),
        composition=composition,
    )


def run_case(scenario, statuses, roles, *, recovery=True):
    preflight = fake_preflight(scenario)
    host = FakeHost(status=statuses[0])
    recovery_host = FakeRecoveryHost() if scenario == "session_recovery" and recovery else None
    old_projection = runner._projection
    old_rows = runner._dispatch_rows
    old_collect = runner._collect_next
    old_receipts = runner._launch_receipts
    old_events = runner._events
    state = {"index": 0, "consumed": 0}
    rows = [
        {"_dogfood_role": role, "payload": {"external_dispatch_key": f"key-{index}"}}
        for index, role in enumerate(roles, start=1)
    ]
    try:
        runner._projection = lambda p, op: {"status": statuses[state["index"]]}
        def collect(p, op, consumed):
            expect(consumed == state["consumed"], "runner consumed cursor drifted")
            state["consumed"] += 1
            if state["index"] + 1 < len(statuses):
                state["index"] += 1
            return state["consumed"]
        runner._collect_next = collect
        runner._dispatch_rows = lambda p, op: rows[: state["consumed"] or 1]
        runner._launch_receipts = lambda p, op: (tuple(range(1001, 1001 + len(roles))), str(1000 + len(roles)))
        runner._events = lambda p, op: [{"event_type": "operation.started", "sequence": 1}]
        result = runner.run_scenario(preflight=preflight, host=host, recovery_host=recovery_host)
    finally:
        runner._projection = old_projection
        runner._dispatch_rows = old_rows
        runner._collect_next = old_collect
        runner._launch_receipts = old_receipts
        runner._events = old_events
    expect(result.release_eligible is False, "raw runner observation must not self-authorize release PASS")
    expect(result.dispatch_roles == tuple(roles), "runner role sequence")
    expect("Start exactly one Operation" in host.instructions[0], "runner instruction must bound operation.start")
    return result


def main():
    happy = run_case(
        "happy_path",
        ["WAITING_EXTERNAL", "WAITING_EXTERNAL", "WAITING_EXTERNAL", "DONE"],
        ["developer", "reviewer", "qa"],
    )
    expect(happy.final_status == "DONE", "happy path final state")

    remediation = run_case(
        "review_remediation",
        ["WAITING_EXTERNAL", "WAITING_EXTERNAL", "WAITING_EXTERNAL", "WAITING_EXTERNAL", "WAITING_EXTERNAL", "DONE"],
        ["developer", "reviewer", "developer", "reviewer", "qa"],
    )
    expect(remediation.dispatch_roles.count("reviewer") == 2, "remediation must include re-review")

    session = run_case(
        "session_recovery",
        ["WAITING_EXTERNAL", "NEEDS_USER"],
        ["developer"],
    )
    expect(session.final_status == "NEEDS_USER", "session recovery final state")
    expect(session.new_session_discovery_observed is True, "fresh session discovery must be observed")
    expect(session.recovery_response_ids == ("resp_recovery",), "fresh session must use distinct Responses trace")

    try:
        run_case("session_recovery", ["WAITING_EXTERNAL", "NEEDS_USER"], ["developer"], recovery=False)
    except runner.V03DogfoodScenarioRunnerError:
        pass
    else:
        raise AssertionError("session recovery without fresh host was accepted")

    broken = FakeRecoveryHost(include_all=False).run(scenario_instruction="x")
    try:
        runner._verify_fresh_session_discovery(broken, operation_id="op-dogfood-1")
    except runner.V03DogfoodScenarioRunnerError:
        pass
    else:
        raise AssertionError("fresh session missing Notification was accepted")

    trace = FakeHost(duplicate=True).run(scenario_instruction="x")
    try:
        runner._operation_start(trace)
    except runner.V03DogfoodScenarioRunnerError:
        pass
    else:
        raise AssertionError("duplicate operation.start result was accepted")

    try:
        run_case("happy_path", ["WAITING_EXTERNAL", "WAITING_EXTERNAL", "DONE"], ["developer", "qa"])
    except runner.V03DogfoodScenarioRunnerError:
        pass
    else:
        raise AssertionError("happy path without independent Reviewer was accepted")

    # Closed dispatch-claim schema has no role. Role is reconstructed from the
    # immediately preceding trusted loop.step.selected fact.
    old_events = runner._events
    try:
        runner._events = lambda p, op: [
            {"sequence": 1, "event_type": "loop.step.selected", "payload": {"step": "CODE_REREVIEW"}},
            {"sequence": 2, "event_type": "dispatch.claimed", "payload": {"external_dispatch_key": "dispatch-" + "a" * 40}},
        ]
        rows = runner._dispatch_rows(SimpleNamespace(), "op")
        expect(runner._dispatch_role(rows[0]) == "reviewer", "role reconstruction from selected step")
    finally:
        runner._events = old_events

    print("v0.3 dogfood scenario runner validation passed")
    print("- roles derive from durable selected-step sequence, not dispatch-claim fields")
    print("- frozen happy/remediation dispatch sequences are exact")
    print("- session recovery uses a distinct fresh Responses session and inbox discovery")
    print("- raw runner observations remain non-release-eligible")


if __name__ == "__main__":
    main()
