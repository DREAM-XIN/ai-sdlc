#!/usr/bin/env python3
"""Deterministic adversarial validation for trusted Feature Event inbox writes."""
from __future__ import annotations

import base64
import copy
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from operator_github_feature_event_gateway import (
    ABSENT,
    APPLIED,
    PENDING,
    UNKNOWN,
    FeatureEventGatewayError,
    GitHubFeatureEventInboxGateway,
)

REPO = "DREAM-XIN/ai-sdlc"
FEATURE = "F-DECISION-GATEWAY-FI"
REF = "feature/F-DECISION-GATEWAY-FI"
REV = 7
EVENT_ID = "EVT-F-DECISION-GATEWAY-FI-DECISION-RESPOND"
MANIFEST_PATH = f"state/features/{FEATURE}.yaml"
EVENT_PATH = f"events/inbox/{EVENT_ID}.yaml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def content_payload(text, sha):
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "sha": sha,
    }


class FakeGitHub:
    def __init__(self):
        self.manifest = {
            "version": "0.1.0",
            "feature": {"id": FEATURE, "title": "Decision gateway fixture"},
            "revision": REV,
            "workflow": {"profile": "standard-feature", "status": "ACTIVE", "current_stage": "acceptance"},
            "stages": {},
            "gates": {},
            "artifacts": [],
            "applied_events": [],
        }
        self.event_text = None
        self.event_sha = None
        self.put_count = 0
        self.put_paths = []
        self.fail_put_after_create = False
        self.fail_event_lookup = False
        self.apply_after_event_lookups = None
        self.event_lookup_count = 0

    def _decode_path(self, url):
        parsed = urlparse(url)
        prefix = f"/repos/{REPO}/contents/"
        if not parsed.path.startswith(prefix):
            return None, parsed
        path = "/".join(unquote(part) for part in parsed.path[len(prefix):].split("/"))
        return path, parsed

    def _maybe_apply(self):
        if self.event_text is None or self.apply_after_event_lookups is None:
            return
        if self.event_lookup_count >= self.apply_after_event_lookups and EVENT_ID not in self.manifest["applied_events"]:
            self.manifest["applied_events"].append(EVENT_ID)
            self.manifest["revision"] = REV + 1

    def __call__(self, method, url, headers, body=None):
        path, parsed = self._decode_path(url)
        if path is None:
            return 404, {}
        query_ref = parse_qs(parsed.query).get("ref", [REF])[0]
        if query_ref != REF:
            return 404, {}

        if method == "GET" and path == EVENT_PATH:
            self.event_lookup_count += 1
            if self.fail_event_lookup:
                return 503, {}
            self._maybe_apply()
            if self.event_text is None:
                return 404, {}
            return 200, content_payload(self.event_text, self.event_sha or "event-sha")

        if method == "GET" and path == MANIFEST_PATH:
            return 200, content_payload(yaml.safe_dump(self.manifest, sort_keys=False), "manifest-sha")

        if method == "PUT":
            self.put_count += 1
            self.put_paths.append(path)
            if path != EVENT_PATH:
                raise AssertionError(f"gateway attempted non-Event write: {path}")
            if self.event_text is not None:
                return 422, {"message": "already exists"}
            raw = base64.b64decode((body or {}).get("content", "")).decode("utf-8")
            self.event_text = raw
            self.event_sha = "event-created-sha"
            if self.fail_put_after_create:
                return 503, {}
            return 201, {"content": {"sha": self.event_sha}}

        return 404, {}


def event(summary="accepted"):
    return {
        "version": "0.1.0",
        "id": EVENT_ID,
        "feature_id": FEATURE,
        "expected_revision": REV,
        "occurred_at": "2026-08-11T05:30:00Z",
        "changes": [
            {
                "kind": "artifact",
                "id": "decision-response-evidence",
                "status": "approved",
                "summary": summary,
            }
        ],
    }


