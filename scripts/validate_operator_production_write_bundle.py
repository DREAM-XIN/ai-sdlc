#!/usr/bin/env python3
"""Validate that the shared runtime can expose exactly the v0.3 write slice when trusted dependencies exist."""
from __future__ import annotations

from operator_decision_policy import DECISION_POLICY_SCHEMA, ProtectedDecisionPolicyVerifier
from operator_production_runtime import TrustedOperatorReadBundle
from operator_production_write_bundle import (
    REQUIRED_V03_WRITE_SLICE,
    extend_with_trusted_decision_writes,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import digest_json
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE

REPOSITORY = "dream-xin/control-fixture"
TARGET = "dream-xin/fixture"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


class DummyFeatureGateway:
    def read_feature(self, *, operation_id):
        raise AssertionError("write-bundle composition test must not invoke Feature truth")


def policy_loader(repository, state_ref, operation_profile):
    material = {
        "schema_version": DECISION_POLICY_SCHEMA,
        "repository": repository,
        "state_ref": state_ref,
        "operation_profile": operation_profile,
        "policy_ref": "protected://fixture/write-bundle",
        "policy_epoch": "fixture-v1",
        "allowed_target_repositories": [TARGET],
        "decision_types": {
            "NEEDS_AUTHORIZATION": {
                "choices": {"approve": "resume-exact-operation"},
                "allowed_responders": ["fixture-principal"],
                "ttl_seconds": 600,
                "warning_seconds": 60,
            }
        },
    }
    return {**material, "policy_digest": digest_json(material)}


def real_runtime():
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
    )


def real_policy_verifier():
    return ProtectedDecisionPolicyVerifier(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=policy_loader,
    )


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
        runtime=real_runtime(),
    )

    require("decision.respond" not in base.backends, "base read bundle unexpectedly contains Decision write authority")
    require("notification.ack" not in base.backends, "base read bundle unexpectedly contains Notification write authority")

    policy_verifier = real_policy_verifier()
    feature_gateway = DummyFeatureGateway()
    extended = extend_with_trusted_decision_writes(
        base,
        policy_verifier=policy_verifier,
        feature_gateway=feature_gateway,
        trusted_context_digest="trusted-runtime-config-digest",
    )
    require(REQUIRED_V03_WRITE_SLICE.issubset(set(extended.backends)), "trusted write bundle does not contain the frozen v0.3 write slice")
    require(extended.backends["operation.start"] is base.backends["operation.start"], "operation.start backend was replaced instead of reused")
    require(extended.backends["operation.cancel"] is base.backends["operation.cancel"], "operation.cancel backend was replaced instead of reused")
    require("decision.respond" in extended.backends, "Decision write backend was not composed")
    require("notification.ack" in extended.backends, "Notification ack backend was not composed")
    require(isinstance(extended.runtime, OperatorStoreRuntime), "write extension lost real OperatorStoreRuntime")

    for kwargs, expected in (
        ({"policy_verifier": None, "feature_gateway": feature_gateway, "trusted_context_digest": "digest"}, "policy verifier"),
        ({"policy_verifier": policy_verifier, "feature_gateway": None, "trusted_context_digest": "digest"}, "Feature truth gateway"),
        ({"policy_verifier": policy_verifier, "feature_gateway": feature_gateway, "trusted_context_digest": ""}, "context digest"),
    ):
        try:
            extend_with_trusted_decision_writes(base, **kwargs)
            raise AssertionError(f"missing trusted {expected} unexpectedly enabled write bundle")
        except ValueError:
            pass

    print("Operator production write-bundle validation passed")
    print("- base bundle: no Decision/Notification writes")
    print("- explicit extension: operation.start/cancel + decision.respond + notification.ack")
    print("- real OperatorStoreRuntime + ProtectedDecisionPolicyVerifier types are required by composition")
    print("- trusted policy/Feature/digest dependencies: mandatory, no fallback")


if __name__ == "__main__":
    main()
