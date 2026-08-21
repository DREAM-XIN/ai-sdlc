#!/usr/bin/env python3
"""Deterministic zero-effect validation for the #314 dispatch/recovery live wrappers."""
from __future__ import annotations

from types import SimpleNamespace

import v03_dispatch_recovery_live_runner as subject


def require(value, message):
    if not value:
        raise AssertionError(message)


class FakeLaunchDelegate:
    def __init__(self, receipt=None):
        self.receipt = receipt or {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}
        self.launches = []
        self.lookups = []

    def launch(self, *, dispatch):
        self.launches.append(dict(dispatch))
        return dict(self.receipt)

    def lookup(self, *, external_dispatch_key):
        self.lookups.append(external_dispatch_key)
        return dict(self.receipt)


class FakeRuntime:
    def __init__(self, results):
        self.backend = object()
        self.clock = lambda: "now"
        self.results = list(results)
        self.protection_calls = 0
        self.commit_calls = 0

    def protected_receipt(self):
        self.protection_calls += 1
        return "receipt"

    def commit_replanned(self, planner, *, max_attempts=4):
        self.commit_calls += 1
        if not self.results:
            raise AssertionError("unexpected fake runtime commit")
        return SimpleNamespace(result=self.results.pop(0))


def validate_closed_phase_map():
    require(set(subject.PHASE_SCENARIO.values()) == {subject.UNKNOWN, subject.CONCURRENT, subject.PREAUTH}, "#314 live phase map escaped closed trio")
    require(subject.IDEMPOTENCY.keys() == {subject.UNKNOWN, subject.CONCURRENT, subject.PREAUTH}, "#314 idempotency map escaped closed trio")
    require(len(set(subject.IDEMPOTENCY.values())) == 3, "#314 scenarios reuse one idempotency key")
    require(subject.UNKNOWN == "unknown-takeover", "UNKNOWN row identity drifted")
    require(subject.CONCURRENT == "concurrent-resume", "concurrent row identity drifted")
    require(subject.PREAUTH == "reservation-committed-pre-authorization-crash-recovery", "preauth row identity drifted")


def validate_unknown_wrapper():
    delegate = FakeLaunchDelegate()
    wrapper = subject.UnknownAfterProductionLaunchGateway(delegate)
    dispatch = {"semantic_effect_key": "semantic", "external_dispatch_key": "external"}
    receipt = wrapper.launch(dispatch=dispatch)
    require(receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}, "UNKNOWN wrapper leaked actual launch certainty")
    require(wrapper.launch_calls == 1 and wrapper.lookup_calls == 0, "UNKNOWN wrapper call count drifted")
    require(wrapper.actual_receipt == {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}, "UNKNOWN wrapper did not retain exact real receipt for evidence")
    require(wrapper.dispatch == dispatch, "UNKNOWN wrapper lost exact dispatch identity")
    try:
        wrapper.lookup(external_dispatch_key="external")
    except subject.V03DispatchRecoveryLiveError:
        pass
    else:
        raise AssertionError("UNKNOWN wrapper allowed fallback lookup")
    try:
        wrapper.launch(dispatch=dispatch)
    except subject.V03DispatchRecoveryLiveError:
        pass
    else:
        raise AssertionError("UNKNOWN wrapper allowed duplicate production launch")

    for bad in (
        {"lookup_state": "UNKNOWN", "receipt_id": None},
        {"lookup_state": "NOT_LAUNCHED", "receipt_id": None},
        {"lookup_state": "LAUNCHED", "receipt_id": None},
        None,
    ):
        bad_delegate = FakeLaunchDelegate(receipt=bad) if bad is not None else FakeLaunchDelegate(receipt={})
        if bad is None:
            bad_delegate.receipt = None
            def invalid_launch(*, dispatch):
                bad_delegate.launches.append(dict(dispatch))
                return None
            bad_delegate.launch = invalid_launch
        try:
            subject.UnknownAfterProductionLaunchGateway(bad_delegate).launch(dispatch=dispatch)
        except subject.V03DispatchRecoveryLiveError:
            continue
        raise AssertionError(f"UNKNOWN wrapper accepted non-exact launch receipt: {bad!r}")


