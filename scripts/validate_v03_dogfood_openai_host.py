#!/usr/bin/env python3
"""Deterministic adversarial validation for the v0.3 real-dogfood Responses host."""
from __future__ import annotations

from operator_api import API_VERSION
from operator_openai_responses import responses_request_profile
from v03_dogfood_openai_host import (
    V03DogfoodOpenAIHostConfig,
    V03DogfoodOpenAIHostError,
    V03DogfoodOpenAIResponsesHost,
    dogfood_responses_request_profile,
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


def call(call_id: str, name: str = "aisdlc_v1_system_capabilities"):
    arguments = '{"api_version":"ai-sdlc.operator/v1"}'
    if name == "aisdlc_v1_operation_start":
        arguments = (
            '{"api_version":"ai-sdlc.operator/v1","feature_id":"F-TEST",'
            '"expected_feature_revision":1,"mode":"ASSISTED"}'
        )
    return {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def message():
    return {"type": "message", "id": "msg_1", "role": "assistant", "content": []}


def host(
    rows, adapter=None, *, max_turns=4, continuation_mode="previous_response_id",
    api_base="https://api.openai.com/v1", requests=None,
):
    queue = list(rows)

    def post(url, headers, body):
        assert url == api_base.rstrip("/") + "/responses"
        assert headers["Authorization"] == "Bearer test-key"
        assert body["parallel_tool_calls"] is False
        assert body["tools"]
        if requests is not None:
            requests.append(dict(body))
        if not queue:
            raise AssertionError("unexpected provider request")
        return 200, queue.pop(0)

    return V03DogfoodOpenAIResponsesHost(
        config=V03DogfoodOpenAIHostConfig(
            api_key="test-key", model="gpt-test", api_base=api_base,
            continuation_mode=continuation_mode, max_tool_turns=max_turns,
        ),
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
    generic = responses_request_profile()
    dogfood = dogfood_responses_request_profile()
    assert generic is not dogfood
    assert generic["tools"] != dogfood["tools"]
    for tool in dogfood["tools"]:
        api_schema = tool["parameters"]["properties"]["api_version"]
        assert api_schema == {"type": "string", "enum": [API_VERSION]}
    for tool in generic["tools"]:
        api_schema = tool["parameters"]["properties"]["api_version"]
        assert api_schema.get("enum") is None, "reusable adapter version negotiation was narrowed"

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

    # The trusted controller may force only a registered initial function tool.
    choice_requests = []
    choice_host = host(
        [
            response("resp_choice", call("call_choice", "aisdlc_v1_operation_start")),
            response("resp_choice_done", message()),
        ],
        requests=choice_requests,
    )
    choice_host.run(
        scenario_instruction="trusted named-choice dogfood",
        initial_tool_name="aisdlc_v1_operation_start",
    )
    assert choice_requests[0]["tool_choice"] == {
        "type": "function",
        "name": "aisdlc_v1_operation_start",
    }
    assert "tool_choice" not in choice_requests[1], "named tool choice leaked into continuation"
    try:
        host([response("resp_unused", message())]).run(
            scenario_instruction="trusted invalid choice",
            initial_tool_name="aisdlc_v1_unknown",
        )
    except V03DogfoodOpenAIHostError as exc:
        assert "initial tool choice" in str(exc)
    else:
        raise AssertionError("unregistered trusted initial tool choice unexpectedly passed")

    # Stateless Responses providers must receive the complete prior user/model/tool
    # transcript instead of an unsupported previous_response_id.
    stateless_requests = []
    stateless = host(
        [response("resp_s1", call("call_s1")), response("resp_s2", message())],
        continuation_mode="full_history",
        api_base="https://api.deepseek.com",
        requests=stateless_requests,
    )
    stateless_trace = stateless.run(scenario_instruction="trusted stateless dogfood")
    assert stateless_trace.response_ids == ("resp_s1", "resp_s2")
    assert len(stateless_requests) == 2
    assert stateless_requests[0]["input"] == "trusted stateless dogfood"
    continuation = stateless_requests[1]
    assert "previous_response_id" not in continuation
    assert continuation["input"][0] == {"role": "user", "content": "trusted stateless dogfood"}
    assert any(item.get("type") == "function_call" and item.get("call_id") == "call_s1"
               for item in continuation["input"] if isinstance(item, dict))
    assert continuation["input"][-1]["type"] == "function_call_output"
    assert continuation["input"][-1]["call_id"] == "call_s1"

    # Completed provider output is mandatory before any executable call crosses
    # the adapter boundary.
    must_fail([response("resp_pending", call("call_p"), status="in_progress")], "completed Responses")

    # DeepSeek Responses always enables parallel tool calls even when the request
    # asks for false. Multiple read-only calls are accepted only after the whole
    # batch is prevalidated, then serialized through the reviewed adapter.
    parallel_adapter = FakeAdapter()
    parallel_requests = []
    parallel = host(
        [
            response("resp_multi", call("call_a"), call("call_b")),
            response("resp_multi_done", message()),
        ],
        adapter=parallel_adapter,
        continuation_mode="full_history",
        api_base="https://api.deepseek.com",
        requests=parallel_requests,
    )
    parallel_trace = parallel.run(scenario_instruction="trusted parallel-read dogfood")
    assert parallel_trace.function_call_ids == ("call_a", "call_b")
    assert [row["call_id"] for row in parallel_adapter.calls] == ["call_a", "call_b"]
    assert len(parallel_requests) == 2
    continuation_items = parallel_requests[1]["input"]
    assert [row.get("call_id") for row in continuation_items if row.get("type") == "function_call"] == [
        "call_a", "call_b"
    ]
    assert [row.get("call_id") for row in continuation_items if row.get("type") == "function_call_output"] == [
        "call_a", "call_b"
    ]

    # A parallel batch containing any write must fail before *any* read or write
    # reaches the adapter, preventing partial effects from one provider response.
    mixed_adapter = FakeAdapter()
    try:
        host(
            [response(
                "resp_mixed",
                call("call_read"),
                call("call_write", "aisdlc_v1_operation_start"),
            )],
            adapter=mixed_adapter,
        ).run(scenario_instruction="trusted mixed dogfood")
    except V03DogfoodOpenAIHostError as exc:
        assert "write capability" in str(exc)
    else:
        raise AssertionError("parallel mixed read/write batch unexpectedly passed")
    assert mixed_adapter.calls == [], "mixed parallel batch partially reached adapter effects"

    duplicate_batch_adapter = FakeAdapter()
    try:
        host(
            [response("resp_batch_dup", call("call_same"), call("call_same"))],
            adapter=duplicate_batch_adapter,
        ).run(scenario_instruction="trusted duplicate batch")
    except V03DogfoodOpenAIHostError as exc:
        assert "duplicate call_id" in str(exc)
    else:
        raise AssertionError("duplicate call_id inside parallel batch unexpectedly passed")
    assert duplicate_batch_adapter.calls == []

    # Built-in/hosted executable items are not alternate Operator authorities.
    must_fail([response("resp_builtin", {"type": "mcp_call", "id": "mcp_1"})], "unsupported executable")

    # Provider response identity is part of the trusted host trace and cannot
    # cycle/replay across a later turn.
    must_fail([
        response("resp_cycle", call("call_c")),
        response("resp_cycle", message()),
    ], "repeated a response id")

    # Duplicate call identity is rejected before crossing the effect boundary.
    duplicate_adapter = FakeAdapter()
    try:
        host([response("resp_dup1", call("call_dup")), response("resp_dup2", call("call_dup"))],
             adapter=duplicate_adapter).run(scenario_instruction="trusted scenario")
    except V03DogfoodOpenAIHostError as exc:
        assert "same Responses call_id" in str(exc)
    else:
        raise AssertionError("duplicate call identity was accepted")
    assert len(duplicate_adapter.calls) == 1, "duplicate call reached adapter effects"

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
