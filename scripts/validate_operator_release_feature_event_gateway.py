#!/usr/bin/env python3
"""Validate release-safe Event receipt recovery when inbox file is absent."""
from __future__ import annotations

from operator_github_feature_event_gateway import APPLIED, FeatureEventGatewayError
from operator_release_feature_event_gateway import (
    ReceiptSafeCanonicalFeatureEventGateway,
    build_release_decision_event_gateway,
)
from validate_operator_github_feature_event_gateway import EVENT_ID, FEATURE, REF, REPO, REV, FakeGitHub


class FixtureReleaseGateway(ReceiptSafeCanonicalFeatureEventGateway):
    @staticmethod
    def _schema_validate(event):
        return None


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def transport(fake):
    return FixtureReleaseGateway(
        token="trusted-event-writer",
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )


def validate_applied_without_inbox_file():
    fake = FakeGitHub()
    fake.event_text = None
    fake.manifest["applied_events"] = [EVENT_ID]
    fake.manifest["revision"] = REV + 1
    gw = transport(fake)
    receipt = gw.lookup_receipt(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        event_id=EVENT_ID,
        expected_revision=REV,
    )
    require(receipt.state == APPLIED, receipt)
    require(receipt.result_revision == REV + 1, receipt)
    require(receipt.event_blob_sha is None, receipt)
    require(fake.put_count == 0, "applied missing-inbox receipt caused a write")


def validate_absent_event_after_unrelated_advance_is_stale():
    fake = FakeGitHub()
    fake.event_text = None
    fake.manifest["applied_events"] = []
    fake.manifest["revision"] = REV + 1
    gw = transport(fake)
    try:
        gw.lookup_receipt(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            event_id=EVENT_ID,
            expected_revision=REV,
        )
        raise AssertionError("missing Event after unrelated revision advance unexpectedly remained ABSENT/retryable")
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", exc)
    require(fake.put_count == 0, "stale missing Event caused a write")


def validate_release_factory_uses_receipt_safe_transport():
    fake = FakeGitHub()
    gateway = build_release_decision_event_gateway(
        token="trusted-event-writer",
        repository=REPO,
        default_branch="main",
        feature_refs={FEATURE: REF},
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    require(isinstance(gateway.transport, ReceiptSafeCanonicalFeatureEventGateway), type(gateway.transport))


def main():
    validate_applied_without_inbox_file()
    validate_absent_event_after_unrelated_advance_is_stale()
    validate_release_factory_uses_receipt_safe_transport()
    print("Release-safe Feature Event receipt validation passed")
    print("- applied_events is authoritative even when inbox file is absent")
    print("- unrelated Feature advance + missing Event => STALE_REVISION")
    print("- both paths perform zero speculative Event writes")
    print("- release factory fixes canonical receipt-safe transport")


if __name__ == "__main__":
    main()
