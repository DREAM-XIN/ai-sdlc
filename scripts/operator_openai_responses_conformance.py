#!/usr/bin/env python3
"""Independent OpenAI Responses conformance driver.

Provider-side fixtures are Responses-shaped. They always cross the production
Responses parser, call journal, canonical request builder and output encoder.
Lane-A construction below uses deterministic trusted backend doubles only after
that boundary and therefore cannot establish Supported production status.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from operator_api import API_VERSION, REGISTRY
from operator_openai_responses import (
    ADAPTER_ID,
    TOOL_CAPABILITIES,
    OpenAIResponsesOperatorAdapter,
    ResponsesProtocolError,
    TrustedResponsesRegistration,
)
from operator_openai_responses_journal import StoreResponsesCallJournal
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier

TRANSPORT_KIND = "openai-responses-function-tools"
_FIXTURE_REPOSITORY = "DREAM-XIN/fixture"
_FIXTURE_FEATURE = "F-CONFORMANCE-0001"
_FIXTURE_STATE_REF = "refs/heads/ai-sdlc-operator-state"
_ALLOWED_CANONICAL_FIELDS = frozenset(
    {
        "api_version",
        "request_id",
        "capability",
        "target",
        "client_identity",
        "context",
        "payload",
        "idempotency_key",
    }
)
_TOOL_BY_CAPABILITY = {capability: name for name, capability in TOOL_CAPABILITIES.items()}

_FIXTURE_RESULTS = {
    "feature.status": {
        "feature_id": _FIXTURE_FEATURE,
        "revision": 7,
        "workflow_status": "ACTIVE",
        "current_stage": "implementation",
    },
    "operator.inbox": {"operations": [], "decisions": [], "notifications": []},
    "operation.status": {
        "operation_id": "op-conformance-1",
        "generation": 2,
        "status": "BLOCKED",
    },
    "decision.list": {"decisions": []},
    "notification.list": {"notifications": []},
}


class _ResponsesSemanticFixtureBackend:
    """Lane-A backend double after the genuine Responses translation boundary."""

    test_only = True

    def __init__(self, capability: str, result: dict[str, Any]):
        self.capability = capability
        self.result = copy.deepcopy(result)
        self.calls = 0

    def availability(self, capability: str, trusted_context: dict[str, Any]):
        trusted = trusted_context.get("trusted_identity") or {}
        if trusted.get("service_id") != "conformance-runtime":
            raise AssertionError("Responses conformance lost trusted runtime identity")
        return True, "AVAILABLE"

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        self.calls += 1
        if request["client_identity"]["adapter_id"] != ADAPTER_ID:
            raise AssertionError("Responses conformance lost fixed adapter identity")
        trusted = trusted_context.get("trusted_identity") or {}
        if trusted.get("authorization_context") != "fixture-policy":
            raise AssertionError("Responses conformance lost trusted authorization context")
        if self.capability == "feature.status":
            target = request.get("target") or {}
            if target.get("repository") != _FIXTURE_REPOSITORY or target.get("feature_id") != _FIXTURE_FEATURE:
                raise AssertionError("Responses feature.status canonical target mismatch")
        if self.capability == "operation.status":
            if (request.get("context") or {}).get("operation_id") != "op-conformance-1":
                raise AssertionError("Responses operation.status canonical context mismatch")
        return copy.deepcopy(self.result)


def _error(request: Mapping[str, Any], code: str, message: str) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "request_id": str(request.get("request_id") or "invalid"),
        "capability": str(request.get("capability") or "unknown"),
        "ok": False,
        "error": {"code": code, "message": message[:512]},
    }


def _provider_call_id(canonical_request: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(canonical_request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "conf-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:40]


def _tool_arguments(canonical_request: Mapping[str, Any], capability: str) -> dict[str, Any]:
    args: dict[str, Any] = {"api_version": canonical_request.get("api_version")}
    if capability == "feature.status":
        args["feature_id"] = (canonical_request.get("target") or {}).get("feature_id")
    elif capability == "operation.status":
        args["operation_id"] = (canonical_request.get("context") or {}).get("operation_id")

    # The transport-neutral harness deliberately probes forged trusted top-level
    # authority. Turn any non-canonical field into an extra model argument so the
    # production strict Responses schema rejects it before canonical dispatch.
    if set(canonical_request) - _ALLOWED_CANONICAL_FIELDS:
        args["__canonical_injection__"] = "rejected-by-strict-responses-schema"
    return args


class OpenAIResponsesConformanceAdapter:
    """Canonical-harness driver that enters through Responses-shaped provider calls."""

    adapter_id = ADAPTER_ID
    transport_kind = TRANSPORT_KIND

    def __init__(self, operator_adapter: OpenAIResponsesOperatorAdapter):
        if not isinstance(operator_adapter, OpenAIResponsesOperatorAdapter):
            raise ValueError("Responses conformance requires the production Responses adapter boundary")
        self.operator_adapter = operator_adapter

    def invoke(self, canonical_request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(canonical_request, dict):
            return _error({}, "INVALID_REQUEST", "conformance request must be an object")
        capability = canonical_request.get("capability")
        if not isinstance(capability, str):
            return _error(canonical_request, "INVALID_REQUEST", "capability is required")

        tool_name = _TOOL_BY_CAPABILITY.get(capability)
        if tool_name is None:
            if capability in REGISTRY:
                return _error(
                    canonical_request,
                    "CAPABILITY_UNAVAILABLE",
                    "capability is intentionally absent from the Responses tool surface",
                )
            # Exercise the real production parser for an unknown model-selected tool.
            tool_name = "aisdlc_v1_unknown_capability"

        call_id = _provider_call_id(canonical_request)
        provider_item = {
            "type": "function_call",
            "id": f"fc-{call_id}",
            "call_id": call_id,
            "name": tool_name,
            "arguments": json.dumps(
                _tool_arguments(canonical_request, capability),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            "status": "completed",
        }
        try:
            output = self.operator_adapter.invoke_function_call(provider_item)
        except ResponsesProtocolError:
            return _error(canonical_request, "INVALID_REQUEST", "Responses protocol rejected invocation")

        if not isinstance(output, dict) or output.get("type") != "function_call_output" or output.get("call_id") != call_id:
            raise AssertionError("Responses conformance output correlation failed")
        encoded = output.get("output")
        if not isinstance(encoded, str):
            raise AssertionError("Responses function_call_output must carry canonical JSON text")
        try:
            response = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise AssertionError("Responses function_call_output is not canonical JSON") from exc
        if not isinstance(response, dict):
            raise AssertionError("Responses canonical output must decode to an object")
        return response


def build_lane_a_responses_conformance_adapter() -> OpenAIResponsesConformanceAdapter:
    """Construct only the deterministic Lane-A driver; never Supported production evidence."""

    store_backend = MemoryStateRefBackend(
        repository=_FIXTURE_REPOSITORY,
        state_ref=_FIXTURE_STATE_REF,
    )
    store_runtime = OperatorStoreRuntime(
        backend=store_backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T11:03:00Z",
    )
    registration = TrustedResponsesRegistration(
        registration_id="responses-conformance-lane-a",
        provider_scope_id="responses-conformance-provider-scope",
        target_repository=_FIXTURE_REPOSITORY,
        feature_refs={_FIXTURE_FEATURE: "refs/heads/feature/F-CONFORMANCE-0001"},
        trusted_context={
            "trusted_identity": {
                "service_id": "conformance-runtime",
                "runtime_id": "responses-lane-a",
                "authorization_context": "fixture-policy",
            },
            "trusted_scope": {
                "repositories": [_FIXTURE_REPOSITORY],
                "feature_ids": [_FIXTURE_FEATURE],
            },
            "trusted_principal": "fixture-user",
        },
        human_principal="fixture-user",
    )
    backends = {
        capability: _ResponsesSemanticFixtureBackend(capability, result)
        for capability, result in _FIXTURE_RESULTS.items()
    }
    production_boundary = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends=backends,
        journal=StoreResponsesCallJournal(store_runtime),
    )
    driver = OpenAIResponsesConformanceAdapter(production_boundary)
    # Exposed only for deterministic evidence assertions; neither is authority
    # outside this test-only Lane-A construction.
    driver.lane_a_store_backend = store_backend
    driver.lane_a_fixture_backends = backends
    return driver
