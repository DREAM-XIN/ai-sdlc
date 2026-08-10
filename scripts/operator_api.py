#!/usr/bin/env python3
"""Transport-independent ai-sdlc.operator/v1 contract foundation."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "spec" / "operator"
API_VERSION = "ai-sdlc.operator/v1"
ERROR_CODES = ("INVALID_REQUEST","UNSUPPORTED_API_VERSION","CAPABILITY_UNAVAILABLE","UNAUTHORIZED","POLICY_DENIED","STALE_REVISION","ALREADY_CLAIMED","ALREADY_APPLIED","SUPERSEDED_GENERATION","CANCELLED_OPERATION","EXTERNAL_WAIT","NEEDS_USER","BLOCKED","TRANSIENT_FAILURE","INTERNAL_FAILURE")
SAFE_AVAILABILITY_REASONS = frozenset(("AVAILABLE", "BACKEND_NOT_IMPLEMENTED", "BACKEND_NOT_CONFIGURED", "POLICY_RESTRICTED"))

@dataclass(frozen=True)
class Capability:
    id: str
    kind: str
    requires_idempotency: bool
    requires_expected_feature_revision: bool
    backend_key: str
    conformance_subset: bool

CAPABILITIES = (
    Capability("system.capabilities", "read", False, False, "system.capabilities", True),
    Capability("project.inspect", "read", False, False, "project.inspect", False),
    Capability("feature.status", "read", False, False, "feature.status", True),
    Capability("operator.inbox", "read", False, False, "operator.inbox", True),
    Capability("operation.start", "write", True, True, "operation.start", False),
    Capability("operation.status", "read", False, False, "operation.status", True),
    Capability("operation.resume", "write", True, True, "operation.resume", False),
    Capability("operation.cancel", "write", True, False, "operation.cancel", False),
    Capability("decision.list", "read", False, False, "decision.list", True),
    Capability("decision.respond", "write", True, False, "decision.respond", False),
    Capability("notification.list", "read", False, False, "notification.list", True),
    Capability("notification.ack", "write", True, False, "notification.ack", False),
)
REGISTRY = {item.id: item for item in CAPABILITIES}

def _load_schema(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def _errors(instance, path: Path):
    schema = _load_schema(path)
    store = {}
    for candidate in SCHEMA_ROOT.rglob("*.schema.json"):
        loaded = _load_schema(candidate)
        if "$id" in loaded:
            store[loaded["$id"]] = loaded
    resolver = RefResolver.from_schema(schema, store=store)
    problems = []
    for error in Draft202012Validator(schema, resolver=resolver).iter_errors(instance):
        loc = ".".join(str(part) for part in error.absolute_path) or "<root>"
        problems.append(f"{loc}: {error.message}")
    return sorted(problems)

def _payload_schema(capability: str, response=False):
    stem = capability.replace(".", "-")
    suffix = "response" if response else "request"
    return SCHEMA_ROOT / "capabilities" / f"{stem}.{suffix}.schema.json"

def _safe_details(message):
    text = str(message)
    lowered = text.lower()
    if any(token in lowered for token in ("token", "secret", "password", "authorization:", "bearer ")):
        return {"reason": "BACKEND_FAILURE_REDACTED"}
    return {"reason": text[:160]}

def _bounded_availability_reason(available, reason):
    """Normalize backend-owned availability text to the public bounded reason vocabulary."""
    if available:
        return "AVAILABLE"
    if reason in SAFE_AVAILABILITY_REASONS and reason != "AVAILABLE":
        return reason
    return "BACKEND_NOT_CONFIGURED"

def _response(request, *, result=None, code=None, message=None, details=None):
    body = {"api_version": API_VERSION, "request_id": request.get("request_id", "invalid"), "capability": request.get("capability", "unknown"), "ok": code is None}
    if code is None:
        body["result"] = result or {}
    else:
        error = {"code": code}
        if message:
            error["message"] = message[:512]
        if details:
            error["details"] = details
        body["error"] = error
    return body

class UnavailableBackend:
    reason = "BACKEND_NOT_IMPLEMENTED"
    def availability(self, capability, trusted_context):
        return False, self.reason
    def invoke(self, request, trusted_context):
        raise RuntimeError("unavailable backend invoked")

class SystemCapabilitiesBackend:
    def __init__(self, backends):
        self.backends = backends
    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"
    def invoke(self, request, trusted_context):
        rows = []
        for item in CAPABILITIES:
            backend = self.backends.get(item.backend_key)
            if item.id == "system.capabilities":
                available, reason = True, "AVAILABLE"
            elif backend is None:
                available, reason = False, "BACKEND_NOT_IMPLEMENTED"
            else:
                available, reason = backend.availability(item.id, trusted_context)
            rows.append({"id": item.id, "available": bool(available), "reason": _bounded_availability_reason(bool(available), reason)})
        return {"supported_api_versions": [API_VERSION], "capabilities": rows}

def default_backends():
    backends = {}
    backends["system.capabilities"] = SystemCapabilitiesBackend(backends)
    return backends

def registry_errors():
    problems = []
    ids = [item.id for item in CAPABILITIES]
    if len(ids) != len(set(ids)):
        problems.append("duplicate capability id")
    if set(ids) != set(REGISTRY):
        problems.append("registry mismatch")
    if len(ids) != 12:
        problems.append(f"expected 12 capabilities, got {len(ids)}")
    for item in CAPABILITIES:
        if item.kind not in {"read", "write"}:
            problems.append(f"{item.id}: invalid kind")
        if item.kind == "write" and not item.requires_idempotency:
            problems.append(f"{item.id}: semantic write lacks idempotency")
        for response in (False, True):
            if not _payload_schema(item.id, response).exists():
                problems.append(f"{item.id}: missing {'response' if response else 'request'} schema")
    return problems

def dispatch(request, *, trusted_context=None, backends=None):
    trusted_context = dict(trusted_context or {})
    backends = dict(default_backends() if backends is None else backends)
    if "system.capabilities" not in backends:
        backends["system.capabilities"] = SystemCapabilitiesBackend(backends)
    outer_errors = _errors(request, SCHEMA_ROOT / "request-envelope.schema.json")
    if outer_errors:
        return _response(request, code="INVALID_REQUEST", message="request validation failed", details={"errors": outer_errors[:8]})
    if request["api_version"] != API_VERSION:
        return _response(request, code="UNSUPPORTED_API_VERSION", message="unsupported canonical API version")
    capability = REGISTRY.get(request["capability"])
    if capability is None:
        return _response(request, code="INVALID_REQUEST", message="unknown capability")
    payload_errors = _errors(request["payload"], _payload_schema(capability.id))
    if payload_errors:
        return _response(request, code="INVALID_REQUEST", message="payload validation failed", details={"errors": payload_errors[:8]})
    if capability.requires_idempotency and not request.get("idempotency_key"):
        return _response(request, code="INVALID_REQUEST", message="idempotency_key is required")
    context = request.get("context") or {}
    if capability.requires_expected_feature_revision and "expected_feature_revision" not in context:
        return _response(request, code="INVALID_REQUEST", message="expected_feature_revision is required")
    trusted_identity = trusted_context.get("trusted_identity")
    if trusted_identity is not None:
        trusted_schema = _load_schema(SCHEMA_ROOT / "identity-context.schema.json")["$defs"]["trusted_identity"]
        if list(Draft202012Validator(trusted_schema).iter_errors(trusted_identity)):
            return _response(request, code="INTERNAL_FAILURE", message="invalid trusted invocation context")
    validated = dict(request)
    validated["_trusted_context"] = trusted_context
    backend = backends.get(capability.backend_key)
    if backend is None:
        return _response(request, code="CAPABILITY_UNAVAILABLE", message="trusted backend unavailable", details={"reason": "BACKEND_NOT_IMPLEMENTED"})
    try:
        available, reason = backend.availability(capability.id, trusted_context)
        if not available:
            return _response(request, code="CAPABILITY_UNAVAILABLE", message="trusted backend unavailable", details={"reason": _bounded_availability_reason(False, reason)})
        result = backend.invoke(validated, trusted_context)
        result_errors = _errors(result, _payload_schema(capability.id, response=True))
        if result_errors:
            return _response(request, code="INTERNAL_FAILURE", message="backend returned invalid canonical result")
        response = _response(request, result=result)
        if _errors(response, SCHEMA_ROOT / "response-envelope.schema.json"):
            return _response(request, code="INTERNAL_FAILURE", message="canonical response validation failed")
        return response
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in ERROR_CODES:
            return _response(request, code=code, message=str(exc), details=_safe_details(exc))
        return _response(request, code="INTERNAL_FAILURE", message="backend invocation failed", details=_safe_details(exc))
