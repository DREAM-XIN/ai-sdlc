#!/usr/bin/env python3
"""Validate release-safe exact Event receipt recovery after Event cleanup."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, unquote, urlparse

from operator_github_feature_event_gateway import APPLIED, UNKNOWN, FeatureEventGatewayError
from operator_release_feature_event_gateway import (
    ReceiptSafeCanonicalFeatureEventGateway,
    RepositoryReceiptSafeCanonicalFeatureEventGateway,
    build_release_decision_event_gateway,
)
from validate_operator_github_feature_event_gateway import (
    EVENT_ID,
    FEATURE,
    REF,
    REPO,
    REV,
    FakeGitHub,
    content_payload,
)

LEGACY_EVENT_PATH = f"events/inbox/{EVENT_ID}.yaml"
REPOSITORY_EVENT_PATH = f"state/events/{FEATURE}/{EVENT_ID}.yaml"


class FixtureReleaseGateway(ReceiptSafeCanonicalFeatureEventGateway):
    pass


class HistoryFakeGitHub(FakeGitHub):
    """Expose exact history for both compatibility and production Event paths.

    Existing release/Vertical validators share this fake. The low-level
    compatibility transport still uses ``events/inbox`` while the release
    factory now uses the canonical repository Event archive path.
    """

    def __init__(self):
        super().__init__()
        self.history_texts: list[str] = []
        self.history_unavailable = False
        self.history_lookup_count = 0

    def _history_content(self, query_ref: str):
        if query_ref == "event-cleanup-sha":
            return 404, {}
        if query_ref.startswith("event-history-"):
            try:
                index = int(query_ref.rsplit("-", 1)[1])
                text = self.history_texts[index]
            except (ValueError, IndexError):
                return 404, {}
            return 200, content_payload(text, f"history-blob-{index}")
        return None

    def _canonical_current(self, method, body=None):
        if method == "GET":
            self.event_lookup_count += 1
            if self.fail_event_lookup:
                return 503, {}
            self._maybe_apply()
            if self.event_text is None:
                return 404, {}
            return 200, content_payload(self.event_text, self.event_sha or "event-sha")
        if method == "PUT":
            self.put_count += 1
            self.put_paths.append(REPOSITORY_EVENT_PATH)
            if self.event_text is not None:
                return 422, {"message": "already exists"}
            self.event_text = base64.b64decode((body or {}).get("content", "")).decode("utf-8")
            self.event_sha = "event-created-sha"
            if self.fail_put_after_create:
                return 503, {}
            return 201, {"content": {"sha": self.event_sha}}
        return None

    def __call__(self, method, url, headers, body=None):
        parsed = urlparse(url)
        if method == "GET" and parsed.path == f"/repos/{REPO}/commits":
            self.history_lookup_count += 1
            if self.history_unavailable:
                return 503, {}
            query = parse_qs(parsed.query)
            if query.get("sha", [None])[0] != REF:
                return 404, {}
            requested_path = unquote(query.get("path", [""])[0])
            if requested_path not in {LEGACY_EVENT_PATH, REPOSITORY_EVENT_PATH}:
                return 404, {}
            rows = [{"sha": "event-cleanup-sha"}]
            rows.extend({"sha": f"event-history-{index}"} for index, _ in enumerate(self.history_texts))
            return 200, rows

        prefix = f"/repos/{REPO}/contents/"
        if parsed.path.startswith(prefix):
            path = "/".join(unquote(part) for part in parsed.path[len(prefix):].split("/"))
            query_ref = parse_qs(parsed.query).get("ref", [REF])[0]
            if path in {LEGACY_EVENT_PATH, REPOSITORY_EVENT_PATH}:
                historical = self._history_content(query_ref)
                if historical is not None:
                    return historical
                if path == REPOSITORY_EVENT_PATH and query_ref == REF:
                    current = self._canonical_current(method, body)
                    if current is not None:
                        return current

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


def canonical_event(variant="accepted"):
    stage_status = "DONE" if variant == "accepted" else "REVIEW"
    event_doc = {
        "version": "0.1.0",
        "id": EVENT_ID,
        "feature_id": FEATURE,
        "expected_revision": REV,
        "occurred_at": "2026-08-11T05:30:00Z",
        "changes": [{"kind": "stage", "id": "acceptance", "status": stage_status}],
    }
    _, text = FixtureReleaseGateway._validate_event(
        event_doc,
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
    require(fake.put_count == 0, "applied missing Event receipt caused a write")


def validate_applied_then_later_feature_advances():
    fake, digest = applied_cleanup_fixture(later_events=True)
    receipt = lookup_applied(fake, expected_event_digest=digest)
    require(receipt.state == APPLIED, receipt)
    require(receipt.result_revision == REV + 1, receipt)
    require(fake.put_count == 0, "late applied receipt caused a speculative write")


def validate_cleanup_content_conflict_fails_closed():
    fake, _ = applied_cleanup_fixture()
    _, conflicting_digest = canonical_event(variant="different-exact-content")
    try:
        lookup_applied(fake, expected_event_digest=conflicting_digest)
        raise AssertionError("different exact Event content reused applied id after cleanup")
    except FeatureEventGatewayError as exc:
        require(exc.code == "CONFLICT", exc)
    require(fake.put_count == 0, "cleanup content conflict attempted an Event write")


def validate_multiple_historical_contents_fail_closed():
    fake, digest = applied_cleanup_fixture()
    conflicting_text, _ = canonical_event(variant="historical-conflict")
    fake.history_texts.append(conflicting_text)
    try:
        lookup_applied(fake, expected_event_digest=digest)
        raise AssertionError("multiple historical exact Event bodies unexpectedly converged")
    except FeatureEventGatewayError as exc:
        require(exc.code == "CONFLICT", exc)


def validate_unprovable_cleanup_receipt_is_unknown():
    fake, digest = applied_cleanup_fixture()
    fake.history_unavailable = True
    require(lookup_applied(fake, expected_event_digest=digest).state == UNKNOWN, "unavailable history was trusted")
    fake, _ = applied_cleanup_fixture()
    require(lookup_applied(fake, expected_event_digest=None).state == UNKNOWN, "missing digest was trusted")
    require(fake.put_count == 0, "unprovable cleanup receipt caused a speculative write")


def validate_absent_event_after_unrelated_advance_is_stale():
    fake = HistoryFakeGitHub()
    fake.event_text = None
    fake.manifest["applied_events"] = []
    fake.manifest["revision"] = REV + 1
    try:
        transport(fake).lookup_receipt(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            event_id=EVENT_ID,
            expected_revision=REV,
            expected_event_digest=canonical_event()[1],
        )
        raise AssertionError("missing Event after unrelated revision advance unexpectedly remained retryable")
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", exc)
    require(fake.put_count == 0, "stale missing Event caused a write")


def validate_release_factory_uses_repository_receipt_safe_transport():
    gateway = build_release_decision_event_gateway(
        token="trusted-event-writer",
        repository=REPO,
        default_branch="main",
        feature_refs={FEATURE: REF},
        api_base="https://api.github.test",
        http_request=HistoryFakeGitHub(),
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    require(isinstance(gateway.transport, RepositoryReceiptSafeCanonicalFeatureEventGateway), type(gateway.transport))
    require(isinstance(gateway.transport, ReceiptSafeCanonicalFeatureEventGateway), type(gateway.transport))


def main():
    validate_applied_without_inbox_file()
    validate_applied_then_later_feature_advances()
    validate_cleanup_content_conflict_fails_closed()
    validate_multiple_historical_contents_fail_closed()
    validate_unprovable_cleanup_receipt_is_unknown()
    validate_absent_event_after_unrelated_advance_is_stale()
    validate_release_factory_uses_repository_receipt_safe_transport()
    print("Release-safe Feature Event receipt validation passed")
    print("- compatibility history remains exact-content bound after cleanup")
    print("- shared fake covers legacy inbox and production state/events paths")
    print("- mismatched/multiple historical content => CONFLICT")
    print("- missing digest/unavailable history => UNKNOWN with zero speculative write")
    print("- late restart after later Events still returns expected_revision + 1")
    print("- unrelated Feature advance + missing Event => STALE_REVISION")
    print("- release factory fixes repository receipt-safe production transport")


if __name__ == "__main__":
    main()
