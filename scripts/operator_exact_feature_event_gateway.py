#!/usr/bin/env python3
"""Exact-revision trusted Feature Event gateway for Operator lifecycle writes."""
from __future__ import annotations

from typing import Any

from operator_github_feature_event_gateway import APPLIED, PENDING, FeatureEventGatewayError
from operator_validated_feature_event_gateway import ValidatedGitHubFeatureEventInboxGateway


class ExactRevisionGitHubFeatureEventGateway(ValidatedGitHubFeatureEventInboxGateway):
    """Add an exact Feature-revision fence before Event creation and while pending."""

    def _require_revision(
        self,
        *,
        repository: str,
        feature_id: str,
        target_ref: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        manifest = self.read_feature(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
        )
        revision = manifest.get("revision")
        if not isinstance(revision, int):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature revision is invalid")
        if revision != expected_revision:
            raise FeatureEventGatewayError(
                "STALE_REVISION",
                f"trusted Feature revision {revision} != expected {expected_revision}",
            )
        return manifest

    def lookup_receipt(self, *, repository: str, feature_id: str, target_ref: str, event_id: str, expected_revision: int, **kwargs):
        receipt = super().lookup_receipt(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            event_id=event_id,
            expected_revision=expected_revision,
            **kwargs,
        )
        if receipt.state != PENDING:
            return receipt
        manifest = self.read_feature(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
        )
        revision = manifest.get("revision")
        applied = manifest.get("applied_events") or []
        if event_id in applied:
            # Super should normally have returned APPLIED from the same ref; a
            # changed view between requests is safe only when it advanced by the
            # exact event.
            return super().lookup_receipt(
                repository=repository,
                feature_id=feature_id,
                target_ref=target_ref,
                event_id=event_id,
                expected_revision=expected_revision,
                **kwargs,
            )
        if not isinstance(revision, int):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature revision is invalid")
        if revision != expected_revision:
            raise FeatureEventGatewayError(
                "STALE_REVISION",
                "Feature advanced while exact Event remained unapplied",
            )
        return receipt

    def submit_event(self, *, repository: str, feature_id: str, target_ref: str, expected_revision: int, event: dict[str, Any]):
        self._require_revision(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
        )
        return super().submit_event(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
            event=event,
        )

    def persist_exact_event(self, *, repository: str, feature_id: str, target_ref: str, expected_revision: int, event: dict[str, Any]):
        self._require_revision(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
        )
        receipt = super().persist_exact_event(
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
            expected_revision=expected_revision,
            event=event,
        )
        if receipt.state == APPLIED:
            return receipt
        return receipt
