#!/usr/bin/env python3
"""Validate that Decision Event targets come only from trusted server config."""
from __future__ import annotations

from operator_configured_feature_event_gateway import (
    ConfiguredExactFeatureEventGateway,
    TrustedFeatureEventConfiguration,
    TrustedFeatureEventTarget,
)
from operator_exact_feature_event_gateway import ExactRevisionGitHubFeatureEventGateway
from operator_github_feature_event_gateway import FeatureEventGatewayError, PENDING
from validate_operator_github_feature_event_gateway import FEATURE, REF, REPO, REV, FakeGitHub, event


class FixtureExactGateway(ExactRevisionGitHubFeatureEventGateway):
    @staticmethod
    def _schema_validate(event):
        return None


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    fake = FakeGitHub()
    transport = FixtureExactGateway(
        token="trusted-event-writer",
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    config = TrustedFeatureEventConfiguration(
        repository=REPO,
        targets=(TrustedFeatureEventTarget(FEATURE, REF),),
    )
    gateway = ConfiguredExactFeatureEventGateway(configuration=config, transport=transport)

    feature = gateway.read_feature(feature_id=FEATURE)
    require(feature["revision"] == REV, feature)
    receipt = gateway.persist_exact_event(feature_id=FEATURE, expected_revision=REV, event=event())
    require(receipt.state == PENDING, receipt)
    require(fake.put_count == 1, "configured gateway did not create exactly one Event")

    try:
        gateway.read_feature(feature_id="F-OUTSIDE-TRUSTED-SCOPE")
        raise AssertionError("unconfigured Feature unexpectedly resolved a target ref")
    except FeatureEventGatewayError as exc:
        require(exc.code == "UNAUTHORIZED", exc)

    # Public configured API intentionally has no repository/target_ref parameters.
    import inspect
    persist_params = set(inspect.signature(gateway.persist_exact_event).parameters)
    read_params = set(inspect.signature(gateway.read_feature).parameters)
    require("repository" not in persist_params and "target_ref" not in persist_params, persist_params)
    require("repository" not in read_params and "target_ref" not in read_params, read_params)

    print("Configured Feature Event gateway validation passed")
    print("- repository/ref authority: server-owned only")
    print("- Feature scope: explicit one-to-one trusted map")
    print("- unconfigured Feature: UNAUTHORIZED")
    print("- configured API exposes no caller-selectable repository/ref")


if __name__ == "__main__":
    main()
