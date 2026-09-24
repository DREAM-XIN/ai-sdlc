#!/usr/bin/env python3
"""Deterministic adversarial validation for the v0.3 real-dogfood Responses host."""
from __future__ import annotations

from v03_dogfood_openai_host import (
    V03DogfoodOpenAIHostConfig,
    V03DogfoodOpenAIHostError,
    V03DogfoodOpenAIResponsesHost,
)


class FakeAdapter:
    def __init__(self) -> None:
        self.calls = []

    def invoke_function_call(self, item):
        self.calls.append(dict(item))
        return {
            "type": "function_call_output",
            "call_id": item["call_id"],
            "output": '{"api_version":"ai-sdlc.operator/v1","ok":true}',
        }


def response(response_id: str, *items, status: str = "completed"):
    return {"id": response_id, "status": status, "output": list(items)}


def call(call_id: str):
    return {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "name": "aisdlc_v1_system_capabilities",
        "arguments": '{"api_version":"ai-sdlc.operator/v1"}',
        "status": "completed",
    }


def message():
    return {"type": "message", "id": "msg_1", "role": "assistant", "content": []}


def host(rows, adapter=None, *, max_turns=4):
    queue = list(rows)

    def post(url, headers, body):
        assert url == "https://api.openai.com/v1/responses"
        assert headers["Authorization"] == "Bearer test-key"
        assert body["parallel_tool_calls"] is False
        assert body["tools"]
        if not queue:
            raise AssertionError("unexpected provider request")
        return 200, queue.pop(0)

    return V03DogfoodOpenAIResponsesHost(
        config=V03DogfoodOpenAIHostConfig(api_key="test-key", model="gpt-test", max_tool_turns=max_turns),
        adapter=adapter or FakeAdapter(),
        http_post=post,
    )


def must_fail(rows, expected: str, *, max_turns=4):
    try:
        host(rows, max_turns=max_turns).run(scenario_instruction="trusted dogfood scenario")
    except V03DogfoodOpenAIHostError as exc:
        assert expected in str(exc), (expected, str(exc))
    else:
        raise AssertionError("expected dogfood Responses host failure")


def main() -> None:
    adapter = FakeAdapter()
    runner = host([
        response("resp_1", call("call_1")),
        response("resp_2", message()),
    ], adapter=adapter)
    trace = runner.run(scenario_instruction="trusted happy-path dogfood")
    assert trace.response_ids == ("resp_1", "resp_2")
    assert trace.function_call_ids == ("call_1",)
    assert len(adapter.calls) == 1
    assert trace.function_outputs[0]["call_id"] == "call_1"

    # Completed provider output is mandatory before any executable call crosses
    # the adapter boundary.
    must_fail([response("resp_pending", call("call_p"), status="in_progress")], "completed Responses")

    # The supported profile is intentionally sequential and cannot partially
    # execute a malformed provider batch.
    must_fail([response("resp_multi", call("call_a"), call("call_b"))], "multiple function calls")

    # Built-in/hosted executable items are not alternate Operator authorities.
    must_fail([response("resp_builtin", {"type": "mcp_call", "id": "mcp_1"})], "unsupported executable")

    # Provider response identity is part of the trusted host trace and cannot
    # cycle/replay across a later turn.
    must_fail([
        response("resp_cycle", call("call_c")),
        response("resp_cycle", message()),
    ], "repeated a response id")

    # Exact adapter call_id correlation is mandatory.
    class WrongCorrelation(FakeAdapter):
        def invoke_function_call(self, item):
            return {"type": "function_call_output", "call_id": "wrong", "output": "{}"}

    try:
        host([response("resp_corr", call("call_corr"))], adapter=WrongCorrelation()).run(
            scenario_instruction="trusted scenario"
        )
    except V03DogfoodOpenAIHostError as exc:
        assert "correlation" in str(exc)
    else:
        raise AssertionError("wrong function-call correlation was accepted")

    # Bound tool turns: once the adapter has executed the allowed number of
    # calls the host cannot ask the provider for another executable turn.
    must_fail([
        response("resp_t1", call("call_t1")),
        response("resp_t2", call("call_t2")),
    ], "exceeded bounded tool turns", max_turns=1)

    print("v0.3 dogfood OpenAI Responses host validation: PASS")


if __name__ == "__main__":
    main()
