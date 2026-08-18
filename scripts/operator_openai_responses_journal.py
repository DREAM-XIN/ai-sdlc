#!/usr/bin/env python3
"""Durable OpenAI Responses call journal over the protected Operator Store."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from operator_store_backends import OperatorStoreRuntime
from operator_store_model import (
    STORE_ROOT,
    StoreInvariantError,
    StoreMutation,
    StoreMutationPlan,
    canonical_json,
)

CALL_BINDING_SCHEMA = "ai-sdlc.openai.responses.call-binding/v1"
CALL_RESULT_SCHEMA = "ai-sdlc.openai.responses.call-result/v1"
_CALL_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ResponsesJournalError(RuntimeError):
    """Base class for bounded durable call-journal failures."""


class ResponsesCallConflict(ResponsesJournalError):
    """The same durable Responses call key was reused with different semantics."""


class ResponsesJournalCorrupt(ResponsesJournalError):
    """Durable journal data failed its immutable binding/schema contract."""


class _AlreadyConverged(RuntimeError):
    def __init__(self, record: dict[str, Any]):
        super().__init__("durable Responses journal record already converged")
        self.record = record


def _validate_call_key(call_key: str) -> str:
    if not isinstance(call_key, str) or not _CALL_KEY_RE.fullmatch(call_key):
        raise ResponsesJournalError("invalid Responses call key")
    return call_key


def call_binding_path(call_key: str) -> str:
    return f"{STORE_ROOT}/adapter-calls/openai-responses/{_validate_call_key(call_key)}.json"


def call_result_path(call_key: str) -> str:
    return f"{STORE_ROOT}/adapter-call-results/openai-responses/{_validate_call_key(call_key)}.json"


def _read_record(snapshot, path: str, *, schema: str, call_key: str) -> dict[str, Any] | None:
    raw = snapshot.get(path)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ResponsesJournalCorrupt("Responses journal record must be an object")
    if raw.get("schema_version") != schema or raw.get("call_key") != call_key:
        raise ResponsesJournalCorrupt("Responses journal schema/call binding mismatch")
    return dict(raw)


def _require_candidate(record: dict[str, Any], *, schema: str, call_key: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ResponsesJournalError("Responses journal candidate must be an object")
    candidate = dict(record)
    if candidate.get("schema_version") != schema or candidate.get("call_key") != call_key:
        raise ResponsesJournalError("Responses journal candidate schema/call binding mismatch")
    return candidate


def _validated_result_receipt(
    record: dict[str, Any],
    *,
    binding: dict[str, Any],
    call_key: str,
    durable: bool,
) -> dict[str, Any]:
    """Bind one result receipt to the exact immutable call and canonical output.

    A durable result is replay authority at the adapter boundary: once present,
    canonical dispatch is skipped. That makes its correlation fields part of the
    fail-closed trust contract rather than advisory metadata.
    """

    error_type = ResponsesJournalCorrupt if durable else ResponsesJournalError
    candidate = dict(record)
    expected_keys = {
        "schema_version",
        "call_key",
        "canonical_response_digest",
        "function_call_output",
    }
    if set(candidate) != expected_keys:
        raise error_type("Responses result receipt has an unsupported shape")
    if candidate.get("schema_version") != CALL_RESULT_SCHEMA or candidate.get("call_key") != call_key:
        raise error_type("Responses result receipt schema/call binding mismatch")

    bound_call_id = binding.get("call_id")
    if not isinstance(bound_call_id, str) or not bound_call_id:
        raise ResponsesJournalCorrupt("Responses call binding has no exact call_id")

    digest = candidate.get("canonical_response_digest")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise error_type("Responses result receipt has an invalid canonical response digest")

    function_output = candidate.get("function_call_output")
    if not isinstance(function_output, dict):
        raise error_type("Responses result receipt function_call_output must be an object")
    if set(function_output) != {"type", "call_id", "output"}:
        raise error_type("Responses result receipt function_call_output shape drifted")
    if function_output.get("type") != "function_call_output":
        raise error_type("Responses result receipt output type is invalid")
    if function_output.get("call_id") != bound_call_id:
        raise error_type("Responses result receipt call_id does not match immutable binding")

    output_json = function_output.get("output")
    if not isinstance(output_json, str):
        raise error_type("Responses result receipt output must be serialized JSON")
    try:
        decoded = json.loads(output_json)
    except json.JSONDecodeError as exc:
        raise error_type("Responses result receipt output is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise error_type("Responses result receipt output must encode a canonical response object")
    canonical_output = json.dumps(
        decoded,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if canonical_output != output_json:
        raise error_type("Responses result receipt output is not canonical serialized JSON")
    if hashlib.sha256(output_json.encode("utf-8")).hexdigest() != digest:
        raise error_type("Responses result receipt canonical response digest mismatch")
    return candidate


class StoreResponsesCallJournal:
    """Immutable binding/result receipts on the same protected Store runtime.

    The journal is transport replay state only. It cannot create Operation facts,
    Feature Events, Persist facts, dispatch reservations, Decisions or Gate authority.
    """

    def __init__(self, runtime: OperatorStoreRuntime):
        if not isinstance(runtime, OperatorStoreRuntime):
            raise ValueError("Responses call journal requires trusted Operator Store runtime")
        self.runtime = runtime

    def bind_call(self, *, call_key: str, binding: dict[str, Any]) -> dict[str, Any]:
        call_key = _validate_call_key(call_key)
        candidate = _require_candidate(binding, schema=CALL_BINDING_SCHEMA, call_key=call_key)
        path = call_binding_path(call_key)

        existing = _read_record(
            self.runtime.backend.read_snapshot(),
            path,
            schema=CALL_BINDING_SCHEMA,
            call_key=call_key,
        )
        if existing is not None:
            if canonical_json(existing) != canonical_json(candidate):
                raise ResponsesCallConflict("Responses call key already has a different immutable binding")
            return existing

        def planner(snapshot):
            current = _read_record(
                snapshot,
                path,
                schema=CALL_BINDING_SCHEMA,
                call_key=call_key,
            )
            if current is not None:
                if canonical_json(current) != canonical_json(candidate):
                    raise ResponsesCallConflict("Responses call key raced with a conflicting immutable binding")
                raise _AlreadyConverged(current)
            return StoreMutationPlan(
                expected_ref_sha=snapshot.ref_sha,
                mutations=(StoreMutation("create_immutable", path, candidate),),
                result=dict(candidate),
            )

        try:
            result = self.runtime.commit_replanned(planner)
            return dict(result.result)
        except _AlreadyConverged as converged:
            return dict(converged.record)
        except StoreInvariantError as exc:
            raise ResponsesJournalCorrupt(str(exc)) from exc

    def lookup_result(self, *, call_key: str) -> dict[str, Any] | None:
        call_key = _validate_call_key(call_key)
        snapshot = self.runtime.backend.read_snapshot()
        binding = _read_record(
            snapshot,
            call_binding_path(call_key),
            schema=CALL_BINDING_SCHEMA,
            call_key=call_key,
        )
        result = _read_record(
            snapshot,
            call_result_path(call_key),
            schema=CALL_RESULT_SCHEMA,
            call_key=call_key,
        )
        if result is None:
            return None
        if binding is None:
            raise ResponsesJournalCorrupt("Responses result receipt exists without immutable call binding")
        return _validated_result_receipt(
            result,
            binding=binding,
            call_key=call_key,
            durable=True,
        )

    def record_result(self, *, call_key: str, result: dict[str, Any]) -> dict[str, Any]:
        call_key = _validate_call_key(call_key)
        candidate = _require_candidate(result, schema=CALL_RESULT_SCHEMA, call_key=call_key)
        binding_path = call_binding_path(call_key)
        result_path = call_result_path(call_key)

        snapshot = self.runtime.backend.read_snapshot()
        binding = _read_record(
            snapshot,
            binding_path,
            schema=CALL_BINDING_SCHEMA,
            call_key=call_key,
        )
        if binding is None:
            raise ResponsesJournalError("Responses result cannot exist before immutable call binding")
        candidate = _validated_result_receipt(
            candidate,
            binding=binding,
            call_key=call_key,
            durable=False,
        )
        existing = _read_record(
            snapshot,
            result_path,
            schema=CALL_RESULT_SCHEMA,
            call_key=call_key,
        )
        if existing is not None:
            existing = _validated_result_receipt(
                existing,
                binding=binding,
                call_key=call_key,
                durable=True,
            )
            if canonical_json(existing) != canonical_json(candidate):
                raise ResponsesCallConflict("Responses call key already has a different immutable result")
            return existing

        def planner(current_snapshot):
            durable_binding = _read_record(
                current_snapshot,
                binding_path,
                schema=CALL_BINDING_SCHEMA,
                call_key=call_key,
            )
            if durable_binding is None:
                raise ResponsesJournalCorrupt("Responses call binding disappeared before result commit")
            current = _read_record(
                current_snapshot,
                result_path,
                schema=CALL_RESULT_SCHEMA,
                call_key=call_key,
            )
            if current is not None:
                current = _validated_result_receipt(
                    current,
                    binding=durable_binding,
                    call_key=call_key,
                    durable=True,
                )
                if canonical_json(current) != canonical_json(candidate):
                    raise ResponsesCallConflict("Responses result raced with a conflicting immutable receipt")
                raise _AlreadyConverged(current)
            return StoreMutationPlan(
                expected_ref_sha=current_snapshot.ref_sha,
                mutations=(StoreMutation("create_immutable", result_path, candidate),),
                result=dict(candidate),
            )

        try:
            committed = self.runtime.commit_replanned(planner)
            return dict(committed.result)
        except _AlreadyConverged as converged:
            return dict(converged.record)
        except StoreInvariantError as exc:
            raise ResponsesJournalCorrupt(str(exc)) from exc
