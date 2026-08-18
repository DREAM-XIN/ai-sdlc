#!/usr/bin/env python3
"""Fail-closed durable Responses result-receipt validation.

A completed adapter result is replay authority: its presence suppresses canonical
redispatch. This validator proves the durable receipt remains bound to the exact
immutable call_id and exact canonical serialized response, including corruption
that bypasses the journal writer and is observed only after process restart.
"""
from __future__ import annotations

import hashlib
import json

from operator_openai_responses_journal import (
    CALL_BINDING_SCHEMA,
    CALL_RESULT_SCHEMA,
    ResponsesJournalCorrupt,
    ResponsesJournalError,
    StoreResponsesCallJournal,
    call_result_path,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import StoreMutation, StoreMutationPlan
from operator_store_protection import PROTECTED, StaticProtectionVerifier

REPO = "DREAM-XIN/responses-result-receipt-fixture"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def runtime() -> OperatorStoreRuntime:
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-12T04:30:00Z",
    )


def binding(call_key: str, call_id: str) -> dict:
    return {
        "schema_version": CALL_BINDING_SCHEMA,
        "call_key": call_key,
        "adapter_id": "ai-sdlc.openai.responses",
        "adapter_protocol_version": "1",
        "registration_digest": "1" * 64,
        "provider_scope_digest": "2" * 64,
        "call_id": call_id,
        "tool_name": "aisdlc_v1_feature_status",
        "capability": "feature.status",
        "arguments_digest": "3" * 64,
        "canonical_request_digest": "4" * 64,
        "canonical_request": {
            "api_version": "ai-sdlc.operator/v1",
            "request_id": "receipt-fixture",
            "capability": "feature.status",
            "client_identity": {"adapter_id": "ai-sdlc.openai.responses"},
            "payload": {},
            "target": {"repository": REPO, "feature_id": "F-RECEIPT-0001"},
        },
    }


def result(call_key: str, call_id: str, body: dict) -> dict:
    output = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "schema_version": CALL_RESULT_SCHEMA,
        "call_key": call_key,
        "canonical_response_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "function_call_output": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        },
    }


def raw_create_result(store: OperatorStoreRuntime, call_key: str, receipt: dict) -> None:
    def planner(snapshot):
        return StoreMutationPlan(
            expected_ref_sha=snapshot.ref_sha,
            mutations=(StoreMutation("create_immutable", call_result_path(call_key), receipt),),
            result={"created": True},
        )

    store.commit_replanned(planner)


def expect_candidate_rejected(receipt_mutator) -> None:
    store = runtime()
    journal = StoreResponsesCallJournal(store)
    call_key = "b" * 64
    call_id = "receipt-exact-call"
    journal.bind_call(call_key=call_key, binding=binding(call_key, call_id))
    candidate = result(call_key, call_id, {"ok": True, "result": {"revision": 7}})
    receipt_mutator(candidate)
    try:
        journal.record_result(call_key=call_key, result=candidate)
    except ResponsesJournalError:
        return
    raise AssertionError("invalid result candidate unexpectedly became durable replay authority")


def validate_exact_receipt() -> None:
    store = runtime()
    journal = StoreResponsesCallJournal(store)
    call_key = "a" * 64
    call_id = "receipt-valid-call"
    journal.bind_call(call_key=call_key, binding=binding(call_key, call_id))
    expected = result(call_key, call_id, {"ok": True, "result": {"revision": 7}})
    committed = journal.record_result(call_key=call_key, result=expected)
    require(committed == expected, "valid result receipt changed during commit")
    require(journal.lookup_result(call_key=call_key) == expected, "valid durable result did not replay exactly")


def validate_invalid_candidates() -> None:
    expect_candidate_rejected(
        lambda receipt: receipt["function_call_output"].__setitem__("call_id", "wrong-call")
    )
    expect_candidate_rejected(
        lambda receipt: receipt.__setitem__("canonical_response_digest", "0" * 64)
    )
    expect_candidate_rejected(
        lambda receipt: receipt["function_call_output"].__setitem__(
            "output", '{"result": {"revision": 7}, "ok": true}'
        )
    )
    expect_candidate_rejected(lambda receipt: receipt.__setitem__("unexpected", True))
    expect_candidate_rejected(
        lambda receipt: receipt["function_call_output"].__setitem__("type", "message")
    )


def validate_durable_corruption_fails_closed() -> None:
    store = runtime()
    journal = StoreResponsesCallJournal(store)
    call_key = "c" * 64
    call_id = "receipt-corrupt-call"
    journal.bind_call(call_key=call_key, binding=binding(call_key, call_id))
    corrupt = result(call_key, call_id, {"ok": True, "result": {"revision": 7}})
    corrupt["function_call_output"]["call_id"] = "other-call"
    raw_create_result(store, call_key, corrupt)
    try:
        StoreResponsesCallJournal(store).lookup_result(call_key=call_key)
    except ResponsesJournalCorrupt:
        pass
    else:
        raise AssertionError("fresh-process lookup accepted corrupt durable call correlation")

    orphan_store = runtime()
    orphan_key = "d" * 64
    orphan = result(orphan_key, "orphan-call", {"ok": True})
    raw_create_result(orphan_store, orphan_key, orphan)
    try:
        StoreResponsesCallJournal(orphan_store).lookup_result(call_key=orphan_key)
    except ResponsesJournalCorrupt:
        pass
    else:
        raise AssertionError("fresh-process lookup accepted result without immutable binding")


def main() -> None:
    validate_exact_receipt()
    validate_invalid_candidates()
    validate_durable_corruption_fails_closed()
    print("OpenAI Responses durable result-receipt validation passed")
    print("- replay receipt is bound to exact immutable call_id")
    print("- function_call_output shape and canonical JSON serialization are closed")
    print("- canonical response digest must match exact provider output bytes")
    print("- malformed candidate receipts fail before commit")
    print("- malformed durable/orphan receipts fail closed on fresh-process lookup")


if __name__ == "__main__":
    main()
