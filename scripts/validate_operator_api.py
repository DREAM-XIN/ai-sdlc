#!/usr/bin/env python3
"""Deterministic validation for ai-sdlc.operator/v1."""
from pathlib import Path
import copy
import json
import sys
from jsonschema import Draft202012Validator
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from operator_api import API_VERSION, CAPABILITIES, REGISTRY, dispatch, registry_errors
from operator_conformance import (
    AliasFixtureAdapter,
    DirectFixtureAdapter,
    FROZEN_CONFORMANCE_SUBSET,
    JsonRoundTripFixtureAdapter,
    assert_materially_independent,
    run_conformance_suite,
)
SCHEMA_ROOT = ROOT / "spec" / "operator"
EXPECTED = {
"system.capabilities": ("read", False, False, True),
"project.inspect": ("read", False, False, False),
"feature.status": ("read", False, False, True),
"operator.inbox": ("read", False, False, True),
"operation.start": ("write", True, True, False),
"operation.status": ("read", False, False, True),
"operation.resume": ("write", True, True, False),
"operation.cancel": ("write", True, False, False),
"decision.list": ("read", False, False, True),
"decision.respond": ("write", True, False, False),
"notification.list": ("read", False, False, True),
"notification.ack": ("write", True, False, False),
}
class CounterBackend:
    def __init__(self, result, fail=None):
        self.calls = 0; self.result = result; self.fail = fail
    def availability(self, capability, trusted_context): return True, "AVAILABLE"
    def invoke(self, request, trusted_context):
        self.calls += 1
        if self.fail: raise RuntimeError(self.fail)
        return copy.deepcopy(self.result)
class AvailabilityBackend(CounterBackend):
    def __init__(self, result, *, available=False, reason="BACKEND_NOT_CONFIGURED"):
        super().__init__(result); self.available = available; self.reason = reason
    def availability(self, capability, trusted_context): return self.available, self.reason
def req(capability, **extra):
    body = {"api_version": API_VERSION, "request_id": "req-1", "capability": capability, "client_identity": {"adapter_id": "fixture-adapter"}, "payload": {}}
    body.update(extra); return body
def assert_code(response, code):
    assert response["ok"] is False, response
    assert response["error"]["code"] == code, response
