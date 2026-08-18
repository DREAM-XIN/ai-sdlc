#!/usr/bin/env python3
"""Deterministic WU1-WU3 checks for the OpenAI Responses adapter boundary."""
from __future__ import annotations

import copy
import json

from operator_api import API_VERSION
from operator_openai_responses import (
    ADAPTER_ID,
    TOOL_CAPABILITIES,
    TOOL_PARAMETER_SCHEMAS,
    TOOLS,
    WRITE_CAPABILITIES,
    OpenAIResponsesOperatorAdapter,
    ResponsesAuthorizationError,
    ResponsesProtocolError,
    TrustedResponsesRegistration,
    build_canonical_request,
    collect_function_call,
    collect_stream_function_call,
    parse_function_call,
    responses_call_key,
    responses_request_profile,
)
from operator_openai_responses_journal import (
    CALL_BINDING_SCHEMA,
    CALL_RESULT_SCHEMA,
    ResponsesCallConflict,
    ResponsesJournalError,
    StoreResponsesCallJournal,
    call_binding_path,
    call_result_path,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier

REPO = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-11T10:58:00Z"


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _expect_protocol(code: str, fn) -> None:
    try:
        fn()
    except ResponsesProtocolError as exc:
        _expect(exc.code == code, f"expected {code}, got {exc.code}")
        return
    raise AssertionError(f"expected ResponsesProtocolError({code})")


class _MemoryJournal:
    """Lane-A transport-only test double; it never satisfies durable Supported proof."""

    test_only = True

    def __init__(self):
        self.bindings = {}
        self.results = {}

    def bind_call(self, *, call_key, binding):
        current = self.bindings.get(call_key)
        if current is not None and current != binding:
            raise RuntimeError("conflicting immutable binding")
        self.bindings.setdefault(call_key, copy.deepcopy(binding))
        return copy.deepcopy(self.bindings[call_key])

    def lookup_result(self, *, call_key):
        value = self.results.get(call_key)
        return copy.deepcopy(value) if value is not None else None

    def record_result(self, *, call_key, result):
        current = self.results.get(call_key)
        if current is not None and current != result:
            raise RuntimeError("conflicting result receipt")
        self.results.setdefault(call_key, copy.deepcopy(result))
        return copy.deepcopy(self.results[call_key])


class _FeatureStatusBackend:
    def __init__(self):
        self.calls = 0

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        self.calls += 1
        _expect(request["target"]["repository"] == REPO, "trusted repository mapping")
        _expect(request["target"]["feature_id"] == "F-TEST-0001", "feature selector mapping")
        _expect(
            trusted_context["trusted_client_adapter_id"] == ADAPTER_ID,
            "trusted adapter identity must be server-owned",
        )
        return {
            "feature_id": "F-TEST-0001",
            "revision": 7,
            "workflow_status": "ACTIVE",
            "current_stage": "implementation",
        }


def _registration(*, provider_scope="provider-scope-a"):
    return TrustedResponsesRegistration(
        registration_id="responses-prod-a",
        provider_scope_id=provider_scope,
        target_repository=REPO,
        feature_refs={
            "F-TEST-0001": "refs/heads/feature/F-TEST-0001",
            "F-TEST-0002": "refs/heads/feature/F-TEST-0002",
        },
        trusted_context={
            "trusted_identity": {
                "service_id": "ai-sdlc-openai-responses",
                "runtime_id": "responses-test",
                "authorization_context": "operator-bounded",
            },
            "trusted_scope": {
                "repositories": [REPO],
                "feature_ids": ["F-TEST-0001", "F-TEST-0002"],
            },
            "trusted_principal": "test-principal",
        },
        human_principal="test-principal",
    )


def _call(name, call_id, arguments, *, status="completed"):
    return {
        "type": "function_call",
        "id": f"fc-{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
        "status": status,
    }


def _test_store_runtime():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )
    return backend, runtime


