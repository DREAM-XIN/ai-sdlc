#!/usr/bin/env python3
"""OpenAI Responses function-tool adapter foundation for ai-sdlc.operator/v1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from operator_api import API_VERSION, dispatch

ADAPTER_ID = "ai-sdlc.openai.responses"
ADAPTER_PROTOCOL_VERSION = "1"
TRANSPORT_KIND = "openai-responses"
PARALLEL_TOOL_CALLS = False
CALL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

TOOL_CAPABILITIES: dict[str, str] = {
    "aisdlc_v1_system_capabilities": "system.capabilities",
    "aisdlc_v1_feature_status": "feature.status",
    "aisdlc_v1_operator_inbox": "operator.inbox",
    "aisdlc_v1_operation_status": "operation.status",
    "aisdlc_v1_decision_list": "decision.list",
    "aisdlc_v1_notification_list": "notification.list",
    "aisdlc_v1_operation_start": "operation.start",
    "aisdlc_v1_operation_cancel": "operation.cancel",
    "aisdlc_v1_decision_respond": "decision.respond",
    "aisdlc_v1_notification_ack": "notification.ack",
}
WRITE_CAPABILITIES = frozenset(
    {"operation.start", "operation.cancel", "decision.respond", "notification.ack"}
)


def _string(*, max_length: int, pattern: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": max_length}
    if pattern:
        schema["pattern"] = pattern
    return schema


def _nullable_string(*, max_length: int, enum: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": ["string", "null"], "maxLength": max_length}
    if enum:
        schema["enum"] = [*enum, None]
    return schema


def _strict_object(properties: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(properties),
        "additionalProperties": False,
    }


API_VERSION_ARG = _string(max_length=64)
FEATURE_ID_ARG = _string(max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
OPERATION_ID_ARG = _string(max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
DECISION_ID_ARG = _string(max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
NOTIFICATION_ID_ARG = _string(max_length=128, pattern=r"^[A-Za-z0-9._:-]{1,128}$")

TOOL_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "aisdlc_v1_system_capabilities": _strict_object({"api_version": API_VERSION_ARG}),
    "aisdlc_v1_feature_status": _strict_object(
        {"api_version": API_VERSION_ARG, "feature_id": FEATURE_ID_ARG}
    ),
    "aisdlc_v1_operator_inbox": _strict_object({"api_version": API_VERSION_ARG}),
    "aisdlc_v1_operation_status": _strict_object(
        {"api_version": API_VERSION_ARG, "operation_id": OPERATION_ID_ARG}
    ),
    "aisdlc_v1_decision_list": _strict_object({"api_version": API_VERSION_ARG}),
    "aisdlc_v1_notification_list": _strict_object({"api_version": API_VERSION_ARG}),
    "aisdlc_v1_operation_start": _strict_object(
        {
            "api_version": API_VERSION_ARG,
            "feature_id": FEATURE_ID_ARG,
            "expected_feature_revision": {"type": "integer", "minimum": 0},
            "mode": _nullable_string(max_length=16, enum=["AUTO", "ASSISTED"]),
        }
    ),
    "aisdlc_v1_operation_cancel": _strict_object(
        {
            "api_version": API_VERSION_ARG,
            "operation_id": OPERATION_ID_ARG,
            "reason": _nullable_string(max_length=512),
        }
    ),
    "aisdlc_v1_decision_respond": _strict_object(
        {
            "api_version": API_VERSION_ARG,
            "decision_id": DECISION_ID_ARG,
            "response": _string(max_length=4096),
        }
    ),
    "aisdlc_v1_notification_ack": _strict_object(
        {"api_version": API_VERSION_ARG, "notification_id": NOTIFICATION_ID_ARG}
    ),
}

_TOOL_DESCRIPTIONS = {
    "aisdlc_v1_system_capabilities": "Read the bounded AI-SDLC Operator capability matrix.",
    "aisdlc_v1_feature_status": "Read one authorized Feature lifecycle status.",
    "aisdlc_v1_operator_inbox": "Read the authorized Operator inbox.",
    "aisdlc_v1_operation_status": "Read one authorized durable Operation status.",
    "aisdlc_v1_decision_list": "List authorized pending/recent Decisions.",
    "aisdlc_v1_notification_list": "List authorized Notifications.",
    "aisdlc_v1_operation_start": "Start or converge one authorized Feature Operation.",
    "aisdlc_v1_operation_cancel": "Cancel one authorized Operation subject to durable launch fencing.",
    "aisdlc_v1_decision_respond": "Respond to one exact authorized current Decision choice.",
    "aisdlc_v1_notification_ack": "Acknowledge one exact authorized Notification.",
}

TOOLS: tuple[dict[str, Any], ...] = tuple(
    {
        "type": "function",
        "name": name,
        "description": _TOOL_DESCRIPTIONS[name],
        "parameters": TOOL_PARAMETER_SCHEMAS[name],
        "strict": True,
    }
    for name in TOOL_CAPABILITIES
)


class ResponsesProtocolError(ValueError):
    """Bounded fail-closed provider/adapter protocol failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ResponsesAuthorizationError(PermissionError):
    """Trusted registration rejected a model-visible selector."""


