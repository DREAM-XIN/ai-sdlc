#!/usr/bin/env python3
"""Canonical exact-revision Feature Event transport for production Decision writes."""
from __future__ import annotations

from typing import Any

import yaml

from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway
from operator_feature_event_validation import (
    TrustedFeatureEventValidationError,
    validate_trusted_feature_event,
)
from operator_github_feature_event_gateway import EVENT_ID, FEATURE_ID, FeatureEventGatewayError


class CanonicalExactRevisionGitHubFeatureEventGateway(ExactRevisionGitHubFeatureEventGateway):
    """Make exact Event byte identity independent of input mapping insertion order."""

    @staticmethod
    def _schema_validate(event: dict[str, Any]) -> None:
        try:
            validate_trusted_feature_event(event)
        except TrustedFeatureEventValidationError as exc:
            raise FeatureEventGatewayError("INVALID_REQUEST", str(exc)) from exc

    @staticmethod
    def _validate_event(event: dict[str, Any], *, feature_id: str, expected_revision: int) -> tuple[str, str]:
        CanonicalExactRevisionGitHubFeatureEventGateway._schema_validate(event)
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
        # Stable Unicode YAML: semantic mapping key order cannot change exact
        # event bytes/digest across retries or process restarts.
        text = yaml.safe_dump(
            event,
            sort_keys=True,
            allow_unicode=True,
            default_flow_style=False,
        )
        return event_id, text
