#!/usr/bin/env python3
"""Require explicit provider response status before Responses tool execution.

This is a narrow regression for the provider terminal-completion boundary. A
provider object with no explicit ``status`` is malformed/ambiguous and must stay
zero-authority even if it contains a syntactically completed function call.
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


def _assert_zero(backend, store_backend, endpoint, *, label: str) -> None:
    require(backend.calls == 0, f"{label}: canonical backend was invoked")
    require(
        store_backend.read_snapshot().ref_sha is None,
        f"{label}: durable Responses call journal was mutated",
    )
    require(
        len(endpoint.create_calls) == 1,
        f"{label}: host created an unauthorized continuation",
    )


def validate_sync_missing_status_is_zero_authority() -> None:
    adapter, backend, store_backend = _adapter()
    endpoint = _FakeResponsesEndpoint(
        creates=[
            {
                "id": "resp-missing-status-sync",
                "output": [_function_call(call_id="call-missing-status-sync")],
            }
        ]
    )
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(),
        adapter=adapter,
    )
    try:
        host.run_sync({"role": "reviewer"})
    except ResponsesHostError as exc:
        require("explicit status" in str(exc), f"unexpected missing-status error: {exc}")
    else:
        raise AssertionError("sync response without explicit status crossed provider completion boundary")
    _assert_zero(backend, store_backend, endpoint, label="sync missing status")


def validate_retrieval_missing_status_is_zero_authority() -> None:
    adapter, backend, store_backend = _adapter()
    response_id = "resp-missing-status-retrieve"
    endpoint = _FakeResponsesEndpoint(
        creates=[],
        retrieves={
            response_id: {
                "id": response_id,
                "output": [_function_call(call_id="call-missing-status-retrieve")],
            }
        },
    )
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(background=True),
        adapter=adapter,
    )
    try:
        host.retrieve_and_continue(response_id)
    except ResponsesHostError as exc:
        require("explicit status" in str(exc), f"unexpected retrieval missing-status error: {exc}")
    else:
        raise AssertionError("retrieved response without explicit status executed semantic work")
    require(backend.calls == 0, "retrieval missing status invoked canonical backend")
    require(store_backend.read_snapshot().ref_sha is None, "retrieval missing status mutated call journal")
    require(endpoint.create_calls == [], "retrieval missing status created an unauthorized continuation")


def validate_stream_terminal_missing_status_is_zero_authority() -> None:
    adapter, backend, store_backend = _adapter()
    events = _stream_events(
        "resp-missing-status-stream",
        _function_call(call_id="call-missing-status-stream"),
    )
    terminal = dict(events[-1])
    terminal_response = dict(terminal["response"])
    terminal_response.pop("status", None)
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
        require("explicit status" in str(exc), f"unexpected stream missing-status error: {exc}")
    else:
        raise AssertionError("response.completed without explicit nested status executed semantic work")
    _assert_zero(backend, store_backend, endpoint, label="stream missing status")


def main() -> None:
    validate_sync_missing_status_is_zero_authority()
    validate_retrieval_missing_status_is_zero_authority()
    validate_stream_terminal_missing_status_is_zero_authority()
    print("OpenAI Responses explicit provider-status validation passed")
    print("- missing status never implies completed")
    print("- sync/background/stream missing-status payloads remain zero-authority")
    print("- completed-looking function items cannot bypass provider completion state")


if __name__ == "__main__":
    main()
