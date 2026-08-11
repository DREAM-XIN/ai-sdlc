#!/usr/bin/env python3
"""Validate that the shared runtime can expose exactly the v0.3 write slice when trusted dependencies exist."""
from __future__ import annotations

from operator_production_runtime import TrustedOperatorReadBundle
from operator_production_write_bundle import (
    REQUIRED_V03_WRITE_SLICE,
    extend_with_trusted_decision_writes,
)


class DummyPolicyVerifier:
    pass


class DummyFeatureGateway:
    pass


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    base = TrustedOperatorReadBundle(
        config=object(),
        trusted_context_provider=object(),
        backends={
            "operation.start": object(),
            "operation.cancel": object(),
            "operation.status": object(),
        },
        runtime=object(),
    )

    require("decision.respond" not in base.backends, "base read bundle unexpectedly contains Decision write authority")
    require("notification.ack" not in base.backends, "base read bundle unexpectedly contains Notification write authority")

    extended = extend_with_trusted_decision_writes(
        base,
        policy_verifier=DummyPolicyVerifier(),
        feature_gateway=DummyFeatureGateway(),
        trusted_context_digest="trusted-runtime-config-digest",
    )
    require(REQUIRED_V03_WRITE_SLICE.issubset(set(extended.backends)), "trusted write bundle does not contain the frozen v0.3 write slice")
    require(extended.backends["operation.start"] is base.backends["operation.start"], "operation.start backend was replaced instead of reused")
    require(extended.backends["operation.cancel"] is base.backends["operation.cancel"], "operation.cancel backend was replaced instead of reused")
    require("decision.respond" in extended.backends, "Decision write backend was not composed")
    require("notification.ack" in extended.backends, "Notification ack backend was not composed")

    for kwargs, expected in (
        ({"policy_verifier": None, "feature_gateway": DummyFeatureGateway(), "trusted_context_digest": "digest"}, "policy verifier"),
        ({"policy_verifier": DummyPolicyVerifier(), "feature_gateway": None, "trusted_context_digest": "digest"}, "Feature truth gateway"),
        ({"policy_verifier": DummyPolicyVerifier(), "feature_gateway": DummyFeatureGateway(), "trusted_context_digest": ""}, "context digest"),
    ):
        try:
            extend_with_trusted_decision_writes(base, **kwargs)
            raise AssertionError(f"missing trusted {expected} unexpectedly enabled write bundle")
        except ValueError:
            pass

    print("Operator production write-bundle validation passed")
    print("- base bundle: no Decision/Notification writes")
    print("- explicit extension: operation.start/cancel + decision.respond + notification.ack")
    print("- trusted policy/Feature/digest dependencies: mandatory, no fallback")


if __name__ == "__main__":
    main()
