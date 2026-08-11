#!/usr/bin/env python3
"""Validate production Feature Event write scope denies the default branch."""
from __future__ import annotations

from operator_configured_feature_event_gateway import TrustedFeatureEventTarget
from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway
from operator_production_feature_event_gateway import (
    ProductionConfiguredFeatureEventGateway,
    TrustedFeatureEventWriteScope,
)
from validate_operator_github_feature_event_gateway import FEATURE, REF, REPO, FakeGitHub


class FixtureExactGateway(ExactRevisionGitHubFeatureEventGateway):
    @staticmethod
    def _schema_validate(event):
        return None


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    try:
        TrustedFeatureEventWriteScope(
            repository=REPO,
            default_branch="main",
            targets=(TrustedFeatureEventTarget(FEATURE, "main"),),
        )
        raise AssertionError("default-branch Feature Event target unexpectedly accepted")
    except ValueError:
        pass

    scope = TrustedFeatureEventWriteScope(
        repository=REPO,
        default_branch="main",
        targets=(TrustedFeatureEventTarget(FEATURE, REF),),
    )
    fake = FakeGitHub()
    transport = FixtureExactGateway(
        token="trusted-event-writer",
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    gateway = ProductionConfiguredFeatureEventGateway(scope=scope, transport=transport)
    feature = gateway.read_feature(feature_id=FEATURE)
    require(feature["revision"] == 7, feature)

    print("Production Feature Event scope validation passed")
    print("- default branch target: forbidden")
    print("- non-default configured Feature ref: allowed")
    print("- repository/ref authority remains server-owned")


if __name__ == "__main__":
    main()
