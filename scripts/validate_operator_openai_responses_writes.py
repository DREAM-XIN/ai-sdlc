#!/usr/bin/env python3
"""Deterministic write-slice validation for the Responses adapter."""
from __future__ import annotations

import json

from operator_api import API_VERSION
from operator_openai_responses import (
    ADAPTER_ID,
    OpenAIResponsesOperatorAdapter,
    ResponsesProtocolError,
    TrustedResponsesRegistration,
    parse_function_call,
    responses_call_key,
)
from operator_openai_responses_journal import StoreResponsesCallJournal, call_binding_path
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from validate_operator_openai_responses_crash_recovery import main as validate_crash_recovery

REPO = "DREAM-XIN/write-fixture"
FEATURE = "F-WRITE-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _WriteBackend:
    def __init__(self, capability):
        self.capability = capability
        self.calls = []

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        self.calls.append(request)
        _expect(request["client_identity"]["adapter_id"] == ADAPTER_ID, "fixed write adapter identity")
        _expect(request["idempotency_key"].startswith("openai-responses/"), "server-derived write idempotency")
        if self.capability == "operation.start":
            _expect(request["target"] == {"repository": REPO, "feature_id": FEATURE}, "start target")
            _expect(request["context"] == {"expected_feature_revision": 19}, "start expected revision")
            _expect(request["payload"] == {"mode": "ASSISTED"}, "start payload")
            return {"operation_id": "op-write-1", "generation": 0, "status": "RUNNING"}
        if self.capability == "operation.cancel":
            _expect(request["context"] == {"operation_id": "op-write-1"}, "cancel context")
            _expect(request["payload"] == {"reason": "operator requested cancellation"}, "cancel payload")
            return {"operation_id": "op-write-1", "status": "CANCELLED"}
        if self.capability == "decision.respond":
            _expect(
                request["payload"] == {"decision_id": "decision-write-1", "response": "approve"},
                "decision payload",
            )
            return {"decision_id": "decision-write-1", "status": "RESPONDED"}
        if self.capability == "notification.ack":
            _expect(request["payload"] == {"notification_id": "notification-write-1"}, "notification payload")
            return {"notification_id": "notification-write-1", "status": "ACKNOWLEDGED"}
        raise AssertionError(self.capability)


def _call(name, call_id, arguments):
    return {
        "type": "function_call",
        "id": f"fc-{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
        "status": "completed",
    }


def _runtime():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T11:18:00Z",
    )
    return backend, runtime


def _registration():
    return TrustedResponsesRegistration(
        registration_id="write-registration",
        provider_scope_id="write-provider-scope",
        target_repository=REPO,
        feature_refs={FEATURE: "refs/heads/feature/F-WRITE-0001"},
        trusted_context={
            "trusted_identity": {
                "service_id": "write-service",
                "runtime_id": "write-runtime",
                "authorization_context": "write-policy",
            },
            "trusted_scope": {"repositories": [REPO], "feature_ids": [FEATURE]},
            "trusted_principal": "write-principal",
        },
        human_principal="write-principal",
    )


def _decode(output):
    _expect(output["type"] == "function_call_output", "write output type")
    return json.loads(output["output"])


def main() -> None:
    store_backend, runtime = _runtime()
    registration = _registration()
    backends = {
        capability: _WriteBackend(capability)
        for capability in (
            "operation.start",
            "operation.cancel",
            "decision.respond",
            "notification.ack",
        )
    }
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends=backends,
        journal=StoreResponsesCallJournal(runtime),
    )

    cases = [
        (
            "aisdlc_v1_operation_start",
            "call-write-start",
            {
                "api_version": API_VERSION,
                "feature_id": FEATURE,
                "expected_feature_revision": 19,
                "mode": "ASSISTED",
            },
            "operation.start",
            {"operation_id": "op-write-1", "generation": 0, "status": "RUNNING"},
        ),
        (
            "aisdlc_v1_operation_cancel",
            "call-write-cancel",
            {
                "api_version": API_VERSION,
                "operation_id": "op-write-1",
                "reason": "operator requested cancellation",
            },
            "operation.cancel",
            {"operation_id": "op-write-1", "status": "CANCELLED"},
        ),
        (
            "aisdlc_v1_decision_respond",
            "call-write-decision",
            {
                "api_version": API_VERSION,
                "decision_id": "decision-write-1",
                "response": "approve",
            },
            "decision.respond",
            {"decision_id": "decision-write-1", "status": "RESPONDED"},
        ),
        (
            "aisdlc_v1_notification_ack",
            "call-write-notification",
            {"api_version": API_VERSION, "notification_id": "notification-write-1"},
            "notification.ack",
            {"notification_id": "notification-write-1", "status": "ACKNOWLEDGED"},
        ),
    ]

    for tool_name, call_id, arguments, capability, expected_result in cases:
        item = _call(tool_name, call_id, arguments)
        first = adapter.invoke_function_call(item)
        ref_after_first = store_backend.read_snapshot().ref_sha
        second = adapter.invoke_function_call(item)
        _expect(first == second, f"{capability}: exact replay output")
        _expect(store_backend.read_snapshot().ref_sha == ref_after_first, f"{capability}: replay must be read-only")
        _expect(len(backends[capability].calls) == 1, f"{capability}: replay semantic dispatch count")
        body = _decode(first)
        _expect(body["ok"] is True and body["result"] == expected_result, f"{capability}: canonical response")

        call_key = responses_call_key(registration, call_id)
        binding = store_backend.read_snapshot().get(call_binding_path(call_key))
        _expect(binding is not None, f"{capability}: durable call binding")
        canonical = binding["canonical_request"]
        _expect(canonical["capability"] == capability, f"{capability}: fixed canonical mapping")
        _expect("idempotency_key" in canonical, f"{capability}: write idempotency missing")
        _expect("trusted_context" not in binding, f"{capability}: trusted context leaked into transport journal")

    forged = _call(
        "aisdlc_v1_operation_start",
        "call-forged-idempotency",
        {
            "api_version": API_VERSION,
            "feature_id": FEATURE,
            "expected_feature_revision": 19,
            "mode": "AUTO",
            "idempotency_key": "model-controlled",
        },
    )
    try:
        parse_function_call(forged)
    except ResponsesProtocolError as exc:
        _expect(exc.code == "SCHEMA_INVALID_ARGUMENTS", "model-controlled idempotency rejection code")
    else:
        raise AssertionError("model-controlled idempotency unexpectedly accepted")

    resume = _call(
        "aisdlc_v1_operation_resume",
        "call-resume",
        {"api_version": API_VERSION},
    )
    try:
        parse_function_call(resume)
    except ResponsesProtocolError as exc:
        _expect(exc.code == "UNKNOWN_TOOL", "operation.resume must fail as unknown Responses tool")
    else:
        raise AssertionError("operation.resume unexpectedly became model-invokable")

    _expect(sum(len(backend.calls) for backend in backends.values()) == 4, "exact four write semantic invocations")

    # WU3 recovery is part of the authoritative write validator so both the
    # dedicated Responses workflow and scripts/validate.py exercise it.
    validate_crash_recovery()

    print("OpenAI Responses exact write-slice validation passed")
    print("- start/cancel/respond/ack map to canonical envelopes with server-derived idempotency")
    print("- exact call replay is durable and read-only")
    print("- crash-after-canonical-write recovery converges through one semantic write")
    print("- model-controlled idempotency and operation.resume fail before backend dispatch")


if __name__ == "__main__":
    main()
