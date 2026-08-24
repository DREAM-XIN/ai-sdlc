#!/usr/bin/env python3
"""Verify reservation-durable crash recovery before launch authorization.

This deterministic harness covers the explicit Frozen v0.3 Release Spec §19.6
window:

    semantic reservation committed -> process crash -> fresh process resumes
    before any launch authorization or external POST occurred.

It uses the lineage-required Vertical executor and the same durable Store backend
across process objects. It is verification support only, never release-eligible
Issue #221 runtime evidence.
"""
from __future__ import annotations

from operator_store import plan_operation_start
from operator_store_model import operation_events, rebuild_projection
from operator_vertical import VERTICAL_PROFILE
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    IDEMPOTENCY,
    REPOSITORY,
    TRUSTED_DIGEST,
    Clock,
    ExternalRuntime,
    FeatureGateway,
    LookupFirstDispatchGateway,
    derive_lost_ack_dispatch_binding,
    event_count,
    make_executor,
    make_runtime,
    manifest,
)

RESERVATION_PREFIX = "state/operator/v1/reservations/external/"
CLAIM_PREFIX = "state/operator/v1/claims/dispatch/"


class InjectedPreAuthorizationCrash(BaseException):
    code = "FI_CRASH_AFTER_RESERVATION_BEFORE_LAUNCH_AUTHORIZATION"

    def __init__(self, semantic_effect_key: str, external_dispatch_key: str):
        self.semantic_effect_key = semantic_effect_key
        self.external_dispatch_key = external_dispatch_key
        super().__init__(self.code)


