#!/usr/bin/env python3
"""Validate the transport-neutral v0.3 write-ready runtime composition."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from operator_api import API_VERSION, dispatch
from operator_decision_policy import DECISION_POLICY_SCHEMA, ProtectedDecisionPolicyVerifier
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_production_write_bundle import REQUIRED_V03_WRITE_SLICE
from operator_store_model import digest_json, rebuild_projection
from operator_store_protection import PROTECTED, ProtectionReceipt
from operator_v03_write_runtime import build_v03_write_ready_operator_bundle
from operator_vertical import VERTICAL_PROFILE
from validate_operator_production_runtime import FakeGitHubContents

TARGET = "DREAM-XIN/fixture"
STORE = "DREAM-XIN/control-fixture"
FEATURE = "F-RUNTIME-COMPOSITION-0001"
FEATURE_REF = "feature/F-RUNTIME-COMPOSITION-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
ADAPTER = "ai-sdlc.future-write-fixture"
NOW = "2026-08-11T05:15:00Z"

KNOWN_SEMANTIC_WRITES = {
    "operation.start",
    "operation.resume",
    "operation.cancel",
    "decision.respond",
    "notification.ack",
}


class FixtureVerifier:
    test_only = False

    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository.lower(),
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity="v03-write-fixture",
            verified_at=NOW,
            policy_digest="v03-write-policy",
        )


class FixtureFeatureGateway:
    """Bounded placeholder for composition; Decision invocation is tested elsewhere."""

    def read_feature(self, *, operation_id):
        raise AssertionError("v0.3 write-runtime surface test must not invoke Decision Feature gateway")


def fixture_policy_loader(repository, state_ref, operation_profile):
    material = {
        "schema_version": DECISION_POLICY_SCHEMA,
        "repository": repository,
        "state_ref": state_ref,
        "operation_profile": operation_profile,
        "policy_ref": "protected://fixture/v03-write-policy",
        "policy_epoch": "fixture-v1",
        "allowed_target_repositories": [TARGET],
        "decision_types": {
            "release-authorization": {
                "choices": {
                    "APPROVE": "release.authorize",
                    "REJECT": "release.reject",
                },
                "allowed_responders": ["future-write-principal"],
                "ttl_seconds": 3600,
                "warning_seconds": 300,
            }
        },
    }
    return {**material, "policy_digest": digest_json(material)}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def main():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-v03-write-runtime-") as td:
        root = Path(td)
        remote = root / "control.git"
        checkout = root / "checkout"
        git("init", "--bare", "-q", str(remote))
        git("clone", "-q", str(remote), str(checkout))
        git("config", "user.name", "ai-sdlc-test", cwd=checkout)
        git("config", "user.email", "ai-sdlc@example.invalid", cwd=checkout)

        config = TrustedOperatorRuntimeConfig(
            target_repository=TARGET,
            store_repository=STORE,
            installation_ref="main",
            store_checkout=checkout,
            principal="future-write-principal",
            feature_bindings=(TrustedFeatureBinding(FEATURE, FEATURE_REF),),
            state_ref=STATE_REF,
        )
        github = FakeGitHubContents()
        policy_verifier = ProtectedDecisionPolicyVerifier(
            repository=STORE,
            state_ref=STATE_REF,
            operation_profile=VERTICAL_PROFILE,
            policy_loader=fixture_policy_loader,
        )
        bundle = build_v03_write_ready_operator_bundle(
            config=config,
            adapter_id=ADAPTER,
            target_read_token="target-token",
            store_token="store-token",
            policy_verifier=policy_verifier,
            feature_gateway=FixtureFeatureGateway(),
            trusted_context_digest="v03-write-runtime-digest",
            github_api_base="https://api.github.test",
            reader_http_get=github,
            protection_verifier=FixtureVerifier(),
        )

        write_surface = set(bundle.backends) & KNOWN_SEMANTIC_WRITES
        require(write_surface == REQUIRED_V03_WRITE_SLICE, f"unexpected v0.3 semantic write surface: {sorted(write_surface)}")

        target = {"repository": TARGET, "feature_id": FEATURE}
        trusted = bundle.trusted_context_provider.for_request(target)
        started = dispatch(
            {
                "api_version": API_VERSION,
                "request_id": "v03-write-start",
                "capability": "operation.start",
                "target": target,
                "context": {"expected_feature_revision": 7},
                "idempotency_key": "v03-write-ready-operation",
                "client_identity": {"adapter_id": ADAPTER},
                "payload": {},
            },
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(started["ok"] is True, started)
        operation_id = started["result"]["operation_id"]
        projection = rebuild_projection(bundle.runtime.backend.read_snapshot(), operation_id)
        require(projection["operation_profile"] == VERTICAL_PROFILE, projection)
        require(projection["target_repository"] == "dream-xin/fixture", projection)
        require(projection["feature_id"] == FEATURE, projection)
        require(projection["expected_feature_revision"] == 7, projection)

    print("Operator v0.3 write-ready runtime validation passed")
    print(f"- operation profile: {VERTICAL_PROFILE}")
    print("- exact trusted Feature revision verified before start")
    print("- Decision composition uses the real ProtectedDecisionPolicyVerifier type")
    print("- frozen semantic write surface: operation.start/cancel + decision.respond + notification.ack")
    print("- operation.resume is not exposed by the frozen AI-client write slice")


if __name__ == "__main__":
    main()