def validate_registry() -> None:
    expected = {
        "aisdlc_v1_system_capabilities",
        "aisdlc_v1_feature_status",
        "aisdlc_v1_operator_inbox",
        "aisdlc_v1_operation_status",
        "aisdlc_v1_decision_list",
        "aisdlc_v1_notification_list",
        "aisdlc_v1_operation_start",
        "aisdlc_v1_operation_cancel",
        "aisdlc_v1_decision_respond",
        "aisdlc_v1_notification_ack",
    }
    _expect(set(TOOL_CAPABILITIES) == expected, "Responses registry must be exact ten tools")
    _expect(len(TOOLS) == 10, "Responses provider registry must contain exactly ten definitions")
    _expect(
        WRITE_CAPABILITIES
        == {"operation.start", "operation.cancel", "decision.respond", "notification.ack"},
        "Responses write surface must be exact",
    )
    _expect("operation.resume" not in TOOL_CAPABILITIES.values(), "operation.resume must remain server-only")
    _expect("project.inspect" not in TOOL_CAPABILITIES.values(), "project.inspect is outside supported surface")
    profile = responses_request_profile()
    _expect(profile["parallel_tool_calls"] is False, "parallel tool calls must be disabled")
    for tool in profile["tools"]:
        _expect(tool["type"] == "function", "Responses tool type")
        _expect(tool["strict"] is True, "all Responses functions must use strict mode")
        schema = tool["parameters"]
        _expect(schema["additionalProperties"] is False, "strict object must reject extras")
        _expect(set(schema["required"]) == set(schema["properties"]), "strict requires every property")


def validate_terminal_protocol() -> None:
    registration = _registration()
    item = _call(
        "aisdlc_v1_operation_start",
        "call-start",
        {
            "api_version": API_VERSION,
            "feature_id": "F-TEST-0001",
            "expected_feature_revision": 12,
            "mode": None,
        },
    )
    parsed = parse_function_call(item)
    _expect(parsed.capability == "operation.start", "fixed tool mapping")
    request = build_canonical_request(
        parsed,
        registration=registration,
        call_key=responses_call_key(registration, parsed.call_id),
    )
    _expect(request["target"] == {"repository": REPO, "feature_id": "F-TEST-0001"}, "target mapping")
    _expect(request["context"] == {"expected_feature_revision": 12}, "revision context mapping")
    _expect(request["payload"] == {}, "nullable mode must omit optional canonical payload")
    _expect(request["idempotency_key"].startswith("openai-responses/"), "write idempotency key")
    _expect(request["client_identity"]["adapter_id"] == ADAPTER_ID, "fixed adapter identity")

    cancel = parse_function_call(
        _call(
            "aisdlc_v1_operation_cancel",
            "call-cancel",
            {"api_version": API_VERSION, "operation_id": "op-123", "reason": None},
        )
    )
    cancel_request = build_canonical_request(
        cancel,
        registration=registration,
        call_key=responses_call_key(registration, cancel.call_id),
    )
    _expect(cancel_request["context"] == {"operation_id": "op-123"}, "cancel operation context")
    _expect(cancel_request["payload"] == {}, "nullable cancel reason must be omitted")

    _expect_protocol(
        "UNKNOWN_TOOL",
        lambda: parse_function_call(
            _call("aisdlc_v1_not_a_tool", "call-unknown-tool", {"api_version": API_VERSION})
        ),
    )

    malformed = _call(
        "aisdlc_v1_system_capabilities",
        "call-malformed-json",
        {"api_version": API_VERSION},
    )
    malformed["arguments"] = "{not-json"
    _expect_protocol("MALFORMED_ARGUMENTS", lambda: parse_function_call(malformed))

    missing_call_id = _call(
        "aisdlc_v1_system_capabilities",
        "provider-item-id-is-not-call-id",
        {"api_version": API_VERSION},
    )
    missing_call_id.pop("call_id")
    _expect_protocol("INVALID_CALL_ID", lambda: parse_function_call(missing_call_id))

    invalid_call_id = _call(
        "aisdlc_v1_system_capabilities",
        "valid-fixture-id",
        {"api_version": API_VERSION},
    )
    invalid_call_id["call_id"] = "invalid call id with spaces"
    _expect_protocol("INVALID_CALL_ID", lambda: parse_function_call(invalid_call_id))

    _expect_protocol(
        "SCHEMA_INVALID_ARGUMENTS",
        lambda: parse_function_call(
            _call(
                "aisdlc_v1_operation_start",
                "call-forged",
                {
                    "api_version": API_VERSION,
                    "feature_id": "F-TEST-0001",
                    "expected_feature_revision": 12,
                    "mode": "AUTO",
                    "repository": "attacker/repo",
                },
            )
        ),
    )

    # Trusted authority inputs are not model fields on any Responses function.
    # Directly lock the Plan-listed selector classes instead of relying only on
    # the generic additionalProperties assertion above.
    for index, forbidden in enumerate(
        (
            "target_ref",
            "store_repository",
            "state_ref",
            "principal",
            "policy",
            "adapter_id",
            "trusted_context",
        )
    ):
        _expect_protocol(
            "SCHEMA_INVALID_ARGUMENTS",
            lambda forbidden=forbidden, index=index: parse_function_call(
                _call(
                    "aisdlc_v1_system_capabilities",
                    f"call-forged-trusted-{index}",
                    {"api_version": API_VERSION, forbidden: "model-controlled"},
                )
            ),
        )

    forged_field = _call(
        "aisdlc_v1_system_capabilities",
        "call-extra",
        {"api_version": API_VERSION},
    )
    forged_field["provider_authority"] = "forged"
    _expect_protocol("UNKNOWN_PROVIDER_FIELD", lambda: parse_function_call(forged_field))
    _expect_protocol(
        "INCOMPLETE_FUNCTION_CALL",
        lambda: parse_function_call(
            _call(
                "aisdlc_v1_system_capabilities",
                "call-incomplete",
                {"api_version": API_VERSION},
                status="in_progress",
            )
        ),
    )

    first = _call(
        "aisdlc_v1_system_capabilities", "call-1", {"api_version": API_VERSION}
    )
    second = _call(
        "aisdlc_v1_decision_list", "call-2", {"api_version": API_VERSION}
    )
    _expect_protocol("MULTIPLE_FUNCTION_CALLS", lambda: collect_function_call([first, second]))
    _expect(collect_function_call([{"type": "message", "content": []}]) is None, "zero function call is allowed")