class CrashAfterDurableReservationRuntime:
    """Delegate real commits, then crash exactly after the first reservation commit."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.backend = delegate.backend
        self.clock = delegate.clock
        self.injected = False
        self.reservation = None

    def protected_receipt(self):
        return self.delegate.protected_receipt()

    def commit_replanned(self, planner):
        result = self.delegate.commit_replanned(planner)
        payload = getattr(result, "result", None)
        if (
            not self.injected
            and isinstance(payload, dict)
            and payload.get("semantic_effect_key")
            and payload.get("external_dispatch_key")
            and not payload.get("claim_id")
        ):
            self.injected = True
            self.reservation = {
                "semantic_effect_key": str(payload["semantic_effect_key"]),
                "external_dispatch_key": str(payload["external_dispatch_key"]),
            }
            raise InjectedPreAuthorizationCrash(
                self.reservation["semantic_effect_key"],
                self.reservation["external_dispatch_key"],
            )
        return result


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def prefixed_paths(snapshot, prefix):
    return sorted(path for path in snapshot.files if path.startswith(prefix))


def main():
    fixture_manifest = manifest()
    binding = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref="feature/F-LOST-ACK-ORCHESTRATION-0001",
        manifest=fixture_manifest,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
        idempotency_key=IDEMPOTENCY,
        occurred_at="2026-08-11T00:00:00Z",
    )

    # One durable backend models the protected Store. Each runtime/executor below
    # is a distinct process object over that same durable authority.
    from operator_store_git import MemoryStateRefBackend

    backend = MemoryStateRefBackend(
        repository=REPOSITORY,
        state_ref="refs/heads/ai-sdlc-operator-state",
    )
    clock = Clock()
    external = ExternalRuntime()
    feature_gateway = FeatureGateway(fixture_manifest)

    bootstrap_runtime = make_runtime(backend, clock)
    start = bootstrap_runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPOSITORY,
            feature_id=FEATURE,
            expected_revision=11,
            idempotency_key=IDEMPOTENCY,
            occurred_at=clock(),
            trusted_context_digest=TRUSTED_DIGEST,
            operation_profile=VERTICAL_PROFILE,
        )
    )
    operation_id = str(start.result["operation_id"])
    require(operation_id == binding.operation_id, "operation identity drifted from trusted dispatch binding")

    phase1_delegate = make_runtime(backend, clock)
    phase1_runtime = CrashAfterDurableReservationRuntime(phase1_delegate)
    phase1_transport = LookupFirstDispatchGateway(external)
    phase1_executor = make_executor(phase1_runtime, feature_gateway, phase1_transport)

    try:
        phase1_executor.advance_until_stop(operation_id=operation_id)
    except InjectedPreAuthorizationCrash as exc:
        require(exc.semantic_effect_key == binding.semantic_effect_key, "crash bound wrong semantic effect")
        require(exc.external_dispatch_key == binding.external_dispatch_key, "crash bound wrong external dispatch key")
    else:
        raise AssertionError("pre-authorization crash was not injected")

    require(phase1_runtime.injected is True, "reservation crash wrapper did not inject")
    require(phase1_runtime.reservation is not None, "reservation crash wrapper lost durable identity")

    after_crash = backend.read_snapshot()
    reservations = prefixed_paths(after_crash, RESERVATION_PREFIX)
    claims = prefixed_paths(after_crash, CLAIM_PREFIX)
    require(len(reservations) == 1, f"crash window expected one durable reservation, got {reservations}")
    require(claims == [], f"crash happened after dispatch claim instead of immediately after reservation: {claims}")
    require(
        event_count(backend, operation_id, "dispatch.launch.authorized", 0) == 0,
        "crashed process durably authorized a launch",
    )
    require(external.post_count == 0, "crashed process reached external POST")
    require(phase1_transport.launch_calls == [], "crashed process called dispatch launch")
    require(phase1_transport.lookup_calls == [], "crashed process called dispatch lookup")

    # Fresh process: no wrapper state is reused, only the durable Store and
    # external authority survive. Replanning must converge on the same exact
    # reservation/key, then create one claim/auth/launch.
    phase2_runtime = make_runtime(backend, clock)
    phase2_transport = LookupFirstDispatchGateway(external)
    phase2_executor = make_executor(phase2_runtime, feature_gateway, phase2_transport)
    recovered = phase2_executor.advance_until_stop(operation_id=operation_id)

    require(recovered["status"] == "WAITING_EXTERNAL", f"fresh recovery did not reach WAITING_EXTERNAL: {recovered['status']}")
    require(external.post_count == 1, "fresh recovery did not produce exactly one external POST")
    require(
        phase2_transport.launch_calls == [binding.external_dispatch_key],
        "fresh recovery did not launch the exact pre-crash external key",
    )
    require(
        external.runs.get(binding.external_dispatch_key) == "run-1",
        "fresh recovery did not bind exact modeled runtime receipt",
    )

    recovered_snapshot = backend.read_snapshot()
    require(
        prefixed_paths(recovered_snapshot, RESERVATION_PREFIX) == reservations,
        "fresh recovery created a second semantic reservation",
    )
    require(len(prefixed_paths(recovered_snapshot, CLAIM_PREFIX)) == 1, "fresh recovery did not converge on one dispatch claim")
    require(
        event_count(backend, operation_id, "dispatch.launch.authorized", 0) == 1,
        "fresh recovery did not create exactly one launch authorization",
    )
    require(
        event_count(backend, operation_id, "dispatch.launch.lookup-recorded", 0) == 1,
        "fresh recovery did not durably record the one launch receipt",
    )
    final_projection = rebuild_projection(recovered_snapshot, operation_id)
    require(
        binding.external_dispatch_key in final_projection["authorized_dispatches"],
        "final projection lost recovered external dispatch authority",
    )

    # A second fresh process must observe the durable WAITING_EXTERNAL stable
    # stop and perform zero dispatch/lookup calls.
    phase3_runtime = make_runtime(backend, clock)
    phase3_transport = LookupFirstDispatchGateway(external)
    phase3_executor = make_executor(phase3_runtime, feature_gateway, phase3_transport)
    second = phase3_executor.advance_until_stop(operation_id=operation_id)
    require(second["status"] == "WAITING_EXTERNAL", "second recovery changed stable stop")
    require(external.post_count == 1, "second recovery created a duplicate external effect")
    require(phase3_transport.launch_calls == [], "second recovery re-entered launch")
    require(phase3_transport.lookup_calls == [], "second recovery performed unnecessary lookup")

    print("v0.3 reservation-committed pre-authorization crash recovery validation passed")
    print("- lineage-required executor durably committed one semantic reservation before injected process crash")
    print("- crashed process created zero dispatch claims, launch authorizations, lookups or external POSTs")
    print("- fresh process reused the exact semantic/external key and created one claim/authorization/external run")
    print("- second fresh process observed durable WAITING_EXTERNAL with zero new external effect")
    print("- deterministic required-test support only; not Issue #221 real-runtime release evidence")


if __name__ == "__main__":
    main()
