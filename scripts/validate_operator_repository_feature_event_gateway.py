#!/usr/bin/env python3
"""Validate production Feature Event path integration with trusted repository Persist."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from operator_github_feature_event_gateway import APPLIED, PENDING
from operator_release_feature_event_gateway import (
    RepositoryReceiptSafeCanonicalFeatureEventGateway,
    build_release_decision_event_gateway,
)
from resolve_feature_event_push import resolve_push

REPO = "DREAM-XIN/ai-sdlc"
FEATURE = "F-REPOSITORY-EVENT-FI"
REF = "feature/F-REPOSITORY-EVENT-FI"
REV = 3
EVENT_ID = "EVT-F-REPOSITORY-EVENT-FI-CODE-REVIEW-PASS"
EVENT_PATH = f"state/events/{FEATURE}/{EVENT_ID}.yaml"
MANIFEST_PATH = f"state/features/{FEATURE}.yaml"


def require(value, message):
    if not value:
        raise AssertionError(message)


def content_payload(text: str, sha: str) -> dict:
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "sha": sha,
    }


def event() -> dict:
    return {
        "version": "0.1.0",
        "id": EVENT_ID,
        "feature_id": FEATURE,
        "expected_revision": REV,
        "occurred_at": "2026-08-14T10:15:00Z",
        "changes": [
            {"kind": "stage", "id": "code-review", "status": "DONE"},
        ],
    }


def canonical_event() -> tuple[str, str]:
    _event_id, text = RepositoryReceiptSafeCanonicalFeatureEventGateway._validate_event(
        event(),
        feature_id=FEATURE,
        expected_revision=REV,
    )
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


class RepositoryFakeGitHub:
    def __init__(self):
        self.manifest = {
            "version": "0.1.0",
            "feature": {"id": FEATURE, "title": "repository Event fixture"},
            "revision": REV,
            "workflow": {"profile": "fixture", "status": "ACTIVE", "current_stage": "code-review"},
            "applied_events": [],
        }
        self.event_text: str | None = None
        self.event_sha = "event-sha"
        self.put_count = 0
        self.put_paths: list[str] = []
        self.event_lookup_count = 0
        self.apply_after_event_lookups: int | None = None
        self.history_text: str | None = None

    def _decode(self, url: str):
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
        parsed = urlparse(url)
        if method == "GET" and parsed.path == f"/repos/{REPO}/commits":
            query = parse_qs(parsed.query)
            if query.get("sha", [None])[0] != REF or unquote(query.get("path", [""])[0]) != EVENT_PATH:
                return 404, {}
            if self.history_text is None:
                return 200, []
            return 200, [{"sha": "history-event-sha"}]

        path, parsed = self._decode(url)
        if path is None:
            return 404, {}
        query_ref = parse_qs(parsed.query).get("ref", [REF])[0]

        if method == "GET" and path == EVENT_PATH and query_ref == "history-event-sha":
            if self.history_text is None:
                return 404, {}
            return 200, content_payload(self.history_text, "history-event-blob")
        if query_ref != REF:
            return 404, {}

        if method == "GET" and path == EVENT_PATH:
            self.event_lookup_count += 1
            self._maybe_apply()
            if self.event_text is None:
                return 404, {}
            return 200, content_payload(self.event_text, self.event_sha)
        if method == "GET" and path == MANIFEST_PATH:
            return 200, content_payload(yaml.safe_dump(self.manifest, sort_keys=False), "manifest-sha")
        if method == "PUT":
            self.put_count += 1
            self.put_paths.append(path)
            if path != EVENT_PATH:
                raise AssertionError(f"production gateway wrote outside canonical Feature Event path: {path}")
            if self.event_text is not None:
                return 422, {"message": "already exists"}
            self.event_text = base64.b64decode((body or {}).get("content", "")).decode("utf-8")
            return 201, {"content": {"sha": self.event_sha}}
        return 404, {}


def factory(fake: RepositoryFakeGitHub):
    configured = build_release_decision_event_gateway(
        token="trusted-event-writer",
        repository=REPO,
        default_branch="main",
        feature_refs={FEATURE: REF},
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=4,
        poll_seconds=0,
    )
    require(
        isinstance(configured.transport, RepositoryReceiptSafeCanonicalFeatureEventGateway),
        "release factory did not select repository-bound production Event transport",
    )
    return configured


def validate_live_path_and_apply_receipt():
    fake = RepositoryFakeGitHub()
    fake.apply_after_event_lookups = 2
    configured = factory(fake)
    receipt = configured.persist_exact_event(
        feature_id=FEATURE,
        expected_revision=REV,
        event=event(),
    )
    require(receipt.state == APPLIED, receipt)
    require(receipt.event_path == EVENT_PATH, receipt)
    require(receipt.result_revision == REV + 1, receipt)
    require(fake.put_count == 1, "canonical Event write was not at-most-once")
    require(fake.put_paths == [EVENT_PATH], "release gateway wrote outside state/events Feature namespace")

    again = configured.persist_exact_event(
        feature_id=FEATURE,
        expected_revision=REV,
        event=event(),
    )
    require(again.state == APPLIED and again.result_revision == REV + 1, again)
    require(fake.put_count == 1, "exact replay created a second repository Event")


def validate_present_archive_late_restart_is_exact_revision():
    fake = RepositoryFakeGitHub()
    text, digest = canonical_event()
    fake.event_text = text
    fake.manifest["applied_events"] = [EVENT_ID, "EVT-LATER-1", "EVT-LATER-2"]
    fake.manifest["revision"] = REV + 3
    configured = factory(fake)
    receipt = configured.lookup_receipt(
        feature_id=FEATURE,
        event_id=EVENT_ID,
        expected_revision=REV,
        expected_event_digest=digest,
    )
    require(receipt.state == APPLIED, receipt)
    require(receipt.result_revision == REV + 1, "archive receipt leaked latest Manifest revision")
    require(fake.put_count == 0, "late restart attempted a repository Event write")


def validate_cleanup_history_uses_same_repository_path():
    fake = RepositoryFakeGitHub()
    text, digest = canonical_event()
    fake.history_text = text
    fake.event_text = None
    fake.manifest["applied_events"] = [EVENT_ID]
    fake.manifest["revision"] = REV + 1
    configured = factory(fake)
    receipt = configured.lookup_receipt(
        feature_id=FEATURE,
        event_id=EVENT_ID,
        expected_revision=REV,
        expected_event_digest=digest,
    )
    require(receipt.state == APPLIED, receipt)
    require(receipt.event_path == EVENT_PATH, receipt)
    require(receipt.event_blob_sha == "history-event-blob", receipt)
    require(receipt.result_revision == REV + 1, receipt)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def validate_real_git_persist_resolver_contract():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        git(root, "init")
        git(root, "config", "user.name", "repository-event-validator")
        git(root, "config", "user.email", "repository-event@example.invalid")
        manifest_file = root / MANIFEST_PATH
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1.0",
                    "feature": {"id": FEATURE, "title": "repository Event fixture"},
                    "revision": REV,
                    "applied_events": [],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        git(root, "add", MANIFEST_PATH)
        git(root, "commit", "-m", "fixture manifest")
        before = git(root, "rev-parse", "HEAD")

        event_file = root / EVENT_PATH
        event_file.parent.mkdir(parents=True, exist_ok=True)
        event_file.write_text(yaml.safe_dump(event(), sort_keys=False), encoding="utf-8")
        git(root, "add", EVENT_PATH)
        git(root, "commit", "-m", "production repository Feature Event")
        after = git(root, "rev-parse", "HEAD")

        mode, event_path, manifest_path, changed = resolve_push(root, before, after)
        require(mode == "persist", f"canonical production Event did not enter trusted Persist resolver: {mode}")
        require(event_path == EVENT_PATH, event_path)
        require(manifest_path == MANIFEST_PATH, manifest_path)
        require(changed == [EVENT_PATH], changed)


def validate_workflow_trigger_matches_production_path():
    workflow = Path(".github/workflows/ai-sdlc-persist.yml").read_text(encoding="utf-8")
    require("state/events/**/*.yaml" in workflow, "trusted Persist workflow does not cover canonical production Event path")
    require("events/inbox" not in workflow, "unexpected legacy Event inbox trigger appeared")
    source = Path("scripts/operator_release_feature_event_gateway.py").read_text(encoding="utf-8")
    require("state/events/{feature_id}/{event_id}.yaml" in source, "release production transport is not Feature-namespaced")


def main():
    validate_live_path_and_apply_receipt()
    validate_present_archive_late_restart_is_exact_revision()
    validate_cleanup_history_uses_same_repository_path()
    validate_real_git_persist_resolver_contract()
    validate_workflow_trigger_matches_production_path()
    print("Repository Feature Event production integration validation passed")
    print("- release factory writes state/events/<feature>/<event>.yaml only")
    print("- exact create/replay is at-most-once and receipt remains expected_revision + 1")
    print("- cleanup history uses the same canonical repository path")
    print("- real Git push is accepted by production resolve_feature_event_push as persist")
    print("- trusted-main Persist workflow trigger covers the production Event path")


if __name__ == "__main__":
    main()
