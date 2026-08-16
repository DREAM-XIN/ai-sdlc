#!/usr/bin/env python3
"""Release-safe canonical Feature Event receipt recovery for Decision/Vertical writes."""
from __future__ import annotations

import base64
import hashlib
from typing import Callable
from urllib.parse import quote

import yaml

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_configured_feature_event_gateway import TrustedFeatureEventTarget
from operator_github_feature_event_gateway import (
    ABSENT,
    APPLIED,
    PENDING,
    UNKNOWN,
    EVENT_ID,
    FEATURE_ID,
    FeatureEventGatewayError,
    FeatureEventReceipt,
    _default_request,
)
from operator_production_feature_event_gateway import (
    ProductionConfiguredFeatureEventGateway,
    TrustedFeatureEventWriteScope,
)


class ReceiptSafeCanonicalFeatureEventGateway(CanonicalExactRevisionGitHubFeatureEventGateway):
    """Recover cleanup-safe receipts only after proving exact historical bytes.

    This compatibility transport retains the historical ``events/inbox`` path.
    Production release composition uses the repository-bound subclass below so
    real Event writes enter the trusted ``state/events`` Persist workflow.
    """

    def _history_url(self, repository: str, event_path: str, target_ref: str) -> str:
        return (
            f"{self.api_base}/repos/{repository}/commits"
            f"?sha={quote(target_ref, safe='')}"
            f"&path={quote(event_path, safe='')}"
            "&per_page=100"
        )

    def _recover_historical_event_digest(
        self,
        *,
        repository: str,
        event_path: str,
        target_ref: str,
        expected_event_digest: str | None,
    ) -> tuple[str, str | None] | None:
        if not expected_event_digest:
            return None
        status, payload = self.http_request(
            "GET",
            self._history_url(repository, event_path, target_ref),
            self._headers(),
            None,
        )
        if status != 200 or not isinstance(payload, list) or len(payload) >= 100:
            return None
        recovered: dict[str, str | None] = {}
        for row in payload:
            if not isinstance(row, dict) or not isinstance(row.get("sha"), str) or not row["sha"]:
                return None
            content_status, content_payload = self._get_content(repository, event_path, str(row["sha"]))
            if content_status == 404:
                continue
            if content_status != 200:
                return None
            text, blob_sha = self._decode_content(content_payload)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            recovered.setdefault(digest, blob_sha)
            if len(recovered) > 1:
                raise FeatureEventGatewayError(
                    "CONFLICT",
                    "Feature Event path history contains multiple exact contents",
                )
        if not recovered:
            return None
        digest, blob_sha = next(iter(recovered.items()))
        if digest != expected_event_digest:
            raise FeatureEventGatewayError(
                "CONFLICT",
                "historically applied Feature Event content differs from expected exact Event",
            )
        return digest, blob_sha

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
        receipt = super().lookup_receipt(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            event_id=event_id,
            expected_revision=expected_revision,
            expected_event_digest=expected_event_digest,
        )
        if receipt.state != ABSENT:
            return receipt
        manifest = self.read_feature(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
        )
        revision = manifest.get("revision")
        applied = manifest.get("applied_events") or []
        manifest_blob_sha = manifest.get("_manifest_blob_sha")
        if event_id in applied:
            if not isinstance(revision, int) or revision < expected_revision + 1:
                raise FeatureEventGatewayError("INTERNAL_FAILURE", "applied Event receipt has invalid Feature revision")
            historical = self._recover_historical_event_digest(
                repository=repository,
                event_path=receipt.event_path,
                target_ref=target_ref,
                expected_event_digest=expected_event_digest,
            )
            if historical is None:
                return FeatureEventReceipt(
                    UNKNOWN,
                    event_id,
                    expected_revision,
                    receipt.event_path,
                    event_blob_sha=None,
                    result_revision=None,
                    manifest_blob_sha=str(manifest_blob_sha) if manifest_blob_sha else None,
                )
            _, historical_blob_sha = historical
            return FeatureEventReceipt(
                APPLIED,
                event_id,
                expected_revision,
                receipt.event_path,
                event_blob_sha=historical_blob_sha,
                result_revision=expected_revision + 1,
                manifest_blob_sha=str(manifest_blob_sha) if manifest_blob_sha else None,
            )
        if not isinstance(revision, int):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature revision is invalid")
        if revision != expected_revision:
            raise FeatureEventGatewayError("STALE_REVISION", "Feature advanced without applying the exact Event")
        return receipt


