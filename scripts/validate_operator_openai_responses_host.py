#!/usr/bin/env python3
"""Deterministic host tests without live/billable OpenAI requests."""
from __future__ import annotations

import json

from operator_api import API_VERSION
from operator_openai_responses import ADAPTER_ID, OpenAIResponsesOperatorAdapter, TrustedResponsesRegistration
from operator_openai_responses_host import (
    OpenAIResponsesHost,
    ResponsesHostError,
    TrustedOpenAIResponsesHostConfig,
)
from operator_openai_responses_journal import StoreResponsesCallJournal
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier

REPO = "DREAM-XIN/host-fixture"
FEATURE = "F-HOST-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _FeatureBackend:
    def __init__(self):
        self.calls = 0

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        self.calls += 1
        _expect(request["target"] == {"repository": REPO, "feature_id": FEATURE}, "host target mapping")
        _expect(request["client_identity"]["adapter_id"] == ADAPTER_ID, "host adapter identity")
        _expect(trusted_context["trusted_principal"] == "host-principal", "host trusted principal")
        return {
            "feature_id": FEATURE,
            "revision": 3,
            "workflow_status": "ACTIVE",
            "current_stage": "implementation",
        }


class _FakeResponsesEndpoint:
    def __init__(self, *, creates=None, retrieves=None):
        self.creates = list(creates or [])
        self.retrieves = dict(retrieves or {})
        self.create_calls = []
        self.retrieve_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if not self.creates:
            raise AssertionError("unexpected Responses create call")
        return self.creates.pop(0)

    def retrieve(self, response_id, **kwargs):
        self.retrieve_calls.append((response_id, kwargs))
        if response_id not in self.retrieves:
            raise AssertionError(f"unexpected Responses retrieve call: {response_id}")
        return self.retrieves[response_id]


class _FakeOpenAIClient:
    def __init__(self, endpoint):
        self.responses = endpoint


def _adapter():
    store_backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = OperatorStoreRuntime(
        backend=store_backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T11:12:00Z",
    )
    registration = TrustedResponsesRegistration(
        registration_id="host-registration",
        provider_scope_id="host-provider-scope",
        target_repository=REPO,
        feature_refs={FEATURE: "refs/heads/feature/F-HOST-0001"},
        trusted_context={
            "trusted_identity": {
                "service_id": "host-service",
                "runtime_id": "host-runtime",
                "authorization_context": "host-policy",
            },
            "trusted_scope": {"repositories": [REPO], "feature_ids": [FEATURE]},
            "trusted_principal": "host-principal",
        },
        human_principal="host-principal",
    )
    feature_backend = _FeatureBackend()
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"feature.status": feature_backend},
        journal=StoreResponsesCallJournal(runtime),
    )
    return adapter, feature_backend, store_backend


def _function_call(*, call_id, feature=FEATURE):
    return {
        "type": "function_call",
        "id": f"fc-{call_id}",
        "call_id": call_id,
        "name": "aisdlc_v1_feature_status",
        "arguments": json.dumps(
            {"api_version": API_VERSION, "feature_id": feature},
            separators=(",", ":"),
        ),
        "status": "completed",
    }


def _config(**overrides):
    values = {
        "model": "server-owned-model",
        "instructions": "Use only the bounded AI-SDLC tools exposed by this host.",
        "store": True,
        "background": False,
        "max_tool_rounds": 4,
    }
    values.update(overrides)
    return TrustedOpenAIResponsesHostConfig(**values)


def validate_sync_round_trip() -> None:
    adapter, backend, store_backend = _adapter()
    first_call = _function_call(call_id="host-sync-call")
    endpoint = _FakeResponsesEndpoint(
        creates=[
            {"id": "resp-sync-1", "status": "completed", "output": [first_call]},
            {"id": "resp-sync-2", "status": "completed", "output": [{"type": "message", "content": []}]},
        ]
    )
    host = OpenAIResponsesHost(client=_FakeOpenAIClient(endpoint), config=_config(), adapter=adapter)
    result = host.run_sync("Inspect the authorized Feature status.")

    _expect(result.response_id == "resp-sync-2" and result.tool_rounds == 1, "sync host did not converge")
    _expect(result.pending is False and backend.calls == 1, "sync semantic dispatch count")
    _expect(len(endpoint.create_calls) == 2, "sync host create call count")
    first_kwargs, continuation = endpoint.create_calls
    _expect(first_kwargs["model"] == "server-owned-model", "model must come from trusted config")
    _expect(first_kwargs["instructions"] == _config().instructions, "instructions must be trusted")
    _expect(first_kwargs["parallel_tool_calls"] is False, "host must disable parallel tool calls")
    _expect(len(first_kwargs["tools"]) == 10, "host must expose exact ten Responses tools")
    _expect("background" not in first_kwargs and "stream" not in first_kwargs, "ordinary sync mode flags")
    _expect(continuation["previous_response_id"] == "resp-sync-1", "continuation response correlation")
    function_output = continuation["input"][0]
    _expect(function_output["type"] == "function_call_output", "continuation output type")
    _expect(function_output["call_id"] == "host-sync-call", "continuation call_id correlation")
    _expect(store_backend.read_snapshot().ref_sha is not None, "sync host did not use durable call journal")


