#!/usr/bin/env python3
"""Single production construction path for trusted Decision Feature Event writes."""
from __future__ import annotations

from typing import Callable

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_configured_feature_event_gateway import TrustedFeatureEventTarget
from operator_github_feature_event_gateway import _default_request
from operator_production_feature_event_gateway import (
    ProductionConfiguredFeatureEventGateway,
    TrustedFeatureEventWriteScope,
)


def build_production_decision_event_gateway(
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
    transport = CanonicalExactRevisionGitHubFeatureEventGateway(**kwargs)
    return ProductionConfiguredFeatureEventGateway(scope=scope, transport=transport)
