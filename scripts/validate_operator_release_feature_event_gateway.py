#!/usr/bin/env python3
"""Validate release-safe exact Event receipt recovery after inbox cleanup."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, unquote, urlparse

from operator_github_feature_event_gateway import APPLIED, UNKNOWN, FeatureEventGatewayError
from operator_release_feature_event_gateway import (
    ReceiptSafeCanonicalFeatureEventGateway,
    build_release_decision_event_gateway,
)
from validate_operator_github_feature_event_gateway import (
    EVENT_ID,
    EVENT_PATH,
    FEATURE,
    REF,
    REPO,
    REV,
    FakeGitHub,
    content_payload,
    event,
)


class FixtureReleaseGateway(ReceiptSafeCanonicalFeatureEventGateway):
    @staticmethod
    def _schema_validate(event):
        return None


class HistoryFakeGitHub(FakeGitHub):
    """Expose immutable Git path history after the current inbox file is gone."""

    def __init__(self):
        super().__init__()
        self.history_texts: list[str] = []
        self.history_unavailable = False
        self.history_lookup_count = 0

    def __call__(self, method, url, headers, body=None):
        parsed = urlparse(url)
        if method == "GET" and parsed.path == f"/repos/{REPO}/commits":
            self.history_lookup_count += 1
            if self.history_unavailable:
                return 503, {}
            query = parse_qs(parsed.query)
            if query.get("sha", [None])[0] != REF:
                return 404, {}
            if unquote(query.get("path", [""])[0]) != EVENT_PATH:
                return 404, {}
            # The newest path touch is the cleanup/delete commit. Earlier rows
            # model immutable commits where the exact Event file still existed.
            rows = [{"sha": "event-cleanup-sha"}]
            rows.extend({"sha": f"event-history-{index}"} for index, _ in enumerate(self.history_texts))
            return 200, rows

        prefix = f"/repos/{REPO}/contents/"
        if method == "GET" and parsed.path.startswith(prefix):
            path = "/".join(unquote(part) for part in parsed.path[len(prefix):].split("/"))
            query_ref = parse_qs(parsed.query).get("ref", [REF])[0]
            if path == EVENT_PATH and query_ref == "event-cleanup-sha":
                return 404, {}
            if path == EVENT_PATH and query_ref.startswith("event-history-"):
                try:
                    index = int(query_ref.rsplit("-", 1)[1])
                    text = self.history_texts[index]
                except (ValueError, IndexError):
                    return 404, {}
                return 200, content_payload(text, f"history-blob-{index}")

        return super().__call__(method, url, headers, body)


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


def canonical_event(summary="accepted"):
    _, text = FixtureReleaseGateway._validate_event(
        event(summary=summary),
        feature_id=FEATURE,
        expected_revision=REV,
    )
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def lookup_applied(fake, *, expected_event_digest):
    return transport(fake).lookup_receipt(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        event_id=EVENT_ID,
        expected_revision=REV,
        expected_event_digest=expected_event_digest,
    )


def applied_cleanup_fixture(*, later_events=False):
    fake = HistoryFakeGitHub()
    text, digest = canonical_event()
    fake.history_texts = [text]
    fake.event_text = None
    fake.manifest["applied_events"] = [EVENT_ID]
    fake.manifest["revision"] = REV + 1
    if later_events:
        fake.manifest["applied_events"].extend(["EVT-LATER-1", "EVT-LATER-2"])
        fake.manifest["revision"] = REV + 3
    return fake, digest


def validate_applied_without_inbox_file():
    fake, digest = applied_cleanup_fixture()
    receipt = lookup_applied(fake, expected_event_digest=digest)
    require(receipt.state == APPLIED, receipt)
    require(receipt.result_revision == REV + 1, receipt)
    require(receipt.event_blob_sha == "history-blob-0", receipt)
    require(fake.history_lookup_count == 1, "cleanup-safe receipt did not verify Git history")
    require(fake.put_count == 0, "applied missing-inbox receipt caused a write")


def validate_applied_then_later_feature_advances():
    fake, digest = applied_cleanup_fixture(later_events=True)
    receipt = lookup_applied(fake, expected_event_digest=digest)
    require(receipt.state == APPLIED, receipt)
    require(
        receipt.result_revision == REV + 1,
        f"late restart returned latest Feature revision instead of exact Event result: {receipt}",
    )
    require(fake.put_count == 0, "late applied receipt caused a speculative write")


def validate_cleanup_content_conflict_fails_closed():
    fake, _ = applied_cleanup_fixture()
    _, conflicting_digest = canonical_event(summary="different-exact-content")
    try:
        lookup_applied(fake, expected_event_digest=conflicting_digest)
        raise AssertionError("different exact Event content reused applied id after cleanup")
    except FeatureEventGatewayError as exc:
        require(exc.code == "CONFLICT", exc)
    require(fake.put_count == 0, "cleanup content conflict attempted an Event write")


def validate_multiple_historical_contents_fail_closed():
    fake, digest = applied_cleanup_fixture()
    conflicting_text, _ = canonical_event(summary="historical-conflict")
    fake.history_texts.append(conflicting_text)
    try:
        lookup_applied(fake, expected_event_digest=digest)
        raise AssertionError("multiple historical exact Event bodies unexpectedly converged")
    except FeatureEventGatewayError as exc:
        require(exc.code == "CONFLICT", exc)


def validate_unprovable_cleanup_receipt_is_unknown():
    fake, digest = applied_cleanup_fixture()
    fake.history_unavailable = True
    receipt = lookup_applied(fake, expected_event_digest=digest)
    require(receipt.state == UNKNOWN, receipt)

    fake, _ = applied_cleanup_fixture()
    receipt = lookup_applied(fake, expected_event_digest=None)
    require(receipt.state == UNKNOWN, receipt)
    require(fake.put_count == 0, "unprovable cleanup receipt caused a speculative write")


def validate_absent_event_after_unrelated_advance_is_stale():
    fake = HistoryFakeGitHub()
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
            expected_event_digest=canonical_event()[1],
        )
        raise AssertionError("missing Event after unrelated revision advance unexpectedly remained ABSENT/retryable")
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", exc)
    require(fake.put_count == 0, "stale missing Event caused a write")


def validate_release_factory_uses_receipt_safe_transport():
    fake = HistoryFakeGitHub()
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
    validate_applied_then_later_feature_advances()
    validate_cleanup_content_conflict_fails_closed()
    validate_multiple_historical_contents_fail_closed()
    validate_unprovable_cleanup_receipt_is_unknown()
    validate_absent_event_after_unrelated_advance_is_stale()
    validate_release_factory_uses_receipt_safe_transport()
    print("Release-safe Feature Event receipt validation passed")
    print("- applied_events membership is never enough to prove exact content after cleanup")
    print("- immutable Git path history binds cleanup-safe APPLIED receipt to expected Event digest")
    print("- mismatched/multiple historical content => CONFLICT")
    print("- missing digest/unavailable history => UNKNOWN with zero speculative write")
    print("- late restart after later Events still returns expected_revision + 1")
    print("- unrelated Feature advance + missing Event => STALE_REVISION")
    print("- release factory fixes canonical receipt-safe transport")


if __name__ == "__main__":
    main()