def main():
    problems = registry_errors(); assert not problems, problems
    for path in SCHEMA_ROOT.rglob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
    assert len(list((SCHEMA_ROOT / "capabilities").glob("*.request.schema.json"))) == 12
    assert len(list((SCHEMA_ROOT / "capabilities").glob("*.response.schema.json"))) == 12
    assert set(REGISTRY) == set(EXPECTED)
    for item in CAPABILITIES:
        kind, idem, rev, conf = EXPECTED[item.id]
        assert (item.kind,item.requires_idempotency,item.requires_expected_feature_revision,item.conformance_subset) == (kind,idem,rev,conf)
    assert tuple(item.id for item in CAPABILITIES if item.conformance_subset) == FROZEN_CONFORMANCE_SUBSET
    probe = CounterBackend({})
    bad = req("system.capabilities"); bad["api_version"] = "ai-sdlc.operator/v999"
    assert_code(dispatch(bad, backends={"system.capabilities": probe}), "UNSUPPORTED_API_VERSION"); assert probe.calls == 0
    assert_code(dispatch(req("not.real")), "INVALID_REQUEST")
    assert_code(dispatch(req("feature.status")), "CAPABILITY_UNAVAILABLE")
    bad = req("system.capabilities"); bad["trusted_identity"] = {"service_id": "evil"}
    assert_code(dispatch(bad, backends={"system.capabilities": probe}), "INVALID_REQUEST"); assert probe.calls == 0
    bad = req("system.capabilities"); bad["unexpected"] = True; assert_code(dispatch(bad), "INVALID_REQUEST")
    for cap in ("operation.start","operation.resume","operation.cancel","decision.respond","notification.ack"):
        payload = {"decision_id":"d-1","response":"yes"} if cap == "decision.respond" else ({"notification_id":"n-1"} if cap == "notification.ack" else {})
        body = req(cap, payload=payload); assert_code(dispatch(body), "INVALID_REQUEST"); body["idempotency_key"] = "idem-1"
        if cap in ("operation.start","operation.resume"):
            assert_code(dispatch(body), "INVALID_REQUEST"); body["context"] = {"expected_feature_revision": 10}
        assert_code(dispatch(body), "CAPABILITY_UNAVAILABLE")
    result = dispatch(req("system.capabilities")); assert result["ok"] is True
    canonical_discovery = copy.deepcopy(result["result"])
    rows = {row["id"]: row for row in canonical_discovery["capabilities"]}; assert set(rows) == set(EXPECTED)
    assert rows["system.capabilities"]["available"] is True
    assert all(not row["available"] for key,row in rows.items() if key != "system.capabilities")
    malformed = copy.deepcopy(canonical_discovery); malformed["capabilities"].pop()
    assert_code(dispatch(req("system.capabilities"), backends={"system.capabilities": CounterBackend(malformed)}), "INTERNAL_FAILURE")
    malformed = copy.deepcopy(canonical_discovery); malformed["capabilities"][0]["id"] = "not.real"
    assert_code(dispatch(req("system.capabilities"), backends={"system.capabilities": CounterBackend(malformed)}), "INTERNAL_FAILURE")
    malformed = copy.deepcopy(canonical_discovery); malformed["capabilities"][-1]["id"] = malformed["capabilities"][0]["id"]
    assert_code(dispatch(req("system.capabilities"), backends={"system.capabilities": CounterBackend(malformed)}), "INTERNAL_FAILURE")
    malformed = copy.deepcopy(canonical_discovery); del malformed["capabilities"][0]["available"]
    assert_code(dispatch(req("system.capabilities"), backends={"system.capabilities": CounterBackend(malformed)}), "INTERNAL_FAILURE")
    malformed = copy.deepcopy(canonical_discovery); malformed["capabilities"][0]["reason"] = "Bearer TOP-SECRET token"
    result = dispatch(req("system.capabilities"), backends={"system.capabilities": CounterBackend(malformed)}); assert_code(result, "INTERNAL_FAILURE")
    assert "TOP-SECRET" not in json.dumps(result)
    status_result = {"feature_id":"F-X","revision":1,"workflow_status":"ACTIVE","current_stage":"implementation"}
    backend = CounterBackend(status_result); result = dispatch(req("feature.status"), backends={"feature.status": backend}); assert result["ok"] is True and backend.calls == 1
    unsafe_unavailable = AvailabilityBackend(status_result, available=False, reason="Bearer TOP-SECRET token password=abc")
    result = dispatch(req("feature.status"), backends={"feature.status": unsafe_unavailable}); assert_code(result, "CAPABILITY_UNAVAILABLE")
    assert result["error"]["details"]["reason"] == "BACKEND_NOT_CONFIGURED" and "TOP-SECRET" not in json.dumps(result)
    result = dispatch(req("system.capabilities"), backends={"feature.status": unsafe_unavailable}); assert result["ok"] is True
    discovery_rows = {row["id"]: row for row in result["result"]["capabilities"]}
    assert discovery_rows["feature.status"]["reason"] == "BACKEND_NOT_CONFIGURED" and "TOP-SECRET" not in json.dumps(result)
    secret_backend = CounterBackend(status_result, fail="Bearer TOP-SECRET token password=abc")
    result = dispatch(req("feature.status"), backends={"feature.status": secret_backend}); assert_code(result, "INTERNAL_FAILURE")
    rendered = json.dumps(result); assert "TOP-SECRET" not in rendered and "password=abc" not in rendered
    prohibited = {"shell.exec","manifest.patch","feature.event","gate.pass","repo.write","merge","release"}; assert prohibited.isdisjoint(REGISTRY)

    direct = DirectFixtureAdapter()
    json_roundtrip = JsonRoundTripFixtureAdapter()
    direct_report = run_conformance_suite(direct)
    json_report = run_conformance_suite(json_roundtrip)
    assert direct_report.semantic_signature == json_report.semantic_signature
    assert direct_report.exercised_capabilities == FROZEN_CONFORMANCE_SUBSET
    assert json_report.exercised_capabilities == FROZEN_CONFORMANCE_SUBSET
    direct_evidence, json_evidence = assert_materially_independent(direct, json_roundtrip)
    alias = AliasFixtureAdapter(direct)
    try:
        assert_materially_independent(direct, alias)
    except AssertionError as exc:
        assert "wrapper/alias" in str(exc)
    else:
        raise AssertionError("thin wrapper incorrectly counted as independent adapter evidence")
    try:
        run_conformance_suite(alias)
    except AssertionError as exc:
        assert "aliases" in str(exc)
    else:
        raise AssertionError("alias fixture incorrectly accepted by standalone conformance suite")

    print("Operator API validation passed")
    print(f"- api_version: {API_VERSION}")
    print(f"- capabilities: {len(CAPABILITIES)}")
    print("- default_available: system.capabilities")
    print("- capability discovery: strict exact-vocabulary schema + bounded availability reasons")
    print(f"- conformance subset: {len(FROZEN_CONFORMANCE_SUBSET)} shared semantics through 2 fixture adapters")
    print(f"- adapter evidence: {direct_evidence.transport_kind} != {json_evidence.transport_kind}; alias/thin-wrapper rejected")
if __name__ == "__main__": main()
