#!/usr/bin/env python3
"""Validate the Lane-B Feature Event seam already available on reviewed main.

This does not execute Lane B or the final Persist gateway. It proves that the
prepared Lane-B harness uses the real reviewed exact-revision Feature Event
transport from PR #247, with only the outer GitHub HTTP interaction emulated.
"""
from __future__ import annotations

from operator_github_feature_event_gateway import (
    FeatureEventGatewayError,
    _canonical_event_yaml,
    _digest,
)
from validate_operator_openai_responses_lane_b import (
    FEATURE,
    NOW,
    MutableFeatureTruth,
    _dynamic_feature_event_gateway,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    truth = MutableFeatureTruth(stage="code-review")
    gateway, http = _dynamic_feature_event_gateway(truth)
    event = {
        "version": "0.1.0",
        "id": "EVT-RESPONSES-LANE-B-SEAM",
        "feature_id": FEATURE,
        "expected_revision": 7,
        "occurred_at": NOW,
        "changes": [
            {"kind": "stage", "id": "code-review", "status": "WORKING"}
        ],
    }
    event_digest = _digest(_canonical_event_yaml(event))

    # Repository/ref are server-owned by ProductionConfiguredFeatureEventGateway;
    # callers supply only the configured Feature identity, exact revision and Event.
    first = gateway.persist_exact_event(
        feature_id=FEATURE,
        expected_revision=7,
        event=event,
    )
    require(first.state == "APPLIED", f"first exact Event write did not apply: {first}")
    require(first.event_id == event["id"], "exact Event receipt lost Event id")
    require(first.expected_revision == 7, "exact Event receipt lost original revision")
    require(first.result_revision == 8, "exact Event receipt did not prove 7 -> 8")
    require(truth.manifest["revision"] == 8, "outer HTTP seam did not advance trusted Feature truth")
    require(event["id"] in truth.manifest["applied_events"], "applied Event identity was not recorded")
    require(len(http.put_calls) == 1, "first exact Event write did not create exactly one inbox Event")

    # persist_exact_event is not a historical replay API. Once trusted Feature
    # truth advanced from revision 7 to 8, a second submission using stale
    # expected_revision=7 must fail the fresh revision fence before any second
    # inbox write. Restart recovery uses exact receipt lookup instead.
    try:
        gateway.persist_exact_event(
            feature_id=FEATURE,
            expected_revision=7,
            event=event,
        )
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", f"stale exact Persist failed with wrong code: {exc.code}")
    else:
        raise AssertionError("stale exact Persist unexpectedly bypassed the revision fence")
    require(len(http.put_calls) == 1, "stale exact Persist created a second inbox Event write")
    require(truth.manifest["revision"] == 8, "stale exact Persist advanced Feature revision twice")

    lookup = gateway.lookup_receipt(
        feature_id=FEATURE,
        event_id=event["id"],
        expected_revision=7,
        expected_event_digest=event_digest,
    )
    require(lookup.state == "APPLIED", "exact Event lookup did not recover APPLIED receipt")
    require(lookup.result_revision == 8, "exact Event lookup lost authoritative result revision")
    require(len(http.put_calls) == 1, "receipt lookup performed an external write")

    print("OpenAI Responses Lane-B exact Feature Event seam validation passed")
    print("- real ExactRevisionGitHubFeatureEventGateway is exercised")
    print("- repository/ref remain server-owned by configured production scope")
    print("- only outer GitHub HTTP is emulated")
    print("- first write proves exact revision 7 -> 8")
    print("- stale second Persist fails STALE_REVISION with zero second PUT")
    print("- exact-digest lookup recovers the original APPLIED receipt")
    print("- this is seam validation only, not Lane-B/full-Persist PASS")


if __name__ == "__main__":
    main()
