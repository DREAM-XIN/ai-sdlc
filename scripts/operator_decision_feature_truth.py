#!/usr/bin/env python3
"""Trusted FeatureTruthGateway adapter for current Decision response verification.

`decision.respond` remains a Store-only Decision fact. This adapter supplies the
fresh Feature/candidate truth required by the accepted Decision backend; it does
not persist a Feature Event and cannot be used to synthesize Gate authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from operator_github_feature_event_gateway import FeatureEventGatewayError
from operator_production_feature_event_gateway import ProductionConfiguredFeatureEventGateway
from operator_store_backends import OperatorStoreRuntime
from operator_store_model import StoreInvariantError, normalize_repository, rebuild_projection
from operator_vertical import FeatureSnapshot

_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class TrustedCandidateSnapshot:
    candidate_pr_number: int | None
    candidate_head_sha: str | None

    def __post_init__(self):
        if self.candidate_pr_number is not None and self.candidate_pr_number < 1:
            raise ValueError("trusted candidate PR number is invalid")
        if self.candidate_head_sha is not None and not _SHA40.fullmatch(self.candidate_head_sha):
            raise ValueError("trusted candidate head must be a full Git SHA")
        if self.candidate_pr_number is not None and self.candidate_head_sha is None:
            raise ValueError("candidate PR identity requires an exact current head")


class TrustedCandidateProvider(Protocol):
    def current_candidate(
        self,
        *,
        operation_id: str,
        repository: str,
        feature_id: str,
        target_ref: str,
    ) -> TrustedCandidateSnapshot:
        ...


class DurableDecisionFeatureTruthGateway:
    """Resolve Decision Feature truth from durable Operation + trusted exact ref.

    The caller supplies only `operation_id`, matching the accepted
    `FeatureTruthGateway` contract. Repository, Feature id, expected revision and
    target ref are derived from trusted state/configuration, never client input.
    """

    def __init__(
        self,
        *,
        runtime: OperatorStoreRuntime,
        feature_gateway: ProductionConfiguredFeatureEventGateway,
        candidate_provider: TrustedCandidateProvider,
    ):
        if not isinstance(runtime, OperatorStoreRuntime):
            raise ValueError("Decision Feature truth requires trusted OperatorStoreRuntime")
        if not isinstance(feature_gateway, ProductionConfiguredFeatureEventGateway):
            raise ValueError("Decision Feature truth requires production configured Feature gateway")
        if candidate_provider is None or not callable(getattr(candidate_provider, "current_candidate", None)):
            raise ValueError("Decision Feature truth requires trusted current-candidate provider")
        self.runtime = runtime
        self.feature_gateway = feature_gateway
        self.candidate_provider = candidate_provider

    def read_feature(self, *, operation_id: str) -> tuple[FeatureSnapshot, dict]:
        if not operation_id:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Decision Operation id is required")
        try:
            projection = rebuild_projection(self.runtime.backend.read_snapshot(), operation_id)
        except StoreInvariantError as exc:
            raise FeatureEventGatewayError("INVALID_REQUEST", "Decision Operation was not found") from exc

        repository = normalize_repository(str(projection.get("target_repository") or ""))
        configured_repository = normalize_repository(self.feature_gateway.configuration.repository)
        feature_id = str(projection.get("feature_id") or "")
        expected_revision = projection.get("expected_feature_revision")
        if repository != configured_repository or not feature_id:
            raise FeatureEventGatewayError("UNAUTHORIZED", "Decision Operation is outside trusted Feature gateway scope")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "Decision Operation lacks expected Feature revision")

        # target_ref is server-configured and Feature-scoped; unconfigured ids
        # fail closed before any GitHub read.
        target_ref = self.feature_gateway.configuration.target_ref(feature_id)
        manifest = self.feature_gateway.read_feature(feature_id=feature_id)
        current_revision = manifest.get("revision")
        if current_revision != expected_revision:
            raise FeatureEventGatewayError(
                "STALE_REVISION",
                f"Decision Feature revision {current_revision} != Operation-bound {expected_revision}",
            )

        candidate = self.candidate_provider.current_candidate(
            operation_id=operation_id,
            repository=repository,
            feature_id=feature_id,
            target_ref=target_ref,
        )
        if not isinstance(candidate, TrustedCandidateSnapshot):
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted candidate provider returned invalid snapshot")

        feature = FeatureSnapshot.from_manifest(
            repository=repository,
            target_ref=target_ref,
            manifest=manifest,
            candidate_pr_number=candidate.candidate_pr_number,
            candidate_head_sha=candidate.candidate_head_sha,
        )
        if feature.feature_id != feature_id or feature.revision != expected_revision:
            raise FeatureEventGatewayError("INTERNAL_FAILURE", "trusted Feature snapshot binding changed during reconstruction")
        return feature, dict(manifest)
