#!/usr/bin/env python3
"""Bounded OpenAI Responses host loop for trusted v0.3 real dogfood.

This host owns only provider-session transport.  Every executable tool call is
passed through the already-reviewed ``OpenAIResponsesOperatorAdapter``; the host
never calls canonical backends, Vertical, Store, dispatch, Persist, Decision or
Feature Event authority directly.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib import error, request

from operator_openai_responses import responses_request_profile


class V03DogfoodOpenAIHostError(RuntimeError):
    pass


@dataclass(frozen=True)
class V03DogfoodOpenAIHostConfig:
    api_key: str
    model: str
    api_base: str = "https://api.openai.com/v1"
    max_tool_turns: int = 12

    def __post_init__(self) -> None:
        if not self.api_key or not self.model:
            raise ValueError("dogfood Responses host requires server-owned API key/model")
        if not self.api_base.startswith("https://"):
            raise ValueError("dogfood Responses host requires HTTPS provider endpoint")
        if self.max_tool_turns < 1 or self.max_tool_turns > 32:
            raise ValueError("dogfood Responses host tool-turn bound is invalid")


@dataclass(frozen=True)
class V03DogfoodResponsesTrace:
    response_ids: tuple[str, ...]
    function_call_ids: tuple[str, ...]
    function_outputs: tuple[dict[str, Any], ...]
    terminal_response: dict[str, Any]


class V03DogfoodOpenAIResponsesHost:
    """Sequential, fail-closed Responses function-tool host for one dogfood run."""

    def __init__(
        self,
        *,
        config: V03DogfoodOpenAIHostConfig,
        adapter: Any,
        http_post: Callable[[str, dict[str, str], dict[str, Any]], tuple[int, Any]] | None = None,
    ) -> None:
        if not callable(getattr(adapter, "invoke_function_call", None)):
            raise ValueError("dogfood Responses host requires reviewed adapter invocation boundary")
        self.config = config
        self.adapter = adapter
        self.http_post = http_post or self._default_post

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ai-sdlc-v03-real-dogfood",
        }

    @staticmethod
    def _default_post(url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=90) as response:
                raw = response.read()
                return int(response.status), json.loads(raw.decode("utf-8")) if raw else {}
        except error.HTTPError as exc:
            return int(exc.code), {}
        except Exception:
            return 0, {}

    @staticmethod
    def _response_id(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise V03DogfoodOpenAIHostError("Responses provider returned non-object payload")
        response_id = payload.get("id")
        if not isinstance(response_id, str) or not response_id.startswith("resp_") or len(response_id) > 256:
            raise V03DogfoodOpenAIHostError("Responses provider returned invalid response id")
        if payload.get("status") != "completed":
            raise V03DogfoodOpenAIHostError("dogfood host executes tools only from completed Responses objects")
        output = payload.get("output")
        if not isinstance(output, list):
            raise V03DogfoodOpenAIHostError("completed Responses object lacks output list")
        return response_id

    @staticmethod
    def _function_call(payload: dict[str, Any]) -> dict[str, Any] | None:
        calls: list[dict[str, Any]] = []
        for item in payload["output"]:
            if not isinstance(item, dict):
                raise V03DogfoodOpenAIHostError("Responses output item is not an object")
            item_type = item.get("type")
            if item_type == "function_call":
                calls.append(item)
                continue
            # The host registers only fixed function tools.  Any executable-like
            # provider item outside that profile is a protocol expansion and is
            # rejected rather than silently becoming an alternate authority.
            if isinstance(item_type, str) and (item_type.endswith("_call") or item_type.endswith("_tool_call")):
                raise V03DogfoodOpenAIHostError("unsupported executable provider item in dogfood response")
        if len(calls) > 1:
            raise V03DogfoodOpenAIHostError("parallel/multiple function calls are forbidden in dogfood profile")
        return calls[0] if calls else None

    def _create(self, body: dict[str, Any]) -> dict[str, Any]:
        status, payload = self.http_post(
            self.config.api_base.rstrip("/") + "/responses",
            self._headers(),
            body,
        )
        if status != 200 or not isinstance(payload, dict):
            raise V03DogfoodOpenAIHostError("OpenAI Responses request failed closed")
        self._response_id(payload)
        return payload

    def run(self, *, scenario_instruction: str) -> V03DogfoodResponsesTrace:
        instruction = str(scenario_instruction or "").strip()
        if not instruction or len(instruction.encode("utf-8")) > 16384:
            raise ValueError("dogfood scenario instruction is missing or unbounded")

        profile = responses_request_profile()
        if profile.get("parallel_tool_calls") is not False:
            raise V03DogfoodOpenAIHostError("reviewed Responses profile must keep parallel_tool_calls=false")
        tools = profile.get("tools")
        if not isinstance(tools, list) or not tools:
            raise V03DogfoodOpenAIHostError("reviewed Responses tool profile is missing")

        response_ids: list[str] = []
        call_ids: list[str] = []
        outputs: list[dict[str, Any]] = []
        payload = self._create({
            "model": self.config.model,
            "input": instruction,
            "tools": tools,
            "parallel_tool_calls": False,
        })

        for _ in range(self.config.max_tool_turns + 1):
            response_id = self._response_id(payload)
            if response_id in response_ids:
                raise V03DogfoodOpenAIHostError("Responses provider repeated a response id")
            response_ids.append(response_id)
            call = self._function_call(payload)
            if call is None:
                return V03DogfoodResponsesTrace(
                    response_ids=tuple(response_ids),
                    function_call_ids=tuple(call_ids),
                    function_outputs=tuple(outputs),
                    terminal_response=dict(payload),
                )
            if len(call_ids) >= self.config.max_tool_turns:
                raise V03DogfoodOpenAIHostError("dogfood Responses host exceeded bounded tool turns")
            result = self.adapter.invoke_function_call(call)
            if not isinstance(result, dict) or result.get("type") != "function_call_output":
                raise V03DogfoodOpenAIHostError("reviewed adapter returned invalid function_call_output")
            call_id = call.get("call_id")
            if not isinstance(call_id, str) or result.get("call_id") != call_id:
                raise V03DogfoodOpenAIHostError("adapter output correlation differs from exact provider call_id")
            if call_id in call_ids:
                raise V03DogfoodOpenAIHostError("same Responses call_id appeared in multiple provider turns")
            call_ids.append(call_id)
            outputs.append(dict(result))
            payload = self._create({
                "model": self.config.model,
                "previous_response_id": response_id,
                "input": [result],
                "tools": tools,
                "parallel_tool_calls": False,
            })

        raise V03DogfoodOpenAIHostError("dogfood Responses host failed to reach bounded terminal response")
