#!/usr/bin/env python3
"""Production-scoped configured Feature Event gateway for Decision writes."""
from __future__ import annotations

from dataclasses import dataclass

from operator_configured_feature_event_gateway import (
    ConfiguredExactFeatureEventGateway,
    TrustedFeatureEventConfiguration,
    TrustedFeatureEventTarget,
)
from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway


@dataclass(frozen=True)
class TrustedFeatureEventWriteScope:
    repository: str
    default_branch: str
    targets: tuple[TrustedFeatureEventTarget, ...]

    def __post_init__(self):
        if not self.default_branch or self.default_branch.startswith("refs/") or ".." in self.default_branch:
            raise ValueError("invalid trusted default branch")
        for target in self.targets:
            if target.target_ref == self.default_branch:
                raise ValueError("Feature Event writes to the default branch are forbidden")

    def configuration(self) -> TrustedFeatureEventConfiguration:
        return TrustedFeatureEventConfiguration(
            repository=self.repository,
            targets=self.targets,
        )


class ProductionConfiguredFeatureEventGateway(ConfiguredExactFeatureEventGateway):
    def __init__(
        self,
        *,
        scope: TrustedFeatureEventWriteScope,
        transport: ExactRevisionGitHubFeatureEventGateway,
    ):
        self.scope = scope
        super().__init__(configuration=scope.configuration(), transport=transport)
