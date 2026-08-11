#!/usr/bin/env python3
"""Reusable transport-neutral conformance harness for ai-sdlc.operator/v1.

The adapters in this module are test doubles only. They are not supported v0.3
release adapters and must not be used as release-readiness evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import json
from typing import Any, Protocol, runtime_checkable

from operator_api import API_VERSION, REGISTRY, dispatch

FROZEN_CONFORMANCE_SUBSET = (
    "system.capabilities",
    "feature.status",
    "operator.inbox",
    "operation.status",
    "decision.list",
    "notification.list",
)

_FIXTURE_RESULTS = {
    "feature.status": {
        "feature_id": "F-CONFORMANCE-0001",
        "revision": 7,
        "workflow_status": "ACTIVE",
        "current_stage": "implementation",
    },
    "operator.inbox": {"operations": [], "decisions": [], "notifications": []},
    "operation.status": {"operation_id": "op-conformance-1", "generation": 2, "status": "BLOCKED"},
    "decision.list": {"decisions": []},
    "notification.list": {"notifications": []},
}

_TRUSTED_CONTEXT = {
    "trusted_identity": {
        "service_id": "conformance-runtime",
        "runtime_id": "fixture-runtime",
        "authorization_context": "fixture-policy",
    }
}


@runtime_checkable
class CanonicalAdapter(Protocol):
    """Minimal transport-neutral adapter boundary consumed by the harness."""

    adapter_id: str
    transport_kind: str

    def invoke(self, canonical_request: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class AdapterEvidence:
    adapter_id: str
    transport_kind: str
    implementation_type: str
    root_implementation_type: str
    wrapper_depth: int


@dataclass(frozen=True)
class ConformanceReport:
    adapter: AdapterEvidence
    exercised_capabilities: tuple[str, ...]
    semantic_signature: tuple[tuple[str, bool, str], ...]


class _SemanticFixtureBackend:
    """Trusted test backend that fails if adapter/trusted identity is not preserved."""

    def __init__(self, result: dict[str, Any], expected_adapter_id: str):
        self.result = copy.deepcopy(result)
        self.expected_adapter_id = expected_adapter_id
        self.calls = 0

    def availability(self, capability: str, trusted_context: dict[str, Any]):
        trusted = trusted_context.get("trusted_identity") or {}
        if trusted.get("service_id") != "conformance-runtime":
            raise AssertionError("trusted runtime identity was not propagated")
        return True, "AVAILABLE"

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        self.calls += 1
        if request["client_identity"]["adapter_id"] != self.expected_adapter_id:
            raise AssertionError("adapter identity was not preserved")
        trusted = trusted_context.get("trusted_identity") or {}
        if trusted.get("authorization_context") != "fixture-policy":
            raise AssertionError("trusted authorization context was not propagated")
        return copy.deepcopy(self.result)


def _fixture_backends(adapter_id: str):
    return {
        capability: _SemanticFixtureBackend(result, adapter_id)
        for capability, result in _FIXTURE_RESULTS.items()
    }


class DirectFixtureAdapter:
    """Test-only adapter using an in-process canonical-object transport boundary."""

    adapter_id = "fixture.direct"
    transport_kind = "in-process-object"

    def __init__(self):
        self._backends = _fixture_backends(self.adapter_id)

    def invoke(self, canonical_request: dict[str, Any]) -> dict[str, Any]:
        return dispatch(
            copy.deepcopy(canonical_request),
            trusted_context=copy.deepcopy(_TRUSTED_CONTEXT),
            backends=self._backends,
        )


class JsonRoundTripFixtureAdapter:
    """Test-only adapter crossing a JSON serialization/deserialization boundary."""

    adapter_id = "fixture.json-roundtrip"
    transport_kind = "json-round-trip"

    def __init__(self):
        self._backends = _fixture_backends(self.adapter_id)

    def invoke(self, canonical_request: dict[str, Any]) -> dict[str, Any]:
        encoded_request = json.dumps(canonical_request, sort_keys=True, separators=(",", ":"))
        decoded_request = json.loads(encoded_request)
        response = dispatch(
            decoded_request,
            trusted_context=json.loads(json.dumps(_TRUSTED_CONTEXT, sort_keys=True)),
            backends=self._backends,
        )
        return json.loads(json.dumps(response, sort_keys=True, separators=(",", ":")))


class AliasFixtureAdapter:
    """Intentional thin wrapper used to prove aliases cannot count as independence."""

    def __init__(self, delegate: CanonicalAdapter):
        self.conformance_delegate = delegate
        self.adapter_id = delegate.adapter_id
        self.transport_kind = delegate.transport_kind

    def invoke(self, canonical_request: dict[str, Any]) -> dict[str, Any]:
        return self.conformance_delegate.invoke(canonical_request)


def _qualified_type(value: object) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def adapter_evidence(adapter: CanonicalAdapter) -> AdapterEvidence:
    if not isinstance(adapter, CanonicalAdapter):
        raise AssertionError("adapter does not implement the canonical conformance boundary")
    root: object = adapter
    seen: set[int] = set()
    wrapper_depth = 0
    while hasattr(root, "conformance_delegate"):
        marker = id(root)
        if marker in seen:
            raise AssertionError("adapter delegate cycle")
        seen.add(marker)
        root = getattr(root, "conformance_delegate")
        wrapper_depth += 1
    return AdapterEvidence(
        adapter_id=adapter.adapter_id,
        transport_kind=adapter.transport_kind,
        implementation_type=_qualified_type(adapter),
        root_implementation_type=_qualified_type(root),
        wrapper_depth=wrapper_depth,
    )


def assert_materially_independent(first: CanonicalAdapter, second: CanonicalAdapter) -> tuple[AdapterEvidence, AdapterEvidence]:
    """Reject aliases/thin wrappers and same-transport evidence as independent."""

    left = adapter_evidence(first)
    right = adapter_evidence(second)
    if left.wrapper_depth or right.wrapper_depth:
        raise AssertionError("thin wrapper/alias cannot count as independent adapter evidence")
    if left.root_implementation_type == right.root_implementation_type:
        raise AssertionError("adapter implementations share the same root implementation")
    if left.transport_kind == right.transport_kind:
        raise AssertionError("adapter implementations share the same transport boundary")
    if left.adapter_id == right.adapter_id:
        raise AssertionError("adapter identities must be distinct")
    return left, right


def _request(adapter: CanonicalAdapter, capability: str, *, request_id: str, api_version: str = API_VERSION):
    request = {
        "api_version": api_version,
        "request_id": request_id,
        "capability": capability,
        "target": {"repository": "DREAM-XIN/fixture", "feature_id": "F-CONFORMANCE-0001"},
        "client_identity": {"adapter_id": adapter.adapter_id, "human_principal": "fixture-user"},
        "payload": {},
    }
    if capability == "operation.status":
        request["context"] = {"operation_id": "op-conformance-1"}
    return request


def _assert_error(response: dict[str, Any], code: str):
    assert response["ok"] is False, response
    assert response["error"]["code"] == code, response


def run_conformance_suite(adapter: CanonicalAdapter) -> ConformanceReport:
    """Run one shared canonical semantic suite through an adapter boundary."""

    evidence = adapter_evidence(adapter)
    assert evidence.wrapper_depth == 0, "aliases are not standalone conformance adapters"
    assert tuple(item.id for item in REGISTRY.values() if item.conformance_subset) == FROZEN_CONFORMANCE_SUBSET

    signature: list[tuple[str, bool, str]] = []

    discovery = adapter.invoke(_request(adapter, "system.capabilities", request_id="conf-discovery"))
    assert discovery["ok"] is True, discovery
    rows = {row["id"]: row for row in discovery["result"]["capabilities"]}
    assert set(rows) == set(REGISTRY)
    for capability in FROZEN_CONFORMANCE_SUBSET:
        assert rows[capability]["available"] is True, (capability, rows[capability])
    assert rows["project.inspect"]["available"] is False
    signature.append(("system.capabilities", True, "OK"))

    for index, capability in enumerate(FROZEN_CONFORMANCE_SUBSET[1:], start=1):
        response = adapter.invoke(_request(adapter, capability, request_id=f"conf-read-{index}"))
        assert response["ok"] is True, (capability, response)
        assert response["result"] == _FIXTURE_RESULTS[capability], (capability, response)
        signature.append((capability, True, "OK"))

    unsupported = _request(adapter, "feature.status", request_id="conf-version", api_version="ai-sdlc.operator/v999")
    response = adapter.invoke(unsupported)
    _assert_error(response, "UNSUPPORTED_API_VERSION")
    signature.append(("unsupported-version", False, "UNSUPPORTED_API_VERSION"))

    response = adapter.invoke(_request(adapter, "not.real", request_id="conf-unknown"))
    _assert_error(response, "INVALID_REQUEST")
    signature.append(("unknown-capability", False, "INVALID_REQUEST"))

    response = adapter.invoke(_request(adapter, "project.inspect", request_id="conf-unavailable"))
    _assert_error(response, "CAPABILITY_UNAVAILABLE")
    signature.append(("known-unavailable", False, "CAPABILITY_UNAVAILABLE"))

    injected = _request(adapter, "feature.status", request_id="conf-identity-injection")
    injected["trusted_identity"] = {"service_id": "evil"}
    response = adapter.invoke(injected)
    _assert_error(response, "INVALID_REQUEST")
    signature.append(("trusted-identity-injection", False, "INVALID_REQUEST"))

    return ConformanceReport(
        adapter=evidence,
        exercised_capabilities=FROZEN_CONFORMANCE_SUBSET,
        semantic_signature=tuple(signature),
    )
