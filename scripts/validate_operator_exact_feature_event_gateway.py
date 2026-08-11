#!/usr/bin/env python3
"""Validate schema and exact-revision fences above the Event inbox transport."""
from __future__ import annotations

from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway
from operator_feature_event_validation import validate_trusted_feature_event
from operator_github_feature_event_gateway import FeatureEventGatewayError, PENDING
from operator_validated_feature_event_gateway import ValidatedGitHubFeatureEventInboxGateway
from validate_operator_github_feature_event_gateway import (
    EVENT_ID,
    FEATURE,
    REF,
    REPO,
    REV,
    FakeGitHub,
    event,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FixtureExactGateway(ExactRevisionGitHubFeatureEventGateway):
    # Low-level transport fixture Event is intentionally minimal. Revision-fence
    # tests isolate race behavior; schema behavior is tested separately below.
    @staticmethod
    def _schema_validate(event):
        return None


def fixture_gateway(fake):
    return FixtureExactGateway(
        token="trusted-event-writer",
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=2,
        poll_seconds=0,
    )


def canonical_fixture():
    return {
        "version": "0.1.0",
        "id": "EVT-EXACT-FIXTURE-0001",
        "feature_id": "F-EXACT-FIXTURE-0001",
        "expected_revision": 3,
        "occurred_at": "2026-08-11T05:00:00Z",
        "changes": [
            {"kind": "stage", "id": "implementation", "status": "WORKING"},
        ],
    }


def validate_pre_create_stale_fence():
    fake = FakeGitHub()
    fake.manifest["revision"] = REV + 1
    gw = fixture_gateway(fake)
    try:
        gw.submit_event(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            expected_revision=REV,
            event=event(),
        )
        raise AssertionError("stale Feature revision unexpectedly created Event inbox file")
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", exc)
    require(fake.put_count == 0, "stale pre-create fence wrote an Event")


def validate_pending_event_becomes_stale():
    fake = FakeGitHub()
    gw = fixture_gateway(fake)
    receipt = gw.submit_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(receipt.state == PENDING, receipt)
    require(fake.put_count == 1, fake.put_count)
    fake.manifest["revision"] = REV + 1
    fake.manifest["applied_events"] = []
    try:
        gw.lookup_receipt(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            event_id=EVENT_ID,
            expected_revision=REV,
        )
        raise AssertionError("pending Event remained retryable after Feature advanced without applying it")
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", exc)
    require(fake.put_count == 1, "stale pending Event caused another create")


def validate_invalid_event_fails_before_transport():
    fake = FakeGitHub()
    gw = ValidatedGitHubFeatureEventInboxGateway(
        token="trusted-event-writer",
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    invalid = {"id": EVENT_ID, "feature_id": FEATURE, "expected_revision": REV, "changes": []}
    try:
        gw.submit_event(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            expected_revision=REV,
            event=invalid,
        )
        raise AssertionError("schema-invalid Feature Event reached transport")
    except FeatureEventGatewayError as exc:
        require(exc.code == "INVALID_REQUEST", exc)
    require(fake.put_count == 0, "schema-invalid Event touched GitHub write transport")


def validate_canonical_schema_fixture():
    # The contract is validation-by-exception; successful validation returns None.
    validate_trusted_feature_event(canonical_fixture())


def main():
    validate_pre_create_stale_fence()
    validate_pending_event_becomes_stale()
    validate_invalid_event_fails_before_transport()
    validate_canonical_schema_fixture()
    print("Exact Feature Event gateway validation passed")
    print("- stale revision before create: zero Event writes")
    print("- pending Event + unrelated Feature advance: STALE_REVISION, no retry")
    print("- schema-invalid Event: rejected before transport")
    print("- self-contained canonical Feature Event remains schema-valid")


if __name__ == "__main__":
    main()