class RepositoryReceiptSafeCanonicalFeatureEventGateway(ReceiptSafeCanonicalFeatureEventGateway):
    """Production Event transport aligned with repository Feature Event/Persist."""

    @staticmethod
    def repository_event_path(feature_id: str, event_id: str) -> str:
        if not FEATURE_ID.fullmatch(str(feature_id or "")):
            raise FeatureEventGatewayError("INVALID_REQUEST", "invalid Feature id for repository Event path")
        if not EVENT_ID.fullmatch(str(event_id or "")):
            raise FeatureEventGatewayError("INVALID_REQUEST", "invalid Event id for repository Event path")
        return f"state/events/{feature_id}/{event_id}.yaml"

    def _current_repository_receipt(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        event_id: str,
        expected_revision: int,
        expected_event_digest: str | None,
    ) -> FeatureEventReceipt:
        event_path = self.repository_event_path(feature_id, event_id)
        event_status, event_payload = self._get_content(repository, event_path, target_ref)
        if event_status not in {200, 404}:
            return FeatureEventReceipt(UNKNOWN, event_id, expected_revision, event_path)
        if event_status == 404:
            return FeatureEventReceipt(ABSENT, event_id, expected_revision, event_path)
        event_text, event_blob_sha = self._decode_content(event_payload)
        if expected_event_digest is not None:
            digest = hashlib.sha256(event_text.encode("utf-8")).hexdigest()
            if digest != expected_event_digest:
                raise FeatureEventGatewayError("CONFLICT", "existing Feature Event id has different content")
        manifest_path = f"state/features/{feature_id}.yaml"
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
        if str((manifest.get("feature") or {}).get("id") or "") != feature_id:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature Manifest identity mismatch")
        revision = manifest.get("revision")
        applied = manifest.get("applied_events") or []
        if event_id in applied:
            if not isinstance(revision, int) or revision < expected_revision + 1:
                raise FeatureEventGatewayError("INTERNAL_FAILURE", "applied Event receipt has invalid Feature revision")
            return FeatureEventReceipt(
                APPLIED,
                event_id,
                expected_revision,
                event_path,
                event_blob_sha=event_blob_sha,
                result_revision=expected_revision + 1,
                manifest_blob_sha=manifest_blob_sha,
            )
        if not isinstance(revision, int):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature revision is invalid")
        if revision != expected_revision:
            raise FeatureEventGatewayError("STALE_REVISION", "Feature advanced while exact Event remained unapplied")
        return FeatureEventReceipt(
            PENDING,
            event_id,
            expected_revision,
            event_path,
            event_blob_sha=event_blob_sha,
            manifest_blob_sha=manifest_blob_sha,
        )

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
        receipt = self._current_repository_receipt(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            event_id=event_id,
            expected_revision=expected_revision,
            expected_event_digest=expected_event_digest,
        )
        if receipt.state != ABSENT:
            return receipt
        manifest = self.read_feature(repository=repository, feature_id=feature_id, target_ref=target_ref)
        revision = manifest.get("revision")
        applied = manifest.get("applied_events") or []
        manifest_blob_sha = manifest.get("_manifest_blob_sha")
        if event_id in applied:
            if not isinstance(revision, int) or revision < expected_revision + 1:
                raise FeatureEventGatewayError(
                    "INTERNAL_FAILURE",
                    "applied repository Event receipt has invalid Feature revision",
                )
            historical = self._recover_historical_event_digest(
                repository=repository,
                event_path=receipt.event_path,
                target_ref=target_ref,
                expected_event_digest=expected_event_digest,
            )
            if historical is None:
                return FeatureEventReceipt(
                    UNKNOWN,
                    event_id,
                    expected_revision,
                    receipt.event_path,
                    manifest_blob_sha=str(manifest_blob_sha) if manifest_blob_sha else None,
                )
            _, historical_blob_sha = historical
            return FeatureEventReceipt(
                APPLIED,
                event_id,
                expected_revision,
                receipt.event_path,
                event_blob_sha=historical_blob_sha,
                result_revision=expected_revision + 1,
                manifest_blob_sha=str(manifest_blob_sha) if manifest_blob_sha else None,
            )
        if not isinstance(revision, int):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature revision is invalid")
        if revision != expected_revision:
            raise FeatureEventGatewayError("STALE_REVISION", "Feature advanced without applying the exact repository Event")
        return receipt

    def persist_exact_event(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        expected_revision: int,
        event: dict,
    ) -> FeatureEventReceipt:
        """Converge exact replay before applying the fresh-revision write fence.

        Once the exact Event is APPLIED the Feature has necessarily advanced to
        ``expected_revision + 1``. Requiring the old revision before checking the
        immutable Event receipt would incorrectly reject crash/restart replay.
        Conflicting bytes, UNKNOWN, and an unrelated advance still fail closed in
        ``lookup_receipt`` before any write is possible.
        """
        event_id, event_text = self._validate_event(
            event,
            feature_id=feature_id,
            expected_revision=expected_revision,
        )
        digest = hashlib.sha256(event_text.encode("utf-8")).hexdigest()
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
        self._require_revision(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
        )
        return self.submit_event(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
            event=event,
        )

    def submit_event(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        expected_revision: int,
        event: dict,
    ) -> FeatureEventReceipt:
        self._require_revision(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
        )
        event_id, event_text = self._validate_event(
            event,
            feature_id=feature_id,
            expected_revision=expected_revision,
        )
        event_path = self.repository_event_path(feature_id, event_id)
        digest = hashlib.sha256(event_text.encode("utf-8")).hexdigest()
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
        status, _payload = self.http_request("PUT", url, self._headers(), body)
        if status not in {200, 201}:
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
        return self.lookup_receipt(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            event_id=event_id,
            expected_revision=expected_revision,
            expected_event_digest=digest,
        )


def build_release_decision_event_gateway(
    *,
    token: str,
    repository: str,
    default_branch: str,
    feature_refs: dict[str, str],
    api_base: str = "https://api.github.com",
    api_version: str = "2022-11-28",
    http_request: Callable = _default_request,
    sleeper=None,
    poll_attempts: int = 8,
    poll_seconds: float = 1.0,
) -> ProductionConfiguredFeatureEventGateway:
    if not isinstance(feature_refs, dict) or not feature_refs:
        raise ValueError("trusted Decision Event runtime requires Feature/ref bindings")
    targets = tuple(
        TrustedFeatureEventTarget(str(feature_id), str(target_ref))
        for feature_id, target_ref in sorted(feature_refs.items())
    )
    scope = TrustedFeatureEventWriteScope(
        repository=repository,
        default_branch=default_branch,
        targets=targets,
    )
    kwargs = {
        "token": token,
        "api_base": api_base,
        "api_version": api_version,
        "http_request": http_request,
        "poll_attempts": poll_attempts,
        "poll_seconds": poll_seconds,
    }
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    transport = RepositoryReceiptSafeCanonicalFeatureEventGateway(**kwargs)
    return ProductionConfiguredFeatureEventGateway(scope=scope, transport=transport)
