#!/usr/bin/env python3
"""Schema-validating trusted wrapper around the GitHub Event inbox transport."""
from __future__ import annotations

from typing import Any

from operator_feature_event_validation import (
    TrustedFeatureEventValidationError,
    validate_trusted_feature_event,
)
from operator_github_feature_event_gateway import (
    FeatureEventGatewayError,
    GitHubFeatureEventInboxGateway,
)


class ValidatedGitHubFeatureEventInboxGateway(GitHubFeatureEventInboxGateway):
    """Reject non-contract Feature Events before any GitHub write/lookup flow."""

    @staticmethod
    def _schema_validate(event: dict[str, Any]) -> None:
        try:
            validate_trusted_feature_event(event)
        except TrustedFeatureEventValidationError as exc:
            raise FeatureEventGatewayError("INVALID_REQUEST", str(exc)) from exc

    def submit_event(self, *, event: dict[str, Any], **kwargs):
        self._schema_validate(event)
        return super().submit_event(event=event, **kwargs)

    def persist_exact_event(self, *, event: dict[str, Any], **kwargs):
        self._schema_validate(event)
        return super().persist_exact_event(event=event, **kwargs)
