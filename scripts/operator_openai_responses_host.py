#!/usr/bin/env python3
"""Trusted host orchestration for the OpenAI Responses SDK boundary.

The SDK client, model, instructions and provider credentials are deployment-owned
trusted inputs. Model-visible tool arguments cannot override them.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol

from operator_openai_responses import (
    OpenAIResponsesOperatorAdapter,
    collect_function_call,
    collect_stream_function_call,
    responses_request_profile,
)

_RESPONSE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_TERMINAL_NONEXECUTABLE_STATUSES = frozenset({"failed", "cancelled", "incomplete"})


class ResponsesEndpoint(Protocol):
    def create(self, **kwargs: Any) -> Any:
        ...

    def retrieve(self, response_id: str, **kwargs: Any) -> Any:
        ...


class OpenAIClientLike(Protocol):
    responses: ResponsesEndpoint


@dataclass(frozen=True)
class TrustedOpenAIResponsesHostConfig:
    """Server-owned provider configuration; never constructed from model arguments."""

    model: str
    instructions: str
    store: bool = True
    background: bool = False
    max_tool_rounds: int = 8

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 128:
            raise ValueError("trusted OpenAI model is required")
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("trusted OpenAI instructions are required")
        if len(self.instructions.encode("utf-8")) > 65536:
            raise ValueError("trusted OpenAI instructions exceed bounded size")
        if not isinstance(self.store, bool) or not isinstance(self.background, bool):
            raise ValueError("trusted OpenAI store/background flags must be boolean")
        if not isinstance(self.max_tool_rounds, int) or not 1 <= self.max_tool_rounds <= 32:
            raise ValueError("max_tool_rounds must be between 1 and 32")


@dataclass(frozen=True)
class ResponsesHostResult:
    response_id: str
    status: str
    tool_rounds: int
    pending: bool
    response: dict[str, Any]


class ResponsesHostError(RuntimeError):
    pass


def _as_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dict(dumped)
    raise ResponsesHostError(f"{label} is not a supported Responses SDK object")


def _response_id(response: Mapping[str, Any]) -> str:
    value = response.get("id")
    if not isinstance(value, str) or not _RESPONSE_ID_RE.fullmatch(value):
        raise ResponsesHostError("Responses object is missing a bounded response id")
    return value


def _response_status(response: Mapping[str, Any]) -> str:
    if "status" not in response:
        raise ResponsesHostError("Responses object is missing explicit status")
    value = response.get("status")
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ResponsesHostError("Responses object has invalid status")
    return value


def _response_pending(status: str) -> bool:
    return status != "completed" and status not in _TERMINAL_NONEXECUTABLE_STATUSES


def _response_output(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("output") or []
    if not isinstance(raw, list):
        raise ResponsesHostError("Responses output must be a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        rows.append(_as_mapping(item, label="Responses output item"))
    return rows


def _function_item_from_call(call) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.call_id,
        "name": call.name,
        "arguments": call.arguments_json,
        "status": "completed",
    }


def _event_response_id(events: list[dict[str, Any]]) -> str:
    """Require one stable response id across every stream event that exposes it."""

    observed: set[str] = set()
    for event in events:
        direct = event.get("response_id")
        if direct is not None:
            if not isinstance(direct, str) or not _RESPONSE_ID_RE.fullmatch(direct):
                raise ResponsesHostError("stream event contains an invalid response id")
            observed.add(direct)
        nested = event.get("response")
        if isinstance(nested, dict) and "id" in nested:
            nested_id = nested.get("id")
            if not isinstance(nested_id, str) or not _RESPONSE_ID_RE.fullmatch(nested_id):
                raise ResponsesHostError("stream terminal object contains an invalid response id")
            observed.add(nested_id)
    if not observed:
        raise ResponsesHostError("stream completed without a bounded response id")
    if len(observed) != 1:
        raise ResponsesHostError("stream response id changed before completion")
    return next(iter(observed))


def _event_terminal_response(events: list[dict[str, Any]], response_id: str) -> dict[str, Any]:
    """Require the explicit provider completion boundary before semantic work.

    `response.output_item.done` is only an item-level event. It cannot authorize
    tool execution by itself because later stream events may still invalidate the
    response or the transport may terminate before the provider completion
    boundary. Only an explicit `response.completed` event is accepted here.
    """

    for event in reversed(events):
        if event.get("type") != "response.completed" or not isinstance(event.get("response"), dict):
            continue
        response = dict(event["response"])
        terminal_id = _response_id(response)
        if terminal_id != response_id:
            raise ResponsesHostError("stream terminal response id does not match collected response id")
        return response
    raise ResponsesHostError("stream ended before response.completed")


class OpenAIResponsesHost:
    """Drive bounded Responses tool rounds through the production adapter boundary."""

    def __init__(
        self,
        *,
        client: OpenAIClientLike,
        config: TrustedOpenAIResponsesHostConfig,
        adapter: OpenAIResponsesOperatorAdapter,
    ) -> None:
        responses = getattr(client, "responses", None)
        if responses is None or not callable(getattr(responses, "create", None)) or not callable(
            getattr(responses, "retrieve", None)
        ):
            raise ValueError("OpenAI Responses host requires an SDK client with responses.create/retrieve")
        if not isinstance(adapter, OpenAIResponsesOperatorAdapter):
            raise ValueError("OpenAI Responses host requires production adapter boundary")
        self.client = client
        self.config = config
        self.adapter = adapter

    def _base_create_kwargs(self) -> dict[str, Any]:
        profile = responses_request_profile()
        return {
            "model": self.config.model,
            "instructions": self.config.instructions,
            "tools": profile["tools"],
            "parallel_tool_calls": profile["parallel_tool_calls"],
            "store": self.config.store,
        }

    def _create(self, *, input_value: Any, previous_response_id: str | None = None, stream: bool = False):
        kwargs = self._base_create_kwargs()
        kwargs["input"] = input_value
        if previous_response_id is not None:
            if not _RESPONSE_ID_RE.fullmatch(previous_response_id):
                raise ResponsesHostError("previous response id is invalid")
            kwargs["previous_response_id"] = previous_response_id
        if stream:
            kwargs["stream"] = True
        elif self.config.background:
            kwargs["background"] = True
        return self.client.responses.create(**kwargs)

    def _continue_after_call(self, *, response_id: str, call) -> Any:
        function_output = self.adapter.invoke_function_call(_function_item_from_call(call))
        return self._create(
            input_value=[function_output],
            previous_response_id=response_id,
            stream=False,
        )

    def run_sync(self, input_value: Any) -> ResponsesHostResult:
        response = _as_mapping(self._create(input_value=input_value), label="Responses object")
        return self._drive_terminal(response, tool_rounds=0)

    def retrieve_and_continue(self, response_id: str) -> ResponsesHostResult:
        if not isinstance(response_id, str) or not _RESPONSE_ID_RE.fullmatch(response_id):
            raise ResponsesHostError("response id is invalid")
        response = _as_mapping(
            self.client.responses.retrieve(response_id),
            label="retrieved Responses object",
        )
        if _response_id(response) != response_id:
            raise ResponsesHostError("retrieved response id does not match trusted retrieval target")
        return self._drive_terminal(response, tool_rounds=0)

    def _drive_terminal(self, response: dict[str, Any], *, tool_rounds: int) -> ResponsesHostResult:
        while True:
            response_id = _response_id(response)
            status = _response_status(response)

            # Provider output is observational until the whole response reaches
            # the explicit completed state. Pending/failed/cancelled/incomplete
            # responses never authorize semantic tool execution, even if their
            # payload happens to contain a syntactically completed function item.
            if status != "completed":
                return ResponsesHostResult(
                    response_id=response_id,
                    status=status,
                    tool_rounds=tool_rounds,
                    pending=_response_pending(status),
                    response=dict(response),
                )

            output = _response_output(response)
            call = collect_function_call(output)
            if call is None:
                return ResponsesHostResult(
                    response_id=response_id,
                    status=status,
                    tool_rounds=tool_rounds,
                    pending=False,
                    response=dict(response),
                )
            if tool_rounds >= self.config.max_tool_rounds:
                raise ResponsesHostError("Responses tool-round limit exceeded")
            tool_rounds += 1
            response = _as_mapping(
                self._continue_after_call(response_id=response_id, call=call),
                label="Responses continuation object",
            )

    def run_stream(self, input_value: Any) -> ResponsesHostResult:
        previous_response_id: str | None = None
        next_input = input_value
        tool_rounds = 0
        while True:
            stream = self._create(
                input_value=next_input,
                previous_response_id=previous_response_id,
                stream=True,
            )
            events = [_as_mapping(event, label="Responses stream event") for event in stream]
            response_id = _event_response_id(events)
            terminal = _event_terminal_response(events, response_id)
            status = _response_status(terminal)

            # As with non-streaming/background responses, a non-completed
            # terminal object cannot authorize a function call. This check is
            # deliberately before collector.finish()/adapter invocation.
            if status != "completed":
                return ResponsesHostResult(
                    response_id=response_id,
                    status=status,
                    tool_rounds=tool_rounds,
                    pending=_response_pending(status),
                    response=terminal,
                )

            call = collect_stream_function_call(events)
            if call is None:
                return ResponsesHostResult(
                    response_id=response_id,
                    status=status,
                    tool_rounds=tool_rounds,
                    pending=False,
                    response=terminal,
                )
            if tool_rounds >= self.config.max_tool_rounds:
                raise ResponsesHostError("Responses streaming tool-round limit exceeded")
            tool_rounds += 1
            function_output = self.adapter.invoke_function_call(_function_item_from_call(call))
            previous_response_id = response_id
            next_input = [function_output]


def build_official_openai_client(**trusted_client_kwargs: Any) -> Any:
    """Lazy production SDK constructor; credentials/options stay server-owned.

    Deployment packaging must install the reviewed official `openai` SDK dependency.
    Deterministic CI need not import or contact it because host tests inject a client.
    """

    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:  # pragma: no cover - deterministic CI uses injected client
        raise ResponsesHostError("official OpenAI Python SDK is not installed") from exc
    return OpenAI(**trusted_client_kwargs)
