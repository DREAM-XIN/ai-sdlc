#!/usr/bin/env python3
"""Adversarial validation for the Responses provider terminal boundary.

Provider output is not canonical authority. A syntactically completed function
item must remain inert until the whole provider response is completed, and a
stream must preserve one response identity through an explicit
``response.completed`` boundary before the adapter may execute semantic work.
"""
from __future__ import annotations

from operator_openai_responses_host import OpenAIResponsesHost, ResponsesHostError
from validate_operator_openai_responses_host import (
    _FakeOpenAIClient,
    _FakeResponsesEndpoint,
    _adapter,
    _config,
    _function_call,
    _stream_events,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_zero_semantic_effect(backend, store_backend, *, label: str) -> None:
    require(backend.calls == 0, f"{label}: provider boundary invoked canonical backend")
    require(store_backend.read_snapshot().ref_sha is None, f"{label}: provider boundary mutated durable call journal")


def validate_noncompleted_sync_output_is_inert() -> None:
    cases = (
        ("in_progress", True),
        ("queued", True),
        ("failed", False),
        ("cancelled", False),
        ("incomplete", False),
    )
    for status, pending in cases:
        adapter, backend, store_backend = _adapter()
        endpoint = _FakeResponsesEndpoint(
            creates=[
                {
                    "id": f"resp-terminal-{status}",
                    "status": status,
                    "output": [_function_call(call_id=f"call-terminal-{status}")],
                }
            ]
        )
        host = OpenAIResponsesHost(
            client=_FakeOpenAIClient(endpoint),
            config=_config(),
            adapter=adapter,
        )
        result = host.run_sync({"role": "reviewer"})
        require(result.status == status, f"sync {status}: response status drifted")
        require(result.pending is pending, f"sync {status}: pending classification drifted")
        require(result.tool_rounds == 0, f"sync {status}: tool round advanced before response completion")
        require(len(endpoint.create_calls) == 1, f"sync {status}: host created an unauthorized continuation")
        _assert_zero_semantic_effect(backend, store_backend, label=f"sync {status}")


def validate_noncompleted_retrieval_output_is_inert() -> None:
    adapter, backend, store_backend = _adapter()
    response_id = "resp-background-terminal-fence"
    endpoint = _FakeResponsesEndpoint(
        creates=[],
        retrieves={
            response_id: {
                "id": response_id,
                "status": "in_progress",
                "output": [_function_call(call_id="call-background-terminal-fence")],
            }
        },
    )
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(background=True),
        adapter=adapter,
    )
    result = host.retrieve_and_continue(response_id)
    require(result.pending is True and result.status == "in_progress", "background retrieval did not remain pending")
    require(result.tool_rounds == 0, "background retrieval advanced tool round before completion")
    require(endpoint.create_calls == [], "background retrieval created a continuation before completion")
    _assert_zero_semantic_effect(backend, store_backend, label="background retrieval")


def validate_output_item_done_without_response_completed_is_inert() -> None:
    adapter, backend, store_backend = _adapter()
    call = _function_call(call_id="call-stream-terminal-fence")
    events = _stream_events("resp-stream-terminal-fence", call)[:-1]
    require(events[-1]["type"] == "response.output_item.done", "fixture no longer ends after output_item.done")
    endpoint = _FakeResponsesEndpoint(creates=[events])
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(),
        adapter=adapter,
    )
    try:
        host.run_stream({"role": "reviewer"})
    except ResponsesHostError as exc:
        require("response.completed" in str(exc), f"unexpected interrupted-stream error: {exc}")
    else:
        raise AssertionError("stream output_item.done executed without response.completed")
    _assert_zero_semantic_effect(backend, store_backend, label="stream missing response.completed")


def validate_noncompleted_terminal_event_is_inert() -> None:
    adapter, backend, store_backend = _adapter()
    call = _function_call(call_id="call-stream-failed-terminal")
    events = _stream_events("resp-stream-failed-terminal", call)
    terminal = dict(events[-1])
    terminal_response = dict(terminal["response"])
    terminal_response["status"] = "failed"
    terminal["response"] = terminal_response
    events[-1] = terminal

    endpoint = _FakeResponsesEndpoint(creates=[events])
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(),
        adapter=adapter,
    )
    result = host.run_stream({"role": "reviewer"})
    require(result.status == "failed" and result.pending is False, "failed terminal event was misclassified")
    require(result.tool_rounds == 0, "failed terminal event advanced tool round")
    _assert_zero_semantic_effect(backend, store_backend, label="stream failed terminal")


def validate_stream_response_identity_cannot_change() -> None:
    adapter, backend, store_backend = _adapter()
    call = _function_call(call_id="call-stream-identity-fence")
    events = _stream_events("resp-stream-A", call)
    terminal = dict(events[-1])
    terminal_response = dict(terminal["response"])
    terminal_response["id"] = "resp-stream-B"
    terminal["response"] = terminal_response
    events[-1] = terminal

    endpoint = _FakeResponsesEndpoint(creates=[events])
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(),
        adapter=adapter,
    )
    try:
        host.run_stream({"role": "reviewer"})
    except ResponsesHostError as exc:
        require("response id changed" in str(exc), f"unexpected mixed-response stream error: {exc}")
    else:
        raise AssertionError("mixed response ids crossed the stream completion boundary")
    _assert_zero_semantic_effect(backend, store_backend, label="mixed response ids")


def main() -> None:
    validate_noncompleted_sync_output_is_inert()
    validate_noncompleted_retrieval_output_is_inert()
    validate_output_item_done_without_response_completed_is_inert()
    validate_noncompleted_terminal_event_is_inert()
    validate_stream_response_identity_cannot_change()
    print("OpenAI Responses provider terminal-boundary validation passed")
    print("- pending/failed/cancelled/incomplete responses cannot execute function calls")
    print("- background retrieval remains zero-authority until provider completion")
    print("- response.output_item.done alone cannot authorize semantic execution")
    print("- stream execution requires explicit response.completed")
    print("- all visible stream response ids must remain identical")


if __name__ == "__main__":
    main()