def validate_no_external_access_fence():
    delegate = FakeLaunchDelegate()
    fence = subject.NoExternalAccessGateway(delegate)
    try:
        fence.launch(dispatch={})
    except subject.V03DispatchRecoveryLiveError:
        pass
    else:
        raise AssertionError("stable-recovery fence allowed launch")
    try:
        fence.lookup(external_dispatch_key="key")
    except subject.V03DispatchRecoveryLiveError:
        pass
    else:
        raise AssertionError("stable-recovery fence allowed lookup")
    require(fence.launch_calls == 1 and fence.lookup_calls == 1, "stable-recovery fence did not account attempted access")
    require(delegate.launches == [] and delegate.lookups == [], "stable-recovery fence leaked access to production delegate")


def validate_preauthorization_crash_boundary():
    unrelated = {"operation_id": "op", "status": "RUNNING"}
    reservation = {
        "semantic_effect_key": "a" * 64,
        "external_dispatch_key": "external-key",
        "effect_lineage_id": "lineage",
    }
    delegate = FakeRuntime([unrelated, reservation])
    wrapper = subject.CrashAfterDurableReservationRuntime(delegate)
    first = wrapper.commit_replanned(lambda _snapshot: None)
    require(first.result == unrelated and wrapper.injected is False, "crash wrapper injected before semantic reservation")
    try:
        wrapper.commit_replanned(lambda _snapshot: None)
    except subject.InjectedPreAuthorizationCrash:
        pass
    else:
        raise AssertionError("crash wrapper did not terminate after durable reservation")
    require(wrapper.injected is True, "crash wrapper lost injected state")
    require(wrapper.reservation == {"semantic_effect_key": "a" * 64, "external_dispatch_key": "external-key"}, "crash wrapper lost exact reservation/external identity")
    require(wrapper.backend is delegate.backend and wrapper.clock is delegate.clock, "crash wrapper created alternate Store/clock authority")
    require(wrapper.protected_receipt() == "receipt" and delegate.protection_calls == 1, "crash wrapper bypassed production protection delegate")

    # A result that already carries a dispatch claim must never be treated as the
    # reservation-before-authorization crash point.
    claimed = dict(reservation, claim_id="claim-1")
    delegate2 = FakeRuntime([claimed])
    wrapper2 = subject.CrashAfterDurableReservationRuntime(delegate2)
    result = wrapper2.commit_replanned(lambda _snapshot: None)
    require(result.result == claimed and wrapper2.injected is False, "crash wrapper injected after dispatch claim")

    blocked = dict(reservation, status="BLOCKED")
    delegate3 = FakeRuntime([blocked])
    wrapper3 = subject.CrashAfterDurableReservationRuntime(delegate3)
    result = wrapper3.commit_replanned(lambda _snapshot: None)
    require(result.result == blocked and wrapper3.injected is False, "crash wrapper injected on blocked lineage proposal")


def validate_generic_record_is_anti_overclaim():
    record = subject._generic_record(
        scenario=subject.UNKNOWN,
        operation_id="op",
        generation=1,
        semantic_effect_key="semantic",
        external_dispatch_key="external",
        candidate_head_sha="1" * 40,
        feature_revision_before=1,
        runtime_lookup_state="UNKNOWN",
        runtime_receipt_identity="run-1",
        measurements={
            "duplicate_external_effect_count": 0,
            "speculative_retry_under_unknown_count": 0,
        },
    )
    require(record["status"] == "PASS", "scenario record did not mark its own row PASS")
    require(record["completed_issue_221_scenarios"] == [subject.UNKNOWN], "scenario record claimed extra Issue #221 rows")
    require(record["overall_issue_221_pass"] is False, "single scenario record attempted overall Issue #221 PASS")
    require(record["measurements"] == {
        "duplicate_external_effect_count": 0,
        "speculative_retry_under_unknown_count": 0,
    }, "single scenario record measurement set drifted")


def main():
    validate_closed_phase_map()
    validate_unknown_wrapper()
    validate_no_external_access_fence()
    validate_preauthorization_crash_boundary()
    validate_generic_record_is_anti_overclaim()
    print("PASS: #314 dispatch/recovery live wrappers are closed, zero-effect in PR validation, and fail-closed")
    print("- UNKNOWN permits one exact delegated launch then suppresses certainty without fallback lookup")
    print("- pre-authorization crash fires only after durable reservation and before any claim-bearing result")
    print("- stable recovery fence prevents all delegated external access")
    print("- scenario records remain one-row-only and cannot claim overall Issue #221 PASS")


if __name__ == "__main__":
    main()