@dataclass(frozen=True)
class NormalizedFunctionCall:
    call_id: str
    name: str
    capability: str
    arguments: dict[str, Any]
    arguments_json: str


@dataclass(frozen=True)
class TrustedResponsesRegistration:
    """Server-owned adapter registration; model arguments cannot mutate these bindings."""

    registration_id: str
    provider_scope_id: str
    target_repository: str
    feature_refs: Mapping[str, str]
    trusted_context: Mapping[str, Any]
    human_principal: str | None = None

    def __post_init__(self) -> None:
        if not self.registration_id or not self.provider_scope_id:
            raise ValueError("trusted Responses registration/provider scope is required")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.target_repository):
            raise ValueError("trusted target repository is invalid")
        if not self.feature_refs:
            raise ValueError("trusted Feature allowlist is required")
        for feature_id, target_ref in self.feature_refs.items():
            if not FEATURE_ID_RE.fullmatch(feature_id) or not isinstance(target_ref, str) or not target_ref:
                raise ValueError("trusted Feature binding is invalid")

        trusted = dict(self.trusted_context)
        configured_client = trusted.get("trusted_client_adapter_id")
        if configured_client not in (None, ADAPTER_ID):
            raise ValueError("trusted adapter identity conflicts with Responses registration")
        scope = trusted.get("trusted_scope")
        if scope is not None:
            repositories = scope.get("repositories") if isinstance(scope, dict) else None
            if not isinstance(repositories, list) or self.target_repository.lower() not in {
                str(value).lower() for value in repositories
            }:
                raise ValueError("trusted Operator scope does not contain target repository")
            feature_ids = scope.get("feature_ids")
            if feature_ids is not None and not set(self.feature_refs).issubset(set(feature_ids)):
                raise ValueError("trusted Operator Feature scope does not contain registration allowlist")

    def require_feature(self, feature_id: str) -> str:
        if feature_id not in self.feature_refs:
            raise ResponsesAuthorizationError("Feature selector is outside trusted Responses registration")
        return str(self.feature_refs[feature_id])

    def invocation_context(self) -> dict[str, Any]:
        context = dict(self.trusted_context)
        context["trusted_client_adapter_id"] = ADAPTER_ID
        return context


@dataclass
class _StreamingCallState:
    item_id: str
    call_id: str
    name: str
    arguments: str = ""
    arguments_done: str | None = None
    terminal_item: dict[str, Any] | None = None


