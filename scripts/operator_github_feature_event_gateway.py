#!/usr/bin/env python3
"""Trusted GitHub Feature Event inbox gateway for Operator runtime writes.

The gateway never edits `state/features/*.yaml`. It creates exactly one
Feature Event inbox file, then observes the authoritative Feature Manifest until
the trusted persistence path records the exact event id in `applied_events`.
Ambiguous write acknowledgements are recovered by exact lookup before any
further action; blind duplicate event creation is forbidden.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
FEATURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

ABSENT = "ABSENT"
PENDING = "PENDING"
APPLIED = "APPLIED"
UNKNOWN = "UNKNOWN"


class FeatureEventGatewayError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FeatureEventReceipt:
    state: str
    event_id: str
    expected_revision: int
    event_path: str
    event_blob_sha: str | None = None
    result_revision: int | None = None
    manifest_blob_sha: str | None = None


def _default_request(method: str, url: str, headers: dict[str, str], body: dict | None = None):
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=20) as response:  # noqa: S310 - trusted GitHub API URL
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload: object = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        return int(exc.code), payload
    except (URLError, TimeoutError, OSError):
        return 0, {}


def _canonical_event_yaml(event: dict[str, Any]) -> str:
    return yaml.safe_dump(event, sort_keys=False, allow_unicode=True)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class GitHubFeatureEventInboxGateway:
    """Create-only Event inbox transport plus exact trusted Persist receipt lookup."""

    def __init__(
        self,
        *,
        token: str,
        api_base: str = "https://api.github.com",
        api_version: str = "2022-11-28",
        http_request: Callable[[str, str, dict[str, str], dict | None], tuple[int, object]] = _default_request,
        sleeper: Callable[[float], None] = time.sleep,
        poll_attempts: int = 8,
        poll_seconds: float = 1.0,
    ):
        if not token:
            raise ValueError("trusted Feature Event writer token is required")
        if not api_base.startswith("https://"):
            raise ValueError("GitHub API base must use HTTPS")
        if poll_attempts < 1 or poll_attempts > 60:
            raise ValueError("invalid Feature Event receipt poll attempts")
        if poll_seconds < 0 or poll_seconds > 30:
            raise ValueError("invalid Feature Event receipt poll interval")
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.api_version = api_version
        self.http_request = http_request
        self.sleeper = sleeper
        self.poll_attempts = poll_attempts
        self.poll_seconds = poll_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "ai-sdlc-operator-feature-event",
        }

    def _url(self, repository: str, path: str, target_ref: str) -> str:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        return f"{self.api_base}/repos/{repository}/contents/{encoded_path}?ref={quote(target_ref, safe='')}"

    def _get_content(self, repository: str, path: str, target_ref: str):
        return self.http_request("GET", self._url(repository, path, target_ref), self._headers(), None)

    @staticmethod
    def _decode_content(payload: object) -> tuple[str, str | None]:
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "GitHub content response lacks file content")
        try:
            text = base64.b64decode(payload["content"]).decode("utf-8")
        except Exception as exc:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "GitHub content response is invalid") from exc
        sha = payload.get("sha")
        return text, str(sha) if isinstance(sha, str) else None

    @staticmethod
    def _validate_event(event: dict[str, Any], *, feature_id: str, expected_revision: int) -> tuple[str, str]:
        if not isinstance(event, dict):
            raise FeatureEventGatewayError("INVALID_REQUEST", "Feature Event must be an object")
        event_id = str(event.get("id") or "")
        if not EVENT_ID.fullmatch(event_id):
            raise FeatureEventGatewayError("INVALID_REQUEST", "invalid Feature Event id")
        if not FEATURE_ID.fullmatch(feature_id):
            raise FeatureEventGatewayError("INVALID_REQUEST", "invalid Feature id")
        if str(event.get("feature_id") or "") != feature_id:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Feature Event feature_id mismatch")
        if event.get("expected_revision") != expected_revision:
            raise FeatureEventGatewayError("STALE_REVISION", "Feature Event expected_revision mismatch")
        if expected_revision < 0:
            raise FeatureEventGatewayError("INVALID_REQUEST", "expected revision must be non-negative")
        changes = event.get("changes")
        if not isinstance(changes, list) or not changes:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Feature Event requires bounded changes")
        text = _canonical_event_yaml(event)
        return event_id, text

    def read_feature(self, *, repository: str, feature_id: str, target_ref: str) -> dict[str, Any]:
        path = f"state/features/{feature_id}.yaml"
        status, payload = self._get_content(repository, path, target_ref)
        if status == 404:
            raise FeatureEventGatewayError("INVALID_REQUEST", "trusted Feature Manifest does not exist")
        if status != 200:
            raise FeatureEventGatewayError("TRANSIENT_FAILURE", f"trusted Feature read failed with HTTP {status}")
        text, blob_sha = self._decode_content(payload)
        try:
            manifest = yaml.safe_load(text)
        except Exception as exc:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature Manifest is invalid YAML") from exc
        if not isinstance(manifest, dict):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature Manifest must be a mapping")
        actual_id = str((manifest.get("feature") or {}).get("id") or "")
        if actual_id != feature_id:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature Manifest identity mismatch")
        result = dict(manifest)
        result["_manifest_blob_sha"] = blob_sha
        return result

    def lookup_receipt(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        event_id: str,
        expected_revision: int,
        expected_event_digest: str | None = None,
    ) -> FeatureEventReceipt:
        event_path = f"events/inbox/{event_id}.yaml"
        manifest_path = f"state/features/{feature_id}.yaml"

        event_status, event_payload = self._get_content(repository, event_path, target_ref)
        if event_status not in {200, 404}:
            return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path)
        if event_status == 404:
            return FeatureEventReceipt(ABSENT, event_id, expected_revision, event_path)

        event_text, event_blob_sha = self._decode_content(event_payload)
        if expected_event_digest is not None and _digest(event_text) != expected_event_digest:
            raise FeatureEventGatewayError("CONFLICT", "existing Feature Event id has different content")

        manifest_status, manifest_payload = self._get_content(repository, manifest_path, target_ref)
        if manifest_status != 200:
            return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path, event_blob_sha=event_blob_sha)
        manifest_text, manifest_blob_sha = self._decode_content(manifest_payload)
        try:
            manifest = yaml.safe_load(manifest_text)
        except Exception:
            return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path, event_blob_sha=event_blob_sha)
        if not isinstance(manifest, dict):
            return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path, event_blob_sha=event_blob_sha)
        applied = manifest.get("applied_events") or []
        if event_id in applied:
            result_revision = manifest.get("revision")
            if not isinstance(result_revision, int) or result_revision < expected_revision + 1:
                return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path, event_blob_sha=event_blob_sha)
            return FeatureEventReceipt(
                APPLIED,
                event_id,
                expected_revision,
                event_path,
                event_blob_sha=event_blob_sha,
                result_revision=result_revision,
                manifest_blob_sha=manifest_blob_sha,
            )
        return FeatureEventReceipt(
            PENDING,
            event_id,
            expected_revision,
            event_path,
            event_blob_sha=event_blob_sha,
            manifest_blob_sha=manifest_blob_sha,
        )

    def submit_event(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        expected_revision: int,
        event: dict[str, Any],
    ) -> FeatureEventReceipt:
        event_id, event_text = self._validate_event(event, feature_id=feature_id, expected_revision=expected_revision)
        event_path = f"events/inbox/{event_id}.yaml"
        digest = _digest(event_text)

        existing = self.lookup_receipt(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            event_id=event_id,
            expected_revision=expected_revision,
            expected_event_digest=digest,
        )
        if existing.state in {APPLIED, PENDING, UNKNOWN}:
            return existing

        url = self._url(repository, event_path, target_ref)
        body = {
            "message": f"chore(ai-sdlc): submit Feature Event {event_id}",
            "content": base64.b64encode(event_text.encode("utf-8")).decode("ascii"),
            "branch": target_ref,
        }
        status, payload = self.http_request("PUT", url, self._headers(), body)
        if status not in {200, 201}:
            # 409/422 may mean the create raced or an ACK was lost. Any failure
            # after the request was attempted must converge by exact lookup.
            observed = self.lookup_receipt(
                repository=repository,
                feature_id=feature_id,
                target_ref=target_ref,
                event_id=event_id,
                expected_revision=expected_revision,
                expected_event_digest=digest,
            )
            if observed.state != ABSENT:
                return observed
            return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path)

        observed = self.lookup_receipt(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            event_id=event_id,
            expected_revision=expected_revision,
            expected_event_digest=digest,
        )
        return observed

    def persist_exact_event(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        expected_revision: int,
        event: dict[str, Any],
    ) -> FeatureEventReceipt:
        event_id, event_text = self._validate_event(event, feature_id=feature_id, expected_revision=expected_revision)
        digest = _digest(event_text)
        receipt = self.submit_event(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
            event=event,
        )
        if receipt.state == APPLIED:
            return receipt
        if receipt.state == UNKNOWN:
            # UNKNOWN is intentionally not retried by creating another file.
            return receipt
        for attempt in range(self.poll_attempts):
            observed = self.lookup_receipt(
                repository=repository,
                feature_id=feature_id,
                target_ref=target_ref,
                event_id=event_id,
                expected_revision=expected_revision,
                expected_event_digest=digest,
            )
            if observed.state in {APPLIED, UNKNOWN, ABSENT}:
                return observed
            if attempt + 1 < self.poll_attempts:
                self.sleeper(self.poll_seconds)
        return receipt
