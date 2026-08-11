#!/usr/bin/env python3
"""Validate the production Decision Event gateway can only be built through safe layers."""
from __future__ import annotations

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_decision_event_runtime import build_production_decision_event_gateway
from validate_operator_github_feature_event_gateway import FEATURE, REF, REPO, REV, FakeGitHub


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    fake = FakeGitHub()
    gateway = build_production_decision_event_gateway(
        token="trusted-event-writer",
        repository=REPO,
        default_branch="main",
        feature_refs={FEATURE: REF},
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    require(isinstance(gateway.transport, CanonicalExactRevisionGitHubFeatureEventGateway), type(gateway.transport))
    require(gateway.scope.default_branch == "main", gateway.scope)
    require(gateway.configuration.target_ref(FEATURE) == REF, gateway.configuration)
    feature = gateway.read_feature(feature_id=FEATURE)
    require(feature["revision"] == REV, feature)

    for feature_refs in ({}, {FEATURE: "main"}):
        try:
            build_production_decision_event_gateway(
                token="trusted-event-writer",
                repository=REPO,
                default_branch="main",
                feature_refs=feature_refs,
                api_base="https://api.github.test",
                http_request=fake,
                sleeper=lambda _: None,
                poll_attempts=1,
                poll_seconds=0,
            )
            raise AssertionError(f"unsafe Decision Event runtime config unexpectedly accepted: {feature_refs}")
        except ValueError:
            pass

    print("Production Decision Event runtime factory validation passed")
    print("- canonical exact Event transport: mandatory")
    print("- schema/revision/default-branch/server-scope layers: fixed by factory")
    print("- empty Feature scope and default-branch target: rejected")


if __name__ == "__main__":
    main()
