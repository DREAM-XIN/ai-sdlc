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


class FakeConfiguredFeatureEventGateway:
    def __init__(self, manifest):
        self.manifest = manifest
        self.calls = []

    def read_feature(self, *, feature_id):
        self.calls.append(feature_id)
        return self.manifest


def validate_manifest_uses_configured_authority_only():
    gateway = FakeConfiguredFeatureEventGateway({"revision": 1})
    preflight = SimpleNamespace(
        composition=SimpleNamespace(feature_event_gateway=gateway),
        slot=SimpleNamespace(feature_id="F-OPERATOR-V03-FI-TEST-0001"),
    )
    manifest = subject._manifest(preflight)
    require(manifest == {"revision": 1}, "scenario Manifest read changed returned truth")
    require(
        gateway.calls == ["F-OPERATOR-V03-FI-TEST-0001"],
        "scenario Manifest read escaped configured Feature authority",
    )

    invalid = SimpleNamespace(
        composition=SimpleNamespace(
            feature_event_gateway=FakeConfiguredFeatureEventGateway({"revision": -1})
        ),
        slot=SimpleNamespace(feature_id="F-OPERATOR-V03-FI-TEST-0001"),
    )
    try:
        subject._manifest(invalid)
    except subject.V03DispatchRecoveryLiveError:
        pass
    else:
        raise AssertionError("invalid configured Feature Manifest was accepted")


def validate_closed_phase_map():
    require(set(subject.PHASE_SCENARIO.values()) == {subject.UNKNOWN, subject.CONCURRENT, subject.PREAUTH}, "#314 live phase map escaped closed trio")
    require(subject.IDEMPOTENCY.keys() == {subject.UNKNOWN, subject.CONCURRENT, subject.PREAUTH}, "#314 idempotency map escaped closed trio")
    require(len(set(subject.IDEMPOTENCY.values())) == 3, "#314 scenarios reuse one idempotency key")
    require(subject.UNKNOWN == "unknown-takeover", "UNKNOWN row identity drifted")
    require(
        subject.IDEMPOTENCY[subject.UNKNOWN] == "v03-release-fi-unknown-takeover-r2",
        "UNKNOWN recovery reused the consumed fail-closed Operation identity",
    )
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


def validate_legacy_unknown_cleanup_is_strictly_prelaunch_only():
    source = open(subject.__file__, encoding="utf-8").read()
    require(
        'LEGACY_UNKNOWN_IDEMPOTENCY = "v03-release-fi-unknown-takeover"' in source,
        "legacy UNKNOWN cleanup lost exact consumed identity",
    )
    require(
        'find_external_create_attempt(snapshot, external_dispatch_key=external_key) is not None' in source,
        "legacy UNKNOWN cleanup does not fail closed after external-create attempt",
    )
    require(
        'len(lookup) > 1' in source
        and 'not lookup and status != "WAITING_EXTERNAL"' in source
        and 'lookup and status != "BLOCKED"' in source,
        "legacy UNKNOWN cleanup does not distinguish exact authorized-precreate and BLOCKED-UNKNOWN shapes",
    )
    require(
        'lookup_payload.get("lookup_state") != "UNKNOWN"' in source,
        "legacy UNKNOWN cleanup does not validate UNKNOWN when a durable lookup exists",
    )
    require(
        'callbacks or persists' in source,
        "legacy UNKNOWN cleanup does not reject callback/Persist contamination",
    )
    require(
        '_retire_prelaunch_unknown_contamination(preflight)' in source,
        "UNKNOWN inject does not retire the exact old prelaunch contamination first",
    )


def validate_legacy_unknown_cleanup_accepts_exact_waiting_external_precreate():
    """Regression for the durable legacy shape observed on protected Store.

    launch.authorization moves the projection to WAITING_EXTERNAL before the
    one-shot external-create-attempt record exists.  Cleanup may cancel only
    that exact pre-create shape; the external-create absence check remains
    authoritative and all other shape checks remain fail-closed.
    """
    originals = {
        "_events": subject._events,
        "vertical_projection": subject.vertical_projection,
        "find_external_create_attempt": subject.find_external_create_attempt,
    }

    external_key = "dispatch-" + "a" * 40
    claim_id = "dc-" + "b" * 40
    operation_id = "op-" + "c" * 40
    projections = iter(({"status": "WAITING_EXTERNAL", "generation": 0}, {"status": "CANCELLED", "generation": 0}))

    class Backend:
        def read_snapshot(self):
            return object()

    class Runtime:
        def __init__(self):
            self.backend = Backend()
            self.clock = lambda: "2026-09-26T00:00:00Z"
            self.commits = 0

        def commit_replanned(self, planner, *, max_attempts=4):
            self.commits += 1
            # The production planner is already covered by Store cancellation
            # validation; this test isolates the live-cleanup admission fence.
            return SimpleNamespace(result={"status": "CANCELLED"})

    runtime = Runtime()
    preflight = SimpleNamespace(
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc"),
        slot=SimpleNamespace(feature_id="F-OPERATOR-V03-FI-UNKNOWN-TAKEOVER-0001"),
        composition=SimpleNamespace(
            bundle=SimpleNamespace(
                runtime=runtime,
                executor=SimpleNamespace(
                    base=SimpleNamespace(
                        config=SimpleNamespace(trusted_context_digest="ctx")
                    )
                ),
            )
        ),
    )

    def fake_events(_preflight, _operation_id, event_type=None, generation=None):
        require(_operation_id == operation_id, "cleanup targeted wrong legacy Operation")
        if event_type is None:
            return [{"event_type": "operation.started"}]
        if event_type == "dispatch.claimed":
            return [{
                "payload": {
                    "claim_id": claim_id,
                    "external_dispatch_key": external_key,
                    "semantic_effect_key": "d" * 64,
                }
            }]
        if event_type == "dispatch.launch.authorized":
            return [{
                "payload": {
                    "claim_id": claim_id,
                    "external_dispatch_key": external_key,
                }
            }]
        return []

    try:
        subject._events = fake_events
        subject.vertical_projection = lambda *_args, **_kwargs: next(projections)
        subject.find_external_create_attempt = lambda *_args, **_kwargs: None
        # Fix the legacy Operation identity to keep the fake event fixture small.
        original_operation_id_for = subject.operation_id_for
        subject.operation_id_for = lambda *_args, **_kwargs: operation_id
        try:
            subject._retire_prelaunch_unknown_contamination(preflight)
        finally:
            subject.operation_id_for = original_operation_id_for
        require(runtime.commits == 1, "exact WAITING_EXTERNAL pre-create contamination was not retired")
    finally:
        subject._events = originals["_events"]
        subject.vertical_projection = originals["vertical_projection"]
        subject.find_external_create_attempt = originals["find_external_create_attempt"]


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
    validate_manifest_uses_configured_authority_only()
    validate_closed_phase_map()
    validate_unknown_wrapper()
    validate_no_external_access_fence()
    validate_preauthorization_crash_boundary()
    validate_legacy_unknown_cleanup_is_strictly_prelaunch_only()
    validate_legacy_unknown_cleanup_accepts_exact_waiting_external_precreate()
    validate_generic_record_is_anti_overclaim()
    print("PASS: #314 dispatch/recovery live wrappers are closed, zero-effect in PR validation, and fail-closed")
    print("- UNKNOWN permits one exact delegated launch then suppresses certainty without fallback lookup")
    print("- pre-authorization crash fires only after durable reservation and before any claim-bearing result")
    print("- stable recovery fence prevents all delegated external access")
    print("- scenario records remain one-row-only and cannot claim overall Issue #221 PASS")


if __name__ == "__main__":
    main()