def gateway(fake, *, attempts=4):
    return GitHubFeatureEventInboxGateway(
        token="trusted-event-writer",
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=attempts,
        poll_seconds=0,
    )


def validate_read_and_apply():
    fake = FakeGitHub()
    fake.apply_after_event_lookups = 2
    gw = gateway(fake)
    feature = gw.read_feature(repository=REPO, feature_id=FEATURE, target_ref=REF)
    require(feature["revision"] == REV, feature)
    receipt = gw.persist_exact_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(receipt.state == APPLIED, receipt)
    require(receipt.result_revision == REV + 1, receipt)
    require(fake.put_count == 1, "exact Event was created more than once")
    require(fake.put_paths == [EVENT_PATH], "gateway wrote outside Event inbox")
    require(EVENT_ID in fake.manifest["applied_events"], "trusted Persist receipt was not observed")

    duplicate = gw.persist_exact_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(duplicate.state == APPLIED, duplicate)
    require(fake.put_count == 1, "exact duplicate Event response re-created the inbox file")


def validate_lost_ack_converges_by_lookup():
    fake = FakeGitHub()
    fake.fail_put_after_create = True
    gw = gateway(fake)
    receipt = gw.submit_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(receipt.state == PENDING, receipt)
    require(fake.put_count == 1, "lost ACK triggered a second Event create")
    second = gw.submit_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(second.state == PENDING, second)
    require(fake.put_count == 1, "post-crash exact replay did not converge on existing Event")


def validate_conflict_and_stale_rejection():
    fake = FakeGitHub()
    gw = gateway(fake)
    first = gw.submit_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(first.state == PENDING, first)
    try:
        gw.submit_event(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            expected_revision=REV,
            event=event(summary="conflicting-choice"),
        )
        raise AssertionError("conflicting exact Event id unexpectedly converged")
    except FeatureEventGatewayError as exc:
        require(exc.code == "CONFLICT", exc)
    require(fake.put_count == 1, "conflicting Event attempted another write")

    stale = event()
    stale["expected_revision"] = REV - 1
    try:
        gw.submit_event(
            repository=REPO,
            feature_id=FEATURE,
            target_ref=REF,
            expected_revision=REV,
            event=stale,
        )
        raise AssertionError("stale Feature Event unexpectedly accepted")
    except FeatureEventGatewayError as exc:
        require(exc.code == "STALE_REVISION", exc)


def validate_unknown_is_not_retried():
    fake = FakeGitHub()
    fake.fail_event_lookup = True
    gw = gateway(fake)
    receipt = gw.submit_event(
        repository=REPO,
        feature_id=FEATURE,
        target_ref=REF,
        expected_revision=REV,
        event=event(),
    )
    require(receipt.state == UNKNOWN, receipt)
    require(fake.put_count == 0, "UNKNOWN preflight speculatively created an Event")


def validate_no_manifest_write_path():
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "operator_github_feature_event_gateway.py"
    text = source.read_text(encoding="utf-8")
    # State manifest path may appear only in GET/lookup code. The create body is
    # constructed from `event_path`, and deterministic fakes fail any non-inbox PUT.
    require("events/inbox/{event_id}.yaml" in text, "Event inbox path missing")
    require("message\": f\"chore(ai-sdlc): submit Feature Event" in text, "create-only Event commit marker missing")


def main():
    validate_read_and_apply()
    validate_lost_ack_converges_by_lookup()
    validate_conflict_and_stale_rejection()
    validate_unknown_is_not_retried()
    validate_no_manifest_write_path()
    print("Trusted GitHub Feature Event inbox gateway validation passed")
    print("- exact Event create: at most once")
    print("- ACK loss / restart: exact lookup before retry")
    print("- same Event id with conflicting content: rejected")
    print("- stale revision: rejected")
    print("- unknown preflight: no speculative Event write")
    print("- authoritative Manifest: observed only; never PUT by gateway")


if __name__ == "__main__":
    main()
