#!/usr/bin/env python3
"""Release-safe canonical Feature Event receipt recovery for Decision writes."""
from __future__ import annotations

import hashlib
from typing import Callable
from urllib.parse import quote

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_configured_feature_event_gateway import TrustedFeatureEventTarget
from operator_github_feature_event_gateway import (
    ABSENT,
    APPLIED,
    CONFLICT if False else ABSENT,
    UNKNOWN,
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

    `applied_events` proves that an Event id crossed trusted Persist, but the id
    alone does not prove exact content identity. Once the inbox file is absent,
    the immutable Git history for that exact path is therefore consulted before
    returning APPLIED. No additional receipt file or Manifest write authority is
    introduced by this recovery path.
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
        # A cleanup-safe APPLIED receipt is an exact-content claim. Without the
        # expected digest there is nothing to bind historical bytes to, so fail
        # closed rather than treating applied_events membership as sufficient.
        if not expected_event_digest:
            return None

        status, payload = self.http_request(
            "GET",
            self._history_url(repository, event_path, target_ref),
            self._headers(),
            None,
        )
        if status != 200 or not isinstance(payload, list):
            return None
        # Path history should normally contain create + optional cleanup. A full
        # page means history may be truncated; do not make an exact identity claim.
        if len(payload) >= 100:
            return None

        recovered: dict[str, str | None] = {}
        for row in payload:
            if not isinstance(row, dict) or not isinstance(row.get("sha"), str) or not row["sha"]:
                return None
            commit_sha = str(row["sha"])
            content_status, content_payload = self._get_content(
                repository,
                event_path,
                commit_sha,
            )
            if content_status == 404:
                # Deletion/cleanup commit: the path legitimately does not exist
                # at this commit, so continue to earlier path history.
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
                raise FeatureEventGatewayError(
                    "INTERNAL_FAILURE",
                    "applied Event receipt has invalid Feature revision",
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
                    event_blob_sha=None,
                    result_revision=None,
                    manifest_blob_sha=str(manifest_blob_sha) if manifest_blob_sha else None,
                )
            _, historical_blob_sha = historical
            # Feature Event application is an optimistic-concurrency transition
            # from exact `expected_revision` to exactly `expected_revision + 1`.
            # The current Manifest may be further ahead because later Events were
            # applied after this one; returning the latest revision would make a
            # valid late-restart receipt look stale to Vertical Persist recovery.
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
            raise FeatureEventGatewayError(
                "STALE_REVISION",
                "Feature advanced without applying the exact Event",
            )
        return receipt


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
    transport = ReceiptSafeCanonicalFeatureEventGateway(**kwargs)
    return ProductionConfiguredFeatureEventGateway(scope=scope, transport=transport)
