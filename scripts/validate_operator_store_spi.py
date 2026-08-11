#!/usr/bin/env python3
"""Deterministic conformance checks for ai-sdlc.operator-store-backend/v1."""
from __future__ import annotations

from pathlib import Path

from operator_store_git import CasConflict, GitStateRefBackend, MemoryStateRefBackend
from operator_store_model import StoreMutation, StoreMutationPlan, decision_path
from operator_store_protection import PROTECTED, ProtectionError, ProtectionReceipt
from operator_store_remote_git import RemoteGitStateRefBackend
from operator_store_spi import (
    FUTURE_BACKEND_TRIGGER_METRICS,
    REFERENCE_BACKEND_ID,
    SEMANTIC_CONTRACT,
    STORE_BACKEND_SPI_VERSION,
    OperationStoreBackend,
    require_store_backend,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-11T00:00:00Z"

EXPECTED_SEMANTIC_CONTRACT = {
    "append-immutable-operation-events",
    "deterministic-snapshot-replay-and-projection-rebuild",
    "create-once-semantic-reservations",
    "generation-feature-and-dispatch-claims",
    "compare-and-set-conflict-detection",
    "exact-effect-and-operation-lookup",
    "launch-and-persist-receipt-correlation",
    "positive-protection-proof-before-semantic-write",
    "concurrency-safe-replan-after-conflict",
    "idempotent-equivalent-write-convergence",
}
EXPECTED_TRIGGER_METRICS = {
    "state-ref-cas-conflict-rate",
    "operation-event-write-latency-p95",
    "operation-event-write-latency-p99",
    "github-api-rate-saturation",
    "callback-throughput",
    "write-amplification",
    "projection-rebuild-cost",
    "multi-tenant-active-operation-count",
    "github-branch-ruleset-api-availability-dependency",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def protected_receipt():
    return ProtectionReceipt(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        status=PROTECTED,
        verifier_identity="spi-conformance",
        verified_at=NOW,
        policy_digest="spi-policy",
    )


def validate_structural_spi():
    memory = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    local_git = GitStateRefBackend(repo_path=ROOT, repository=REPOSITORY, state_ref=STATE_REF)
    remote_git = RemoteGitStateRefBackend(repo_path=ROOT, repository=REPOSITORY, state_ref=STATE_REF)

    for backend in (memory, local_git, remote_git):
        require(isinstance(backend, OperationStoreBackend), f"{type(backend).__name__} does not satisfy frozen Store SPI")
        require(require_store_backend(backend) is backend, "Store SPI guard did not preserve conforming backend")

    class InvalidBackend:
        repository = REPOSITORY
        state_ref = STATE_REF

    try:
        require_store_backend(InvalidBackend())
        raise AssertionError("incomplete backend unexpectedly passed frozen Store SPI guard")
    except TypeError:
        pass

    class CallerSelectedRefBackend(MemoryStateRefBackend):
        pass

    bad = CallerSelectedRefBackend(repository=REPOSITORY, state_ref="feature/user-selected")
    try:
        require_store_backend(bad)
        raise AssertionError("non-branch Store state ref unexpectedly passed SPI guard")
    except TypeError:
        pass


def validate_backend_cas_and_protection_contract():
    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    path = decision_path("spi-conformance-decision")
    first_plan = StoreMutationPlan(
        expected_ref_sha=None,
        mutations=(StoreMutation("create_immutable", path, {"id": "spi-conformance-decision"}),),
        result={"stored": True},
    )

    try:
        backend.commit(first_plan, None)
        raise AssertionError("Store backend semantic write succeeded without positive protection receipt")
    except ProtectionError:
        pass

    committed = backend.commit(first_plan, protected_receipt())
    require(committed.result == {"stored": True}, "Store backend commit result changed")
    require(committed.snapshot.get(path) == {"id": "spi-conformance-decision"}, "Store backend lost immutable object")

    stale_plan = StoreMutationPlan(
        expected_ref_sha=None,
        mutations=(),
        result={"stale": True},
    )
    try:
        backend.commit(stale_plan, protected_receipt())
        raise AssertionError("stale Store backend CAS unexpectedly succeeded")
    except CasConflict:
        pass

    replanned = backend.commit_replanned(
        lambda snapshot: StoreMutationPlan(
            expected_ref_sha=snapshot.ref_sha,
            mutations=(),
            result={"replanned": True},
        ),
        protected_receipt(),
    )
    require(replanned.result == {"replanned": True}, "Store backend re-plan contract changed")


def validate_contract_metadata():
    require(STORE_BACKEND_SPI_VERSION == "ai-sdlc.operator-store-backend/v1", "Store SPI version drifted")
    require(REFERENCE_BACKEND_ID == "remote-git-protected-ref-cas", "v0.3 reference backend identity drifted")
    require(set(SEMANTIC_CONTRACT) == EXPECTED_SEMANTIC_CONTRACT, "Store semantic contract is incomplete")
    require(set(FUTURE_BACKEND_TRIGGER_METRICS) == EXPECTED_TRIGGER_METRICS, "future-backend trigger metrics drifted")
    doc = ROOT / "docs" / "operator-store-spi.md"
    require(doc.is_file(), "Store SPI architecture document is missing")
    text = doc.read_text(encoding="utf-8")
    require("v0.3 reference backend" in text, "Store SPI document does not name the v0.3 reference-backend boundary")
    require("does not introduce Postgres, Redis, EventStore" in text, "Store SPI document must explicitly reject premature backend rewrite")


def main():
    validate_contract_metadata()
    validate_structural_spi()
    validate_backend_cas_and_protection_contract()
    print("Operator Store SPI validation passed")
    print(f"- spi: {STORE_BACKEND_SPI_VERSION}")
    print(f"- reference backend: {REFERENCE_BACKEND_ID}")
    print(f"- semantic requirements: {len(SEMANTIC_CONTRACT)}")
    print(f"- future-backend trigger metrics: {len(FUTURE_BACKEND_TRIGGER_METRICS)}")


if __name__ == "__main__":
    main()
