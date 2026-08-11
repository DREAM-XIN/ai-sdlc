#!/usr/bin/env python3
"""Adversarial validation for Decision FeatureTruthGateway production binding."""
from __future__ import annotations

from operator_configured_feature_event_gateway import TrustedFeatureEventTarget
from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway, TrustedCandidateSnapshot
from operator_github_feature_event_gateway import FeatureEventGatewayError
from operator_production_feature_event_gateway import ProductionConfiguredFeatureEventGateway, TrustedFeatureEventWriteScope
from operator_store import plan_operation_start
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE

REPOSITORY = "dream-xin/fixture"
OTHER_REPOSITORY = "dream-xin/other"
FEATURE = "F-DECISION-TRUTH-0001"
REF = "feature/F-DECISION-TRUTH-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
HEAD = "a" * 40
NOW = "2026-08-11T05:45:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FixtureTransport:
    def __init__(self):
        self.manifest = {
            "version": "0.1.0",
            "feature": {"id": FEATURE, "title": "Decision truth fixture"},
            "revision": 7,
            "workflow": {
                "profile": "standard-feature",
                "status": "ACTIVE",
                "current_stage": "implementation",
                "stages": [{"id": "implementation", "status": "WORKING"}],
            },
            "tasks": [],
            "artifacts": [],
            "gates": [],
            "applied_events": [],
        }
        self.reads = []

    def read_feature(self, *, repository, feature_id, target_ref):
        self.reads.append((repository, feature_id, target_ref))
        return dict(self.manifest)


class CandidateProvider:
    def __init__(self, result=None):
        self.result = result or TrustedCandidateSnapshot(230, HEAD)
        self.calls = []

    def current_candidate(self, *, operation_id, repository, feature_id, target_ref):
        self.calls.append((operation_id, repository, feature_id, target_ref))
        return self.result


def runtime():
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository="dream-xin/control", state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )


def start(runtime, *, repository=REPOSITORY, feature_id=FEATURE, revision=7, key="decision-truth"):
    result = runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=repository,
            feature_id=feature_id,
            expected_revision=revision,
            idempotency_key=key,
            occurred_at=NOW,
            trusted_context_digest="decision-truth-fixture",
            operation_profile=VERTICAL_PROFILE,
        )
    )
    return str(result.result["operation_id"])


def configured_gateway(transport):
    return ProductionConfiguredFeatureEventGateway(
        scope=TrustedFeatureEventWriteScope(
            repository=REPOSITORY,
            default_branch="main",
            targets=(TrustedFeatureEventTarget(FEATURE, REF),),
        ),
        transport=transport,
    )


def assert_code(code, fn):
    try:
        fn()
    except Exception as exc:
        require(getattr(exc, "code", None) == code, (code, type(exc).__name__, str(exc)))
    else:
        raise AssertionError(f"expected {code}")


def main():
    store = runtime()
    operation_id = start(store)
    transport = FixtureTransport()
    candidates = CandidateProvider()
    adapter = DurableDecisionFeatureTruthGateway(
        runtime=store,
        feature_gateway=configured_gateway(transport),
        candidate_provider=candidates,
    )

    feature, manifest = adapter.read_feature(operation_id=operation_id)
    require(isinstance(feature, FeatureSnapshot), type(feature))
    require(feature.repository == REPOSITORY, feature)
    require(feature.feature_id == FEATURE and feature.target_ref == REF, feature)
    require(feature.revision == 7 and feature.candidate_head_sha == HEAD, feature)
    require(feature.candidate_pr_number == 230, feature)
    require(manifest["revision"] == 7, manifest)
    require(transport.reads == [(REPOSITORY, FEATURE, REF)], transport.reads)
    require(candidates.calls == [(operation_id, REPOSITORY, FEATURE, REF)], candidates.calls)

    # Client/caller cannot ask this adapter to persist Events: it is the read-only
    # accepted FeatureTruthGateway used by Decision response verification.
    require(not hasattr(adapter, "persist_decision_response"), "Feature truth adapter unexpectedly owns Decision Event writes")
    require(not hasattr(adapter, "persist_exact_event"), "Feature truth adapter unexpectedly exposes raw Feature Event persistence")

    foreign_operation = start(
        store,
        repository=OTHER_REPOSITORY,
        feature_id="F-OTHER",
        revision=7,
        key="foreign-operation",
    )
    assert_code("UNAUTHORIZED", lambda: adapter.read_feature(operation_id=foreign_operation))
    require(len(transport.reads) == 1, "foreign Operation reached trusted Feature read transport")

    transport.manifest["revision"] = 8
    assert_code("STALE_REVISION", lambda: adapter.read_feature(operation_id=operation_id))
    transport.manifest["revision"] = 7

    bad_candidates = CandidateProvider(result=object())
    bad_adapter = DurableDecisionFeatureTruthGateway(
        runtime=store,
        feature_gateway=configured_gateway(transport),
        candidate_provider=bad_candidates,
    )
    assert_code("INTERNAL_FAILURE", lambda: bad_adapter.read_feature(operation_id=operation_id))

    assert_code("INVALID_REQUEST", lambda: adapter.read_feature(operation_id="op-not-found"))

    print("Decision durable Feature truth validation passed")
    print("- operation_id is the only accepted lookup authority")
    print("- repository/Feature/revision come from durable Operation state")
    print("- target ref comes from server-owned Feature configuration")
    print("- current candidate head comes from a separate trusted provider")
    print("- foreign Operation, stale Feature and invalid candidate provider fail closed")
    print("- Feature truth adapter exposes no Feature Event write method")


if __name__ == "__main__":
    main()
