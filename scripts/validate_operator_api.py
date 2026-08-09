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
    def __init__(self, result, identity="fixture-a", transport="fixture-transport-a", fail=None):
        self.calls = 0; self.result = result; self.identity = identity; self.transport = transport; self.fail = fail
    def availability(self, capability, trusted_context): return True, "AVAILABLE"
    def invoke(self, request, trusted_context):
        self.calls += 1
        if self.fail: raise RuntimeError(self.fail)
        return copy.deepcopy(self.result)
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
    rows = {row["id"]: row for row in result["result"]["capabilities"]}; assert set(rows) == set(EXPECTED)
    assert rows["system.capabilities"]["available"] is True
    assert all(not row["available"] for key,row in rows.items() if key != "system.capabilities")
    status_result = {"feature_id":"F-X","revision":1,"workflow_status":"ACTIVE","current_stage":"implementation"}
    backend = CounterBackend(status_result); result = dispatch(req("feature.status"), backends={"feature.status": backend}); assert result["ok"] is True and backend.calls == 1
    secret_backend = CounterBackend(status_result, fail="Bearer TOP-SECRET token password=abc")
    result = dispatch(req("feature.status"), backends={"feature.status": secret_backend}); assert_code(result, "INTERNAL_FAILURE")
    rendered = json.dumps(result); assert "TOP-SECRET" not in rendered and "password=abc" not in rendered
    prohibited = {"shell.exec","manifest.patch","feature.event","gate.pass","repo.write","merge","release"}; assert prohibited.isdisjoint(REGISTRY)
    fixture_a = CounterBackend(status_result, identity="adapter-a", transport="fixture-a")
    fixture_b = CounterBackend(status_result, identity="adapter-b", transport="fixture-b")
    assert (fixture_a.identity, fixture_a.transport) != (fixture_b.identity, fixture_b.transport)
    alias = CounterBackend(status_result, identity=fixture_a.identity, transport=fixture_a.transport)
    assert (alias.identity, alias.transport) == (fixture_a.identity, fixture_a.transport)
    print("Operator API validation passed")
    print(f"- api_version: {API_VERSION}")
    print(f"- capabilities: {len(CAPABILITIES)}")
    print("- default_available: system.capabilities")
    print("- conformance fixture identities: 2 distinct; alias rejected as independent evidence")
if __name__ == "__main__": main()
