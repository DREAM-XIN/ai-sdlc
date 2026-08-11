#!/usr/bin/env python3
"""Server-configured exact Feature Event gateway for Decision runtime use."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway
from operator_github_feature_event_gateway import FeatureEventGatewayError, FeatureEventReceipt

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FEATURE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class TrustedFeatureEventTarget:
    feature_id: str
    target_ref: str

    def __post_init__(self):
        if not FEATURE.fullmatch(self.feature_id):
            raise ValueError("invalid trusted Feature id")
        if not self.target_ref or self.target_ref.startswith("refs/") or ".." in self.target_ref:
            raise ValueError("invalid trusted Feature target ref")


@dataclass(frozen=True)
class TrustedFeatureEventConfiguration:
    repository: str
    targets: tuple[TrustedFeatureEventTarget, ...]

    def __post_init__(self):
        if not REPOSITORY.fullmatch(self.repository):
            raise ValueError("trusted target repository must be owner/repo")
        ids = [row.feature_id for row in self.targets]
        refs = [row.target_ref for row in self.targets]
        if not ids or len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("trusted Feature Event targets must be non-empty and one-to-one")

    def target_ref(self, feature_id: str) -> str:
        matches = [row.target_ref for row in self.targets if row.feature_id == feature_id]
        if len(matches) != 1:
            raise FeatureEventGatewayError("UNAUTHORIZED", "Feature is outside trusted Event gateway configuration")
        return matches[0]


class ConfiguredExactFeatureEventGateway:
    """Expose no caller-selectable repository/ref authority."""

    def __init__(
        self,
        *,
        configuration: TrustedFeatureEventConfiguration,
        transport: ExactRevisionGitHubFeatureEventGateway,
    ):
        self.configuration = configuration
        self.transport = transport

    def read_feature(self, *, feature_id: str) -> dict[str, Any]:
        return self.transport.read_feature(
            repository=self.configuration.repository,
            feature_id=feature_id,
            target_ref=self.configuration.target_ref(feature_id),
        )

    def lookup_receipt(
        self,
        *,
        feature_id: str,
        event_id: str,
        expected_revision: int,
        expected_event_digest: str | None = None,
    ) -> FeatureEventReceipt:
        return self.transport.lookup_receipt(
            repository=self.configuration.repository,
            feature_id=feature_id,
            target_ref=self.configuration.target_ref(feature_id),
            event_id=event_id,
            expected_revision=expected_revision,
            expected_event_digest=expected_event_digest,
        )

    def persist_exact_event(
        self,
        *,
        feature_id: str,
        expected_revision: int,
        event: dict[str, Any],
    ) -> FeatureEventReceipt:
        return self.transport.persist_exact_event(
            repository=self.configuration.repository,
            feature_id=feature_id,
            target_ref=self.configuration.target_ref(feature_id),
            expected_revision=expected_revision,
            event=event,
        )
