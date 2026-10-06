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
        names = ("aisdlc_v1_operation_start", "aisdlc_v1_operation_start") if self.duplicate else ("aisdlc_v1_operation_start",)
        return V03DogfoodResponsesTrace(
            response_ids=("resp_1",),
            function_call_ids=("call-start",),
            function_call_names=names,
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
            function_call_names=("aisdlc_v1_operator_inbox",),
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
    old_wait = runner._wait_current_dispatch
    state = {"index": 0, "consumed": 0}
    def request_decision(**kwargs):
        expect(scenario == "session_recovery", "non-session requested Decision")
        expect(kwargs["decision_type"] == "NEEDS_AUTHORIZATION", "wrong Decision type")
        state["index"] = 1
        return {"decision_id": "decision-1", "status": "PENDING"}
    preflight.composition.bundle = SimpleNamespace(
        decision_notification_coordinator=SimpleNamespace(request_decision=request_decision))
    rows = [
        {"_dogfood_role": role, "payload": {"external_dispatch_key": f"key-{index}"}}
        for index, role in enumerate(roles, start=1)
    ]
    try:
        runner._wait_current_dispatch = lambda *args: None
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
        runner._wait_current_dispatch = old_wait
    expect(result.release_eligible is False, "raw runner observation must not self-authorize release PASS")
    expect(result.dispatch_roles == tuple(roles), "runner role sequence")
    expect("Start exactly one Operation" in host.instructions[0], "runner instruction must bound operation.start")
    expect("first tool response must contain exactly one function call: operation.start" in host.instructions[0],
           "runner instruction must prevent DeepSeek from batching reads with operation.start")
    expect("do not place any other tool call beside operation.start" in host.instructions[0],
           "runner instruction must forbid parallel write batches")
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
    expect(session.worker_results_consumed == 0, "session recovery must preserve unfinished callback/lifecycle work")
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

    start_output = {
        "type": "function_call_output",
        "call_id": "call-start",
        "output": json.dumps({"ok": True, "result": {"operation_id": "op-dogfood-1", "generation": 0, "status": "WAITING_EXTERNAL"}}),
    }
    status_output = {
        "type": "function_call_output",
        "call_id": "call-status",
        "output": json.dumps({"ok": True, "result": {"operation_id": "op-dogfood-1", "generation": 0, "status": "WAITING_EXTERNAL"}}),
    }
    start_then_status = V03DogfoodResponsesTrace(
        response_ids=("resp-start", "resp-status"),
        function_call_ids=("call-start", "call-status"),
        function_call_names=("aisdlc_v1_operation_start", "aisdlc_v1_operation_status"),
        function_outputs=(start_output, status_output),
        terminal_response={"id": "resp-status", "status": "completed", "output": []},
    )
    expect(
        runner._operation_start(start_then_status) == ("op-dogfood-1", "WAITING_EXTERNAL"),
        "operation.status result was misclassified as a second operation.start",
    )

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

    # A trusted generation takeover may repeat the same claim for the same
    # external effect. Dogfood must count that as one logical dispatch while
    # retaining the newest generation's claim.
    old_events = runner._events
    try:
        logical_key = "dispatch-" + "b" * 40
        semantic_key = "c" * 64
        runner._events = lambda p, op: [
            {"sequence": 1, "operation_generation": 0, "event_type": "loop.step.selected",
             "payload": {"step": "IMPLEMENTATION_WORK"}},
            {"sequence": 2, "operation_generation": 0, "event_type": "dispatch.claimed",
             "payload": {"external_dispatch_key": logical_key, "semantic_effect_key": semantic_key}},
            {"sequence": 3, "operation_generation": 1, "event_type": "loop.step.selected",
             "payload": {"step": "IMPLEMENTATION_WORK"}},
            {"sequence": 4, "operation_generation": 1, "event_type": "dispatch.claimed",
             "payload": {"external_dispatch_key": logical_key, "semantic_effect_key": semantic_key}},
        ]
        rows = runner._dispatch_rows(SimpleNamespace(), "op")
        expect(len(rows) == 1, "cross-generation replay was misclassified as a second logical dispatch")
        expect(rows[0]["operation_generation"] == 1, "logical replay did not retain newest generation claim")

        runner._events = lambda p, op: [
            {"sequence": 1, "operation_generation": 0, "event_type": "loop.step.selected",
             "payload": {"step": "IMPLEMENTATION_WORK"}},
            {"sequence": 2, "operation_generation": 0, "event_type": "dispatch.claimed",
             "payload": {"external_dispatch_key": logical_key, "semantic_effect_key": semantic_key}},
            {"sequence": 3, "operation_generation": 1, "event_type": "loop.step.selected",
             "payload": {"step": "CODE_REVIEW"}},
            {"sequence": 4, "operation_generation": 1, "event_type": "dispatch.claimed",
             "payload": {"external_dispatch_key": logical_key, "semantic_effect_key": semantic_key}},
        ]
        try:
            runner._dispatch_rows(SimpleNamespace(), "op")
        except runner.V03DogfoodScenarioRunnerError:
            pass
        else:
            raise AssertionError("cross-generation replay with role drift was accepted")

        runner._events = lambda p, op: [
            {"sequence": 1, "operation_generation": 1, "event_type": "loop.step.selected",
             "payload": {"step": "IMPLEMENTATION_WORK"}},
            {"sequence": 2, "operation_generation": 1, "event_type": "dispatch.claimed",
             "payload": {"external_dispatch_key": logical_key, "semantic_effect_key": semantic_key}},
            {"sequence": 3, "operation_generation": 1, "event_type": "loop.step.selected",
             "payload": {"step": "IMPLEMENTATION_WORK"}},
            {"sequence": 4, "operation_generation": 1, "event_type": "dispatch.claimed",
             "payload": {"external_dispatch_key": logical_key, "semantic_effect_key": semantic_key}},
        ]
        try:
            runner._dispatch_rows(SimpleNamespace(), "op")
        except runner.V03DogfoodScenarioRunnerError:
            pass
        else:
            raise AssertionError("same-generation duplicate dispatch claim was accepted")
    finally:
        runner._events = old_events


    good = dict(id=1001, event="workflow_dispatch", head_branch="main", head_sha="a"*40,
                path=".github/workflows/worker.yml", display_title="AI-SDLC gh-aw key",
                run_attempt=1, status="completed", conclusion="success")
    states = [{**good, "status": "queued", "conclusion": None},
              {**good, "status": "in_progress", "conclusion": None}, good]
    waits = []
    result = runner.wait_for_worker_run(read_run=lambda receipt: states.pop(0), receipt="1001",
                                       workflow="worker.yml", installation_sha="a"*40,
                                       external_dispatch_key="key", sleeper=waits.append)
    expect(result == good and waits == [5.0, 5.0], "pending Worker was not observed to completion")
    for changed in ({"id": 1002}, {"head_sha": "b"*40}, {"run_attempt": 2},
                    {"conclusion": "failure"}, {"path": ".github/workflows/other.yml"}):
        try:
            runner.wait_for_worker_run(read_run=lambda receipt: {**good, **changed}, receipt="1001",
                                      workflow="worker.yml", installation_sha="a"*40,
                                      external_dispatch_key="key", sleeper=lambda _: None)
        except runner.V03DogfoodScenarioRunnerError:
            pass
        else:
            raise AssertionError("invalid Worker accepted before callback")
    try:
        runner.wait_for_worker_run(read_run=lambda receipt: {**good, "status": "in_progress", "conclusion": None},
                                  receipt="1001", workflow="worker.yml", installation_sha="a"*40,
                                  external_dispatch_key="key", max_attempts=2, sleeper=lambda _: None)
    except runner.V03DogfoodScenarioRunnerError:
        pass
    else:
        raise AssertionError("unbounded pending Worker accepted")

    print("v0.3 dogfood scenario runner validation passed")
    print("- roles derive from durable selected-step sequence, not dispatch-claim fields")
    print("- frozen happy/remediation dispatch sequences are exact")
    print("- session recovery uses a distinct fresh Responses session and inbox discovery")
    print("- raw runner observations remain non-release-eligible")


if __name__ == "__main__":
    main()
