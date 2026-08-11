#!/usr/bin/env python3
"""Authoritative production construction path for trusted Decision Feature Event writes."""
from __future__ import annotations

from typing import Callable

from operator_github_feature_event_gateway import _default_request
from operator_production_feature_event_gateway import ProductionConfiguredFeatureEventGateway
from operator_release_feature_event_gateway import build_release_decision_event_gateway


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
    """Build the one production/release-safe exact Event transport.

    This compatibility name deliberately delegates to the release-safe factory;
    there is no weaker production construction path that can bypass cleanup-safe
    exact-content receipt recovery.
    """

    return build_release_decision_event_gateway(
        token=token,
        repository=repository,
        default_branch=default_branch,
        feature_refs=feature_refs,
        api_base=api_base,
        api_version=api_version,
        http_request=http_request,
        sleeper=sleeper,
        poll_attempts=poll_attempts,
        poll_seconds=poll_seconds,
    )