def _stream_events(response_id, call=None):
    if call is None:
        return [
            {
                "type": "response.completed",
                "response": {"id": response_id, "status": "completed", "output": [{"type": "message", "content": []}]},
            }
        ]
    args = call["arguments"]
    split = len(args) // 2
    return [
        {
            "type": "response.output_item.added",
            "response_id": response_id,
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": call["id"],
                "call_id": call["call_id"],
                "name": call["name"],
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": response_id,
            "item_id": call["id"],
            "output_index": 0,
            "delta": args[:split],
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": response_id,
            "item_id": call["id"],
            "output_index": 0,
            "delta": args[split:],
        },
        {
            "type": "response.function_call_arguments.done",
            "response_id": response_id,
            "item_id": call["id"],
            "output_index": 0,
            "arguments": args,
        },
        {
            "type": "response.output_item.done",
            "response_id": response_id,
            "output_index": 0,
            "item": call,
        },
        {
            "type": "response.completed",
            "response": {"id": response_id, "status": "completed", "output": [call]},
        },
    ]


def validate_stream_round_trip() -> None:
    adapter, backend, _ = _adapter()
    call = _function_call(call_id="host-stream-call")
    endpoint = _FakeResponsesEndpoint(
        creates=[
            _stream_events("resp-stream-1", call),
            _stream_events("resp-stream-2"),
        ]
    )
    host = OpenAIResponsesHost(client=_FakeOpenAIClient(endpoint), config=_config(), adapter=adapter)
    result = host.run_stream("Inspect by streaming.")
    _expect(result.response_id == "resp-stream-2" and result.tool_rounds == 1, "stream host convergence")
    _expect(backend.calls == 1, "stream must dispatch only after terminal function item")
    _expect(endpoint.create_calls[0]["stream"] is True, "initial stream flag")
    _expect(endpoint.create_calls[1]["stream"] is True, "continuation stream flag")
    _expect(endpoint.create_calls[1]["previous_response_id"] == "resp-stream-1", "stream previous response")


def validate_interrupted_stream_zero_dispatch() -> None:
    adapter, backend, _ = _adapter()
    call = _function_call(call_id="host-interrupted")
    interrupted = _stream_events("resp-interrupted", call)[:3]
    endpoint = _FakeResponsesEndpoint(creates=[interrupted])
    host = OpenAIResponsesHost(client=_FakeOpenAIClient(endpoint), config=_config(), adapter=adapter)
    try:
        host.run_stream("Interrupted stream.")
    except Exception as exc:
        _expect("stream" in str(exc).lower(), "interrupted stream should fail at Responses collector")
    else:
        raise AssertionError("interrupted stream unexpectedly converged")
    _expect(backend.calls == 0, "interrupted stream must perform zero semantic dispatch")


def validate_stream_interruption_retrieval_recovery() -> None:
    """Known provider response id may recover transport, but never Operator truth."""

    adapter, backend, store_backend = _adapter()
    call = _function_call(call_id="host-retrieval-recovery")
    interrupted = _stream_events("resp-recoverable", call)[:3]
    completed = {"id": "resp-recoverable", "status": "completed", "output": [call]}
    terminal_message_1 = {
        "id": "resp-recovered-next",
        "status": "completed",
        "output": [{"type": "message", "content": []}],
    }
    terminal_message_2 = {
        "id": "resp-replayed-next",
        "status": "completed",
        "output": [{"type": "message", "content": []}],
    }
    endpoint = _FakeResponsesEndpoint(
        creates=[interrupted, terminal_message_1, terminal_message_2],
        retrieves={"resp-recoverable": completed},
    )
    host = OpenAIResponsesHost(client=_FakeOpenAIClient(endpoint), config=_config(), adapter=adapter)

    try:
        host.run_stream("Start a stream that loses its terminal events.")
    except Exception as exc:
        _expect("stream" in str(exc).lower(), "recoverable interrupted stream failed outside collector")
    else:
        raise AssertionError("recoverable interrupted stream unexpectedly dispatched")
    _expect(backend.calls == 0, "interrupted stream performed semantic work before retrieval")
    _expect(store_backend.read_snapshot().ref_sha is None, "interrupted stream mutated durable call journal")

    recovered = host.retrieve_and_continue("resp-recoverable")
    _expect(recovered.response_id == "resp-recovered-next", "retrieval recovery did not converge")
    _expect(backend.calls == 1, "retrieval recovery did not perform exactly one semantic dispatch")
    recovery_ref = store_backend.read_snapshot().ref_sha
    _expect(recovery_ref is not None, "retrieval recovery did not persist durable call journal")
    _expect(endpoint.retrieve_calls == [("resp-recoverable", {})], "retrieval targeted wrong provider response")
    recovery_continuation = endpoint.create_calls[1]
    _expect(
        recovery_continuation["previous_response_id"] == "resp-recoverable",
        "retrieval continuation lost provider conversation correlation",
    )
    _expect(
        recovery_continuation["input"][0]["call_id"] == "host-retrieval-recovery",
        "retrieval continuation did not correlate function output by call_id",
    )

    # Provider retrieval may be retried, but the exact function call must enter
    # the same durable journal first and therefore replay without backend work.
    replayed = host.retrieve_and_continue("resp-recoverable")
    _expect(replayed.response_id == "resp-replayed-next", "retrieval replay did not converge")
    _expect(backend.calls == 1, "retrieval replay repeated semantic backend work")
    _expect(store_backend.read_snapshot().ref_sha == recovery_ref, "retrieval replay mutated journal Store")
    _expect(
        endpoint.retrieve_calls == [("resp-recoverable", {}), ("resp-recoverable", {})],
        "provider retrieval retry sequence drifted",
    )
    replay_continuation = endpoint.create_calls[2]
    _expect(
        replay_continuation["input"][0] == recovery_continuation["input"][0],
        "retrieval replay changed durable function_call_output",
    )


