#!/usr/bin/env python3
"""Validate fresh protection authority on every Operator Store CAS retry."""
from __future__ import annotations

from operator_store import plan_operation_start
from operator_store_backends import OperatorStoreRuntime, StoreBackendError
from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_ids
from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionReceipt

REPO = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-15T08:40:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class RuntimeRetryMemoryBackend(MemoryStateRefBackend):
    """Production-shaped test backend that forbids stale-receipt backend retries."""

    def commit_replanned(self, planner, receipt, *, max_attempts=4):
        raise AssertionError("OperatorStoreRuntime delegated CAS retries to backend with one receipt")


class SequencedProductionVerifier:
    test_only = False

    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    def verify(self, repository, state_ref):
        if self.calls >= len(self.observations):
            raise AssertionError("protection verifier called more times than expected")
        observation = self.observations[self.calls]
        self.calls += 1
        if isinstance(observation, BaseException):
            raise observation
        status, verifier_identity, policy_digest = observation
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=status,
            verifier_identity=verifier_identity,
            verified_at=f"{NOW}/attempt-{self.calls}",
            policy_digest=policy_digest,
        )


def start_plan(snapshot, feature_id="F-CAS-PROTECTION-0001", key="cas-protection"):
    return plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=feature_id,
        expected_revision=1,
        idempotency_key=key,
        occurred_at=NOW,
        trusted_context_digest="cas-protection-refresh-validator",
    )


def build_runtime(observations):
    backend = RuntimeRetryMemoryBackend(repository=REPO, state_ref=STATE_REF)
    verifier = SequencedProductionVerifier(observations)
    return backend, verifier, OperatorStoreRuntime(backend=backend, protection_verifier=verifier)


def expect_denied_after_conflict(observations, label):
    backend, verifier, runtime = build_runtime(observations)
    backend.inject_conflict_once()
    planned_refs = []

    def planner(snapshot):
        planned_refs.append(snapshot.ref_sha)
        return start_plan(snapshot, feature_id=f"F-{label.upper().replace('_', '-')}", key=label)

    try:
        runtime.commit_replanned(planner)
        raise AssertionError(f"{label}: retry unexpectedly wrote Store state")
    except StoreBackendError as exc:
        require(exc.code == "POLICY_DENIED", f"{label}: retry did not fail as POLICY_DENIED")

    require(verifier.calls == 2, f"{label}: retry did not obtain a fresh second protection receipt")
    require(planned_refs == [None], f"{label}: semantic planner reran after protection authority was denied")
    require(not operation_ids(backend.read_snapshot()), f"{label}: denied retry created semantic Store state")


def validate_negative_refresh_cases():
    stable = (PROTECTED, "github-ruleset:integration:4576406", "policy-a")
    expect_denied_after_conflict(
        [stable, (UNPROTECTED, stable[1], stable[2])],
        "unprotected_after_conflict",
    )
    expect_denied_after_conflict(
        [stable, (UNKNOWN, stable[1], stable[2])],
        "unknown_after_conflict",
    )
    expect_denied_after_conflict(
        [stable, RuntimeError("trusted verifier unavailable")],
        "verifier_failure_after_conflict",
    )
    expect_denied_after_conflict(
        [stable, (PROTECTED, stable[1], "policy-b")],
        "policy_digest_drift",
    )
    expect_denied_after_conflict(
        [stable, (PROTECTED, "different-verifier", stable[2])],
        "verifier_identity_drift",
    )


def validate_positive_refresh_and_replan():
    stable = (PROTECTED, "github-ruleset:integration:4576406", "policy-a")
    backend, verifier, runtime = build_runtime([stable, stable])
    backend.inject_conflict_once()
    planned_refs = []

    def planner(snapshot):
        planned_refs.append(snapshot.ref_sha)
        return start_plan(snapshot)

    result = runtime.commit_replanned(planner)
    require(verifier.calls == 2, "successful CAS retry did not refresh protection authority")
    require(planned_refs == [None, "conflict-1"], "CAS retry did not re-read and re-plan from fresh Store state")
    require(result.ref_sha == "memory-2", "successful retry produced unexpected Store ref")
    require(len(operation_ids(backend.read_snapshot())) == 1, "successful retry did not create exactly one Operation")


def main():
    validate_negative_refresh_cases()
    validate_positive_refresh_and_replan()
    print("Operator Store CAS protection refresh validation passed")


if __name__ == "__main__":
    main()
