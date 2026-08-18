#!/usr/bin/env python3
"""WU3 crash/restart proof for the durable OpenAI Responses call journal.

This validator proves the Plan-required failure window where the canonical
Operator write has already converged but the adapter crashes before persisting
its Responses result receipt. Recovery must reuse the exact canonical
idempotency key, converge to one semantic write, repair the missing result
receipt, and then become journal-only/read-only on later fresh-process replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from operator_api import API_VERSION
from operator_openai_responses import (
    ADAPTER_ID,
    OpenAIResponsesOperatorAdapter,
    TrustedResponsesRegistration,
    responses_call_key,
)
from operator_openai_responses_journal import (
    StoreResponsesCallJournal,
    call_binding_path,
    call_result_path,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier

REPO = "DREAM-XIN/responses-crash-recovery-fixture"
FEATURE = "F-RESPONSES-CRASH-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _call() -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": "fc-responses-crash-recovery",
        "call_id": "responses-crash-recovery",
        "name": "aisdlc_v1_operation_start",
        "arguments": json.dumps(
            {
                "api_version": API_VERSION,
                "feature_id": FEATURE,
                "expected_feature_revision": 19,
                "mode": "ASSISTED",
            },
            separators=(",", ":"),
        ),
        "status": "completed",
    }


def _registration() -> TrustedResponsesRegistration:
    return TrustedResponsesRegistration(
        registration_id="responses-crash-registration",
        provider_scope_id="responses-crash-provider-scope",
        target_repository=REPO,
        feature_refs={FEATURE: "refs/heads/feature/F-RESPONSES-CRASH-0001"},
        trusted_context={
            "trusted_identity": {
                "service_id": "responses-crash-service",
                "runtime_id": "responses-crash-runtime",
                "authorization_context": "responses-crash-policy",
            },
            "trusted_scope": {"repositories": [REPO], "feature_ids": [FEATURE]},
            "trusted_principal": "responses-crash-principal",
        },
        human_principal="responses-crash-principal",
    )


def _runtime(backend: MemoryStateRefBackend) -> OperatorStoreRuntime:
    return OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T14:10:00Z",
    )


@dataclass
class DurableCanonicalState:
    results_by_idempotency: dict[str, dict[str, Any]] = field(default_factory=dict)
    semantic_writes: int = 0


class IdempotentStartBackend:
    """Deterministic stand-in for already-reviewed canonical idempotency semantics."""

    def __init__(self, state: DurableCanonicalState):
        self.state = state
        self.calls = 0

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        self.calls += 1
        require(request["capability"] == "operation.start", "unexpected crash-recovery capability")
        require(request["client_identity"]["adapter_id"] == ADAPTER_ID, "adapter identity drifted")
        require(
            request["target"] == {"repository": REPO, "feature_id": FEATURE},
            "trusted Feature target drifted",
        )
        key = request.get("idempotency_key")
        require(isinstance(key, str) and key.startswith("openai-responses/"), "stable idempotency missing")
        existing = self.state.results_by_idempotency.get(key)
        if existing is not None:
            return dict(existing)

        self.state.semantic_writes += 1
        result = {"operation_id": "op-responses-crash", "generation": 0, "status": "RUNNING"}
        self.state.results_by_idempotency[key] = dict(result)
        return result


class SimulatedProcessCrash(RuntimeError):
    pass


class CrashBeforeResultJournal:
    """Persist binding, then crash exactly when the result receipt would be written."""

    def __init__(self, delegate: StoreResponsesCallJournal):
        self.delegate = delegate
        self.crashed = False

    def bind_call(self, *, call_key: str, binding: dict[str, Any]):
        return self.delegate.bind_call(call_key=call_key, binding=binding)

    def lookup_result(self, *, call_key: str):
        return self.delegate.lookup_result(call_key=call_key)

    def record_result(self, *, call_key: str, result: dict[str, Any]):
        if not self.crashed:
            self.crashed = True
            raise SimulatedProcessCrash("crash after canonical write before Responses result receipt")
        return self.delegate.record_result(call_key=call_key, result=result)


def _decode(output: dict[str, Any]) -> dict[str, Any]:
    require(output.get("type") == "function_call_output", "recovered output type drifted")
    body = json.loads(output["output"])
    require(body.get("ok") is True, f"recovered canonical response failed: {body}")
    return body


def main() -> None:
    store_backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    registration = _registration()
    item = _call()
    call_key = responses_call_key(registration, item["call_id"])
    canonical_state = DurableCanonicalState()

    # Process 1: immutable binding lands, canonical semantic write converges, then
    # the process dies before the Responses result receipt can be committed.
    runtime1 = _runtime(store_backend)
    backend1 = IdempotentStartBackend(canonical_state)
    adapter1 = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"operation.start": backend1},
        journal=CrashBeforeResultJournal(StoreResponsesCallJournal(runtime1)),
    )
    try:
        adapter1.invoke_function_call(item)
    except SimulatedProcessCrash:
        pass
    else:
        raise AssertionError("crash-before-result fixture did not crash")

    after_crash = store_backend.read_snapshot()
    binding = after_crash.get(call_binding_path(call_key))
    require(isinstance(binding, dict), "immutable Responses call binding was not durable before crash")
    require(after_crash.get(call_result_path(call_key)) is None, "result receipt existed before crash recovery")
    require(backend1.calls == 1, "first process did not issue exactly one canonical dispatch")
    require(canonical_state.semantic_writes == 1, "first process did not converge one semantic write")
    durable_idempotency = binding["canonical_request"].get("idempotency_key")
    require(
        durable_idempotency in canonical_state.results_by_idempotency,
        "durable call binding idempotency does not identify converged canonical outcome",
    )

    # Process 2: reconstruct all adapter/journal objects over the same durable
    # Store. The missing result forces one canonical redispatch, but the exact
    # idempotency key must adopt the prior semantic outcome instead of applying a
    # second semantic write. The missing Responses result receipt is then repaired.
    runtime2 = _runtime(store_backend)
    backend2 = IdempotentStartBackend(canonical_state)
    adapter2 = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"operation.start": backend2},
        journal=StoreResponsesCallJournal(runtime2),
    )
    recovered = adapter2.invoke_function_call(item)
    body = _decode(recovered)
    require(
        body["result"]
        == {"operation_id": "op-responses-crash", "generation": 0, "status": "RUNNING"},
        "fresh-process recovery changed canonical outcome",
    )
    require(backend2.calls == 1, "fresh recovery did not perform exactly one canonical idempotent lookup/dispatch")
    require(canonical_state.semantic_writes == 1, "fresh recovery applied a second semantic write")
    after_recovery = store_backend.read_snapshot()
    result_receipt = after_recovery.get(call_result_path(call_key))
    require(isinstance(result_receipt, dict), "fresh recovery did not repair Responses result receipt")
    require(
        result_receipt.get("function_call_output") == recovered,
        "repaired result receipt does not match returned provider output",
    )

    # Process 3: once the receipt exists, replay must be journal-only/read-only and
    # must not re-enter canonical semantic dispatch at all.
    runtime3 = _runtime(store_backend)
    backend3 = IdempotentStartBackend(canonical_state)
    adapter3 = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={"operation.start": backend3},
        journal=StoreResponsesCallJournal(runtime3),
    )
    ref_before_replay = store_backend.read_snapshot().ref_sha
    replay = adapter3.invoke_function_call(item)
    require(replay == recovered, "fresh-process journal replay changed provider output")
    require(store_backend.read_snapshot().ref_sha == ref_before_replay, "journal replay mutated Store")
    require(backend3.calls == 0, "completed journal replay re-entered canonical backend")
    require(canonical_state.semantic_writes == 1, "completed replay applied another semantic write")

    print("OpenAI Responses crash-before-result recovery validation passed")
    print("- immutable call binding survives the simulated process crash")
    print("- canonical write lands once while the Responses result receipt is absent")
    print("- fresh process reuses the exact durable idempotency key and repairs the receipt")
    print("- canonical redispatch converges to one semantic write")
    print("- later fresh-process replay is journal-only, read-only, and performs zero backend calls")


if __name__ == "__main__":
    main()