def validate_streaming() -> None:
    args = json.dumps(
        {"api_version": API_VERSION, "operation_id": "op-123"},
        separators=(",", ":"),
    )
    split = len(args) // 2
    events = [
        {
            "type": "response.output_item.added",
            "response_id": "resp-1",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc-stream",
                "call_id": "call-stream",
                "name": "aisdlc_v1_operation_status",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": "resp-1",
            "item_id": "fc-stream",
            "output_index": 0,
            "delta": args[:split],
        },
        {
            "type": "response.function_call_arguments.delta",
            "response_id": "resp-1",
            "item_id": "fc-stream",
            "output_index": 0,
            "delta": args[split:],
        },
        {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-1",
            "item_id": "fc-stream",
            "output_index": 0,
            "arguments": args,
        },
        {
            "type": "response.output_item.done",
            "response_id": "resp-1",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc-stream",
                "call_id": "call-stream",
                "name": "aisdlc_v1_operation_status",
                "arguments": args,
                "status": "completed",
            },
        },
    ]
    parsed = collect_stream_function_call(events)
    _expect(parsed is not None and parsed.capability == "operation.status", "terminal streaming call")
    _expect(parsed.arguments["operation_id"] == "op-123", "stream argument collection")

    terminal = collect_function_call([events[-1]["item"]])
    _expect(terminal == parsed, "sync/retrieval terminal and stream collectors normalized differently")

    # response_id and item id are transport correlation only. Even with both
    # present, they cannot substitute for the provider call_id that binds replay.
    no_call_id = copy.deepcopy(events)
    no_call_id[0]["item"].pop("call_id")
    _expect_protocol("INVALID_CALL_ID", lambda: collect_stream_function_call(no_call_id))

    _expect_protocol(
        "INTERRUPTED_FUNCTION_CALL",
        lambda: collect_stream_function_call(events[:-1]),
    )
    conflict = copy.deepcopy(events)
    conflict[2]["item_id"] = "fc-other"
    _expect_protocol("STREAM_BINDING_CONFLICT", lambda: collect_stream_function_call(conflict))


def validate_registration_and_lane_a_replay() -> None:
    registration = _registration()
    try:
        registration.require_feature("F-OTHER")
    except ResponsesAuthorizationError:
        pass
    else:
        raise AssertionError("feature selector outside trusted registration unexpectedly allowed")

    same_call = "call-scope"
    _expect(
        responses_call_key(registration, same_call)
        != responses_call_key(_registration(provider_scope="provider-scope-b"), same_call),
        "provider scope must bind call identity",
    )

    backend = _FeatureStatusBackend()
    journal = _MemoryJournal()
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"feature.status": backend},
        journal=journal,
    )
    item = _call(
        "aisdlc_v1_feature_status",
        "call-lane-a-replay",
        {"api_version": API_VERSION, "feature_id": "F-TEST-0001"},
    )
    first = adapter.invoke_function_call(item)
    second = adapter.invoke_function_call(item)
    _expect(first == second, "Lane-A exact provider retry must replay exact output")
    _expect(backend.calls == 1, "Lane-A exact replay must not perform second semantic dispatch")

    unsupported_backend = _FeatureStatusBackend()
    unsupported_adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"feature.status": unsupported_backend},
        journal=_MemoryJournal(),
    )
    unsupported_output = unsupported_adapter.invoke_function_call(
        _call(
            "aisdlc_v1_feature_status",
            "call-unsupported-version",
            {"api_version": "ai-sdlc.operator/v999", "feature_id": "F-TEST-0001"},
        )
    )
    unsupported_body = json.loads(unsupported_output["output"])
    _expect(unsupported_body["ok"] is False, "unsupported canonical version unexpectedly succeeded")
    _expect(
        unsupported_body["error"]["code"] == "UNSUPPORTED_API_VERSION",
        "unsupported version did not use canonical version handling",
    )
    _expect(unsupported_backend.calls == 0, "unsupported canonical version reached semantic backend work")