def validate_background_retrieval() -> None:
    adapter, backend, _ = _adapter()
    retrieved_call = _function_call(call_id="host-background-call")
    endpoint = _FakeResponsesEndpoint(
        creates=[
            {"id": "resp-bg-1", "status": "in_progress", "output": []},
            {"id": "resp-bg-2", "status": "completed", "output": []},
        ],
        retrieves={
            "resp-bg-1": {"id": "resp-bg-1", "status": "completed", "output": [retrieved_call]}
        },
    )
    host = OpenAIResponsesHost(
        client=_FakeOpenAIClient(endpoint),
        config=_config(background=True),
        adapter=adapter,
    )
    pending = host.run_sync("Background inspection.")
    _expect(pending.pending is True and pending.response_id == "resp-bg-1", "background pending result")
    _expect(endpoint.create_calls[0]["background"] is True, "background create flag")
    _expect(backend.calls == 0, "pending background response must not dispatch")

    completed_result = host.retrieve_and_continue("resp-bg-1")
    _expect(completed_result.response_id == "resp-bg-2" and completed_result.pending is False, "background retrieval convergence")
    _expect(backend.calls == 1, "retrieved terminal function call dispatch count")
    _expect(endpoint.retrieve_calls == [("resp-bg-1", {})], "exact response retrieval target")
    _expect(endpoint.create_calls[1]["previous_response_id"] == "resp-bg-1", "background continuation correlation")
    _expect(endpoint.create_calls[1]["background"] is True, "background continuation remains server configured")


def validate_trusted_configuration() -> None:
    try:
        TrustedOpenAIResponsesHostConfig(model="", instructions="x")
    except ValueError:
        pass
    else:
        raise AssertionError("empty trusted model unexpectedly accepted")
    try:
        TrustedOpenAIResponsesHostConfig(model="m", instructions="x", max_tool_rounds=0)
    except ValueError:
        pass
    else:
        raise AssertionError("unbounded/zero tool rounds unexpectedly accepted")
    try:
        OpenAIResponsesHost(client=object(), config=_config(), adapter=_adapter()[0])
    except ValueError:
        pass
    else:
        raise AssertionError("host without Responses SDK surface unexpectedly accepted")
    try:
        adapter, _, _ = _adapter()
        endpoint = _FakeResponsesEndpoint(retrieves={"resp-good": {"id": "resp-other", "status": "completed", "output": []}})
        OpenAIResponsesHost(client=_FakeOpenAIClient(endpoint), config=_config(), adapter=adapter).retrieve_and_continue("resp-good")
    except ResponsesHostError:
        pass
    else:
        raise AssertionError("retrieval id mismatch unexpectedly accepted")


def main() -> None:
    validate_sync_round_trip()
    validate_stream_round_trip()
    validate_interrupted_stream_zero_dispatch()
    validate_stream_interruption_retrieval_recovery()
    validate_background_retrieval()
    validate_trusted_configuration()
    print("OpenAI Responses SDK host validation passed")
    print("- sync / stream / background-retrieve paths use one production Responses adapter boundary")
    print("- interrupted stream recovery by trusted response id re-enters durable journal before semantic work")
    print("- repeated retrieval replays the exact durable function output with zero second backend dispatch")
    print("- model/instructions/background policy remain server-owned")
    print("- deterministic CI injects SDK-shaped clients and performs no live OpenAI request")


if __name__ == "__main__":
    main()
