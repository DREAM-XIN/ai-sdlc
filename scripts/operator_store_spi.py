#!/usr/bin/env python3
"""Storage-technology-neutral SPI for the durable Operator Store.

The semantic planner/model remains authoritative for Operation Store invariants.
A backend implements durable snapshot/CAS persistence only; it does not gain
Feature lifecycle, Gate, launch, Persist, or authorization authority.
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from operator_store_model import StoreMutationPlan, StoreSnapshot
from operator_store_protection import ProtectionReceipt

STORE_BACKEND_SPI_VERSION = "ai-sdlc.operator-store-backend/v1"
REFERENCE_BACKEND_ID = "remote-git-protected-ref-cas"

SEMANTIC_CONTRACT = (
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
)

FUTURE_BACKEND_TRIGGER_METRICS = (
    "state-ref-cas-conflict-rate",
    "operation-event-write-latency-p95",
    "operation-event-write-latency-p99",
    "github-api-rate-saturation",
    "callback-throughput",
    "write-amplification",
    "projection-rebuild-cost",
    "multi-tenant-active-operation-count",
    "github-branch-ruleset-api-availability-dependency",
)


@runtime_checkable
class OperationStoreCommitResult(Protocol):
    ref_sha: str
    snapshot: StoreSnapshot
    result: dict


@runtime_checkable
class OperationStoreBackend(Protocol):
    """Minimal durable persistence boundary consumed by OperatorStoreRuntime."""

    repository: str
    state_ref: str

    def read_snapshot(self) -> StoreSnapshot:
        ...

    def commit(
        self,
        plan: StoreMutationPlan,
        receipt: ProtectionReceipt | None,
    ) -> OperationStoreCommitResult:
        ...

    def commit_replanned(
        self,
        planner: Callable[[StoreSnapshot], StoreMutationPlan],
        receipt: ProtectionReceipt | None,
        *,
        max_attempts: int = 4,
    ) -> OperationStoreCommitResult:
        ...


def require_store_backend(backend: object) -> OperationStoreBackend:
    """Fail closed when trusted composition supplies an incompatible backend."""
    if not isinstance(backend, OperationStoreBackend):
        raise TypeError(f"Operator Store backend does not satisfy {STORE_BACKEND_SPI_VERSION}")
    repository = str(getattr(backend, "repository", ""))
    state_ref = str(getattr(backend, "state_ref", ""))
    if "/" not in repository:
        raise TypeError("Operator Store backend repository identity is invalid")
    if not state_ref.startswith("refs/heads/"):
        raise TypeError("Operator Store backend state ref must be a branch ref")
    return backend