def validate_durable_store_journal() -> None:
    registration = _registration()
    backend = _FeatureStatusBackend()
    store_backend, runtime = _test_store_runtime()
    journal = StoreResponsesCallJournal(runtime)
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"feature.status": backend},
        journal=journal,
    )
    item = _call(
        "aisdlc_v1_feature_status",
        "call-durable-replay",
        {"api_version": API_VERSION, "feature_id": "F-TEST-0001"},
    )
    call_key = responses_call_key(registration, "call-durable-replay")

    first = adapter.invoke_function_call(item)
    first_ref = store_backend.read_snapshot().ref_sha
    _expect(first_ref is not None, "durable journal did not commit protected Store state")
    snapshot = store_backend.read_snapshot()
    binding = snapshot.get(call_binding_path(call_key))
    result = snapshot.get(call_result_path(call_key))
    _expect(binding is not None and binding["schema_version"] == CALL_BINDING_SCHEMA, "durable binding missing")
    _expect(result is not None and result["schema_version"] == CALL_RESULT_SCHEMA, "durable result missing")
    _expect(binding["call_key"] == call_key and result["call_key"] == call_key, "journal call binding")

    second = adapter.invoke_function_call(item)
    second_ref = store_backend.read_snapshot().ref_sha
    _expect(first == second, "durable exact retry must reproduce same function_call_output")
    _expect(second_ref == first_ref, "durable exact replay must not manufacture an empty Store CAS commit")
    _expect(backend.calls == 1, "durable exact replay must not perform a second canonical dispatch")

    fresh_journal = StoreResponsesCallJournal(runtime)
    fresh_adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"feature.status": backend},
        journal=fresh_journal,
    )
    third = fresh_adapter.invoke_function_call(item)
    _expect(third == first, "fresh-process journal reconstruction must preserve provider output replay")
    _expect(store_backend.read_snapshot().ref_sha == first_ref, "fresh replay must remain read-only")
    _expect(backend.calls == 1, "fresh-process replay must not repeat semantic dispatch")

    conflicting = _call(
        "aisdlc_v1_feature_status",
        "call-durable-replay",
        {"api_version": API_VERSION, "feature_id": "F-TEST-0002"},
    )
    try:
        fresh_adapter.invoke_function_call(conflicting)
    except ResponsesCallConflict:
        pass
    else:
        raise AssertionError("conflicting durable call_id reuse unexpectedly dispatched")
    _expect(backend.calls == 1, "conflicting durable call reuse must fail before semantic dispatch")

    unbound_key = "f" * 64
    try:
        journal.record_result(
            call_key=unbound_key,
            result={
                "schema_version": CALL_RESULT_SCHEMA,
                "call_key": unbound_key,
                "function_call_output": {"type": "function_call_output", "call_id": "x", "output": "{}"},
            },
        )
    except ResponsesJournalError:
        pass
    else:
        raise AssertionError("durable result without call binding unexpectedly accepted")


def validate_schema_objects() -> None:
    for name, schema in TOOL_PARAMETER_SCHEMAS.items():
        _expect(schema["type"] == "object", f"{name}: object schema")
        _expect(schema["additionalProperties"] is False, f"{name}: closed schema")
        _expect(set(schema["required"]) == set(schema["properties"]), f"{name}: strict required set")


def main() -> None:
    validate_registry()
    validate_schema_objects()
    validate_terminal_protocol()
    validate_streaming()
    validate_registration_and_lane_a_replay()
    validate_durable_store_journal()
    print("OpenAI Responses adapter WU1-WU3 validation passed")
    print("- unknown tool, malformed JSON and missing/invalid call_id fail closed")
    print("- trusted ref/Store/principal/policy/adapter/context fields cannot be injected")
    print("- sync/retrieval terminal and stream collectors normalize the same call identically")
    print("- response_id/item_id never substitute for call_id")
    print("- unsupported canonical API version returns canonical error with zero backend work")


if __name__ == "__main__":
    main()
