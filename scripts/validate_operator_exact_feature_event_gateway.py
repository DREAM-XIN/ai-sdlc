#!/usr/bin/env python3
"""Validate schema and exact-revision fences above the Event inbox transport."""
from __future__ import annotations

import yaml
from pathlib import Path

from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway
from operator_feature_event_validation import TrustedFeatureEventValidationError, validate_trusted_feature_event
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

ROOT = Path(__file__).resolve().parents[1]


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


def validate_repository_has_schema_valid_event_fixture():
    # Protect against a validator that accidentally rejects the repository's own
    # canonical Event contract. We do not mutate or submit this fixture.
    candidates = []
    for base in (ROOT / "examples", ROOT / "events", ROOT / "tests" / "fixtures"):
        if base.exists():
            candidates.extend(base.rglob("*.yaml"))
    found = None
    for path in candidates:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            validate_trusted_feature_event(doc)
            found = path
            break
        except (TrustedFeatureEventValidationError, yaml.YAMLError, OSError):
            continue
    require(found is not None, "no existing repository fixture satisfies Feature Event schema")


def main():
    validate_pre_create_stale_fence()
    validate_pending_event_becomes_stale()
    validate_invalid_event_fails_before_transport()
    validate_repository_has_schema_valid_event_fixture()
    print("Exact Feature Event gateway validation passed")
    print("- stale revision before create: zero Event writes")
    print("- pending Event + unrelated Feature advance: STALE_REVISION, no retry")
    print("- schema-invalid Event: rejected before transport")
    print("- repository canonical Feature Event fixture remains schema-valid")


if __name__ == "__main__":
    main()