class ResponsesStreamCollector:
    """Collect function-call stream events without dispatching partial arguments."""

    def __init__(self) -> None:
        self._calls: dict[int, _StreamingCallState] = {}

    @staticmethod
    def _index(event: Mapping[str, Any]) -> int:
        value = event.get("output_index")
        if not isinstance(value, int) or value < 0:
            raise ResponsesProtocolError("INVALID_STREAM_EVENT", "stream output_index is invalid")
        return value

    def add(self, event: Any) -> None:
        if not isinstance(event, dict):
            raise ResponsesProtocolError("INVALID_STREAM_EVENT", "stream event must be an object")
        event_type = event.get("type")
        if event_type == "response.output_item.added":
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "function_call":
                return
            index = self._index(event)
            if index in self._calls:
                raise ResponsesProtocolError("STREAM_CONFLICT", "duplicate function_call output_index")
            allowed = {"type", "id", "call_id", "name", "arguments", "status"}
            if set(item) - allowed:
                raise ResponsesProtocolError("UNKNOWN_PROVIDER_FIELD", "function_call contains unsupported fields")
            item_id = item.get("id")
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments", "")
            if not isinstance(item_id, str) or not item_id:
                raise ResponsesProtocolError("INVALID_STREAM_EVENT", "function_call item id is required")
            if not isinstance(call_id, str) or not CALL_ID_RE.fullmatch(call_id):
                raise ResponsesProtocolError("INVALID_CALL_ID", "stream function_call.call_id is invalid")
            if not isinstance(name, str) or name not in TOOL_CAPABILITIES:
                raise ResponsesProtocolError("UNKNOWN_TOOL", "stream function_call.name is not supported")
            if not isinstance(arguments, str):
                raise ResponsesProtocolError("INVALID_ARGUMENTS", "stream function_call.arguments is invalid")
            self._calls[index] = _StreamingCallState(
                item_id=item_id,
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
            if len(self._calls) > 1:
                raise ResponsesProtocolError(
                    "MULTIPLE_FUNCTION_CALLS",
                    "parallel_tool_calls=false profile permits at most one executable function call",
                )
            return

        if event_type == "response.function_call_arguments.delta":
            index = self._index(event)
            state = self._calls.get(index)
            if state is None:
                raise ResponsesProtocolError("STREAM_BINDING_MISSING", "argument delta has no function_call")
            if event.get("item_id") != state.item_id:
                raise ResponsesProtocolError("STREAM_BINDING_CONFLICT", "argument delta item binding mismatch")
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise ResponsesProtocolError("INVALID_STREAM_EVENT", "argument delta must be a string")
            combined = state.arguments + delta
            if len(combined.encode("utf-8")) > 65536:
                raise ResponsesProtocolError("INVALID_ARGUMENTS", "stream arguments exceed bounded size")
            state.arguments = combined
            return

        if event_type == "response.function_call_arguments.done":
            index = self._index(event)
            state = self._calls.get(index)
            if state is None:
                raise ResponsesProtocolError("STREAM_BINDING_MISSING", "arguments.done has no function_call")
            if event.get("item_id") != state.item_id:
                raise ResponsesProtocolError("STREAM_BINDING_CONFLICT", "arguments.done item binding mismatch")
            arguments = event.get("arguments")
            if not isinstance(arguments, str):
                raise ResponsesProtocolError("INVALID_STREAM_EVENT", "arguments.done arguments must be a string")
            if state.arguments and state.arguments != arguments:
                raise ResponsesProtocolError("STREAM_ARGUMENT_CONFLICT", "stream delta aggregate differs from arguments.done")
            if len(arguments.encode("utf-8")) > 65536:
                raise ResponsesProtocolError("INVALID_ARGUMENTS", "stream arguments exceed bounded size")
            state.arguments = arguments
            state.arguments_done = arguments
            return

        if event_type == "response.output_item.done":
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "function_call":
                return
            index = self._index(event)
            state = self._calls.get(index)
            if state is None:
                raise ResponsesProtocolError("STREAM_BINDING_MISSING", "output_item.done has no function_call")
            terminal = parse_function_call(item)
            try:
                streamed_arguments = json.dumps(
                    json.loads(state.arguments),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            except json.JSONDecodeError as exc:
                raise ResponsesProtocolError("MALFORMED_ARGUMENTS", "stream argument aggregate is not valid JSON") from exc
            if (
                item.get("id") != state.item_id
                or terminal.call_id != state.call_id
                or terminal.name != state.name
                or terminal.arguments_json != streamed_arguments
            ):
                raise ResponsesProtocolError("STREAM_BINDING_CONFLICT", "terminal function_call differs from stream binding")
            if state.arguments_done is not None and item.get("arguments") != state.arguments_done:
                raise ResponsesProtocolError("STREAM_ARGUMENT_CONFLICT", "terminal arguments differ from arguments.done")
            state.terminal_item = dict(item)

    def finish(self) -> NormalizedFunctionCall | None:
        if not self._calls:
            return None
        if len(self._calls) != 1:
            raise ResponsesProtocolError("MULTIPLE_FUNCTION_CALLS", "multiple function calls are not supported")
        state = next(iter(self._calls.values()))
        if state.terminal_item is None:
            raise ResponsesProtocolError("INTERRUPTED_FUNCTION_CALL", "stream ended before output_item.done")
        return parse_function_call(state.terminal_item)


def collect_stream_function_call(events: Any) -> NormalizedFunctionCall | None:
    collector = ResponsesStreamCollector()
    for event in events:
        collector.add(event)
    return collector.finish()


class ResponsesCallJournal(Protocol):
    def bind_call(self, *, call_key: str, binding: dict[str, Any]) -> dict[str, Any]:
        ...

    def lookup_result(self, *, call_key: str) -> dict[str, Any] | None:
        ...

    def record_result(self, *, call_key: str, result: dict[str, Any]) -> dict[str, Any]:
        ...


def tool_definitions() -> list[dict[str, Any]]:
    """Return detached tool definitions safe for provider request construction."""

    return json.loads(json.dumps(TOOLS))


def responses_request_profile() -> dict[str, Any]:
    return {
        "tools": tool_definitions(),
        "parallel_tool_calls": PARALLEL_TOOL_CALLS,
    }


def _schema_errors(name: str, arguments: dict[str, Any]) -> list[str]:
    errors = []
    for error in Draft202012Validator(TOOL_PARAMETER_SCHEMAS[name]).iter_errors(arguments):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return sorted(errors)


def parse_function_call(item: Any) -> NormalizedFunctionCall:
    if not isinstance(item, dict):
        raise ResponsesProtocolError("INVALID_PROVIDER_ITEM", "Responses output item must be an object")
    allowed = {"type", "id", "call_id", "name", "arguments", "status"}
    unknown = set(item) - allowed
    if unknown:
        raise ResponsesProtocolError("UNKNOWN_PROVIDER_FIELD", "function_call contains unsupported fields")
    if item.get("type") != "function_call":
        raise ResponsesProtocolError("INVALID_PROVIDER_ITEM", "expected a function_call item")
    status = item.get("status")
    if status is not None and status != "completed":
        raise ResponsesProtocolError("INCOMPLETE_FUNCTION_CALL", "function_call is not completed")
    call_id = item.get("call_id")
    name = item.get("name")
    raw_arguments = item.get("arguments")
    if not isinstance(call_id, str) or not CALL_ID_RE.fullmatch(call_id):
        raise ResponsesProtocolError("INVALID_CALL_ID", "function_call.call_id is missing or invalid")
    if not isinstance(name, str) or name not in TOOL_CAPABILITIES:
        raise ResponsesProtocolError("UNKNOWN_TOOL", "function_call.name is not supported")
    if not isinstance(raw_arguments, str) or len(raw_arguments.encode("utf-8")) > 65536:
        raise ResponsesProtocolError("INVALID_ARGUMENTS", "function_call.arguments must be bounded serialized JSON")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ResponsesProtocolError("MALFORMED_ARGUMENTS", "function_call.arguments is not valid JSON") from exc
    if not isinstance(arguments, dict):
        raise ResponsesProtocolError("INVALID_ARGUMENTS", "function_call.arguments must decode to an object")
    errors = _schema_errors(name, arguments)
    if errors:
        raise ResponsesProtocolError("SCHEMA_INVALID_ARGUMENTS", "function_call.arguments failed strict schema")
    normalized = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return NormalizedFunctionCall(
        call_id=call_id,
        name=name,
        capability=TOOL_CAPABILITIES[name],
        arguments=dict(arguments),
        arguments_json=normalized,
    )


def collect_function_call(output_items: Any) -> NormalizedFunctionCall | None:
    """Collect a terminal Responses output and accept at most one executable function call."""

    if not isinstance(output_items, list):
        raise ResponsesProtocolError("INVALID_PROVIDER_OUTPUT", "Responses output must be a list")
    calls = []
    for item in output_items:
        if not isinstance(item, dict):
            raise ResponsesProtocolError("INVALID_PROVIDER_ITEM", "Responses output item must be an object")
        if item.get("type") == "function_call":
            calls.append(parse_function_call(item))
    if len(calls) > 1:
        raise ResponsesProtocolError(
            "MULTIPLE_FUNCTION_CALLS",
            "parallel_tool_calls=false profile permits at most one executable function call",
        )
    return calls[0] if calls else None


def encode_function_call_output(call_id: str, canonical_response: Mapping[str, Any]) -> dict[str, Any]:
    if not CALL_ID_RE.fullmatch(call_id):
        raise ResponsesProtocolError("INVALID_CALL_ID", "cannot encode output for invalid call_id")
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(
            dict(canonical_response),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }


def responses_call_key(registration: TrustedResponsesRegistration, call_id: str) -> str:
    if not CALL_ID_RE.fullmatch(call_id):
        raise ResponsesProtocolError("INVALID_CALL_ID", "cannot bind invalid call_id")
    material = {
        "schema": "ai-sdlc.openai.responses.call/v1",
        "adapter_id": ADAPTER_ID,
        "adapter_registration_id": registration.registration_id,
        "trusted_provider_scope_id": registration.provider_scope_id,
        "call_id": call_id,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request_id(call_key: str) -> str:
    return f"openai-responses-{call_key[:40]}"


def _idempotency_key(call_key: str) -> str:
    digest = hashlib.sha256(f"openai-responses-write/v1:{call_key}".encode("utf-8")).hexdigest()
    return f"openai-responses/{digest}"


def build_canonical_request(
    call: NormalizedFunctionCall,
    *,
    registration: TrustedResponsesRegistration,
    call_key: str,
) -> dict[str, Any]:
    args = dict(call.arguments)
    api_version = str(args.pop("api_version"))
    request: dict[str, Any] = {
        "api_version": api_version,
        "request_id": _request_id(call_key),
        "capability": call.capability,
        "client_identity": {"adapter_id": ADAPTER_ID},
        "payload": {},
    }
    if registration.human_principal is not None:
        request["client_identity"]["human_principal"] = registration.human_principal

    capability = call.capability
    if capability == "feature.status":
        feature_id = str(args.pop("feature_id"))
        registration.require_feature(feature_id)
        request["target"] = {
            "repository": registration.target_repository,
            "feature_id": feature_id,
        }
    elif capability == "operation.status":
        request["context"] = {"operation_id": str(args.pop("operation_id"))}
    elif capability == "operation.start":
        feature_id = str(args.pop("feature_id"))
        registration.require_feature(feature_id)
        request["target"] = {
            "repository": registration.target_repository,
            "feature_id": feature_id,
        }
        request["context"] = {
            "expected_feature_revision": int(args.pop("expected_feature_revision"))
        }
        mode = args.pop("mode")
        if mode is not None:
            request["payload"]["mode"] = mode
    elif capability == "operation.cancel":
        request["context"] = {"operation_id": str(args.pop("operation_id"))}
        reason = args.pop("reason")
        if reason is not None:
            request["payload"]["reason"] = reason
    elif capability == "decision.respond":
        request["payload"] = {
            "decision_id": str(args.pop("decision_id")),
            "response": str(args.pop("response")),
        }
    elif capability == "notification.ack":
        request["payload"] = {"notification_id": str(args.pop("notification_id"))}

    if args:
        raise ResponsesProtocolError("INTERNAL_MAPPING_ERROR", "validated arguments were not fully mapped")
    if capability in WRITE_CAPABILITIES:
        request["idempotency_key"] = _idempotency_key(call_key)
    return request


def _digest_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OpenAIResponsesOperatorAdapter:
    """Production translation path; backend composition remains injected trusted authority."""

    def __init__(
        self,
        *,
        registration: TrustedResponsesRegistration,
        backends: Mapping[str, Any],
        journal: ResponsesCallJournal,
    ) -> None:
        self.registration = registration
        self.backends = dict(backends)
        self.journal = journal

    def invoke_function_call(self, item: Any) -> dict[str, Any]:
        call = parse_function_call(item)
        call_key = responses_call_key(self.registration, call.call_id)
        request = build_canonical_request(
            call,
            registration=self.registration,
            call_key=call_key,
        )
        binding = {
            "schema_version": "ai-sdlc.openai.responses.call-binding/v1",
            "call_key": call_key,
            "adapter_id": ADAPTER_ID,
            "adapter_protocol_version": ADAPTER_PROTOCOL_VERSION,
            "registration_digest": hashlib.sha256(
                self.registration.registration_id.encode("utf-8")
            ).hexdigest(),
            "provider_scope_digest": hashlib.sha256(
                self.registration.provider_scope_id.encode("utf-8")
            ).hexdigest(),
            "call_id": call.call_id,
            "tool_name": call.name,
            "capability": call.capability,
            "arguments_digest": hashlib.sha256(call.arguments_json.encode("utf-8")).hexdigest(),
            "canonical_request_digest": _digest_json(request),
            "canonical_request": request,
        }
        self.journal.bind_call(call_key=call_key, binding=binding)
        existing = self.journal.lookup_result(call_key=call_key)
        if existing is not None:
            output = existing.get("function_call_output")
            if not isinstance(output, dict):
                raise ResponsesProtocolError("JOURNAL_CORRUPT", "stored Responses result is invalid")
            return dict(output)

        canonical_response = dispatch(
            request,
            trusted_context=self.registration.invocation_context(),
            backends=self.backends,
        )
        function_output = encode_function_call_output(call.call_id, canonical_response)
        result = {
            "schema_version": "ai-sdlc.openai.responses.call-result/v1",
            "call_key": call_key,
            "canonical_response_digest": _digest_json(canonical_response),
            "function_call_output": function_output,
        }
        self.journal.record_result(call_key=call_key, result=result)
        return function_output
