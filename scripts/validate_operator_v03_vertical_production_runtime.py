#!/usr/bin/env python3
"""Deterministic validation for shared Store + Vertical production composition.

The test constructs production-shaped runtime objects but performs no Git/GitHub
write, Worker dispatch, Feature Persist, or external effect.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

from operator_decision_policy import ProtectedDecisionPolicyVerifier
from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
from operator_effect_rollout import EffectLineageWriteFence, LINEAGE_WRITER_CAPABILITY, VerifiedEffectLineageRollout
from operator_production_bundle import build_trusted_operator_backend_bundle_from_runtime
from operator_production_feature_event_gateway import ProductionConfiguredFeatureEventGateway
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_production_write_bundle import REQUIRED_V03_WRITE_SLICE
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, ProtectionReceipt, StaticProtectionVerifier
from operator_store_remote_git import RemoteGitStateRefBackend
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor
from operator_vertical_runtime import VerticalLoopStartBackend
from operator_v03_vertical_production_runtime import (
    TrustedV03VerticalProductionBundle,
    _DeferredExactVerticalPersistGateway,
    _feature_scoped_adapter_config,
    _validate_decision_policy_binding,
    build_v03_vertical_production_bundle,
)

STORE_REPOSITORY = "dream-xin/control"
TARGET_REPOSITORY = "dream-xin/target"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
STORE_CHECKOUT = Path("/tmp/operator-v03-compose-store")
FEATURE_ID = "F-V03-VERTICAL-COMPOSE-0001"
TARGET_REF = "feature/F-V03-VERTICAL-COMPOSE-0001"
SECOND_FEATURE_ID = "F-V03-VERTICAL-COMPOSE-0002"
SECOND_TARGET_REF = "feature/F-V03-VERTICAL-COMPOSE-0002"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class InertProductionProtectionVerifier:
    test_only = False

    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity="inert-production-shape",
            verified_at="2026-08-11T00:00:00Z",
            policy_digest="inert-production-policy",
        )


class InertResolutionVerifier(ProtectedEffectResolutionPolicyVerifier):
    def __init__(self):
        self.repository = STORE_REPOSITORY
        self.state_ref = STATE_REF
        self.operation_profile = VERTICAL_PROFILE

    def verify_current(self):
        return SimpleNamespace(proposal_profile_digest="inert-resolution-profile")


class InertRolloutVerifier:
    def verify(self, *, repository, state_ref, operation_profile):
        return VerifiedEffectLineageRollout(
            repository=repository,
            state_ref=state_ref,
            operation_profile=operation_profile,
            effect_lineage_required=True,
            policy_ref="protected://inert-effect-lineage",
            policy_digest="inert-effect-lineage-policy",
            writer_capability=LINEAGE_WRITER_CAPABILITY,
            writer_fence_receipt_ref="protected://inert-writer-fence",
            writer_fence_receipt_digest="inert-writer-fence-digest",
            test_only=False,
        )


class InertFeatureGateway:
    def read_feature(self, *, operation_id):
        raise AssertionError("full-builder construction must not read Feature truth")


class InertDispatchGateway:
    def launch(self, *, dispatch):
        raise AssertionError("full-builder construction must not launch Worker")

    def lookup(self, *, external_dispatch_key):
        raise AssertionError("full-builder construction must not inspect Worker dispatch")


def config(*, include_second=False):
    bindings = [TrustedFeatureBinding(FEATURE_ID, TARGET_REF)]
    if include_second:
        bindings.append(TrustedFeatureBinding(SECOND_FEATURE_ID, SECOND_TARGET_REF))
    return TrustedOperatorRuntimeConfig(
        target_repository=TARGET_REPOSITORY,
        store_repository=STORE_REPOSITORY,
        installation_ref="main",
        store_checkout=STORE_CHECKOUT,
        principal="operator-v03-compose",
        feature_bindings=tuple(bindings),
        state_ref=STATE_REF,
    )


def remote_runtime(
    *,
    repository=STORE_REPOSITORY,
    state_ref=STATE_REF,
    repo_path=STORE_CHECKOUT,
    remote_name="origin",
    verifier=None,
):
    return OperatorStoreRuntime(
        backend=RemoteGitStateRefBackend(
            repo_path=repo_path,
            repository=repository,
            state_ref=state_ref,
            remote_name=remote_name,
        ),
        protection_verifier=verifier or InertProductionProtectionVerifier(),
    )


def memory_runtime():
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=STORE_REPOSITORY, state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
    )


def policy_verifier(*, repository=STORE_REPOSITORY, state_ref=STATE_REF, operation_profile=VERTICAL_PROFILE):
    return ProtectedDecisionPolicyVerifier(
        repository=repository,
        state_ref=state_ref,
        operation_profile=operation_profile,
        policy_loader=lambda *_args: {},
    )


def validate_existing_runtime_helper():
    cfg = config()
    runtime = remote_runtime()
    bundle = build_trusted_operator_backend_bundle_from_runtime(
        config=cfg,
        adapter_id="openai-responses",
        target_read_token="trusted-read-token",
        runtime=runtime,
        reader_http_get=lambda *_args, **_kwargs: (500, {}),
        operation_profile=VERTICAL_PROFILE,
    )
    require(bundle.runtime is runtime, "production bundle did not preserve supplied Store runtime identity")
    require(bundle.backends["operation.start"].delegate.runtime is runtime, "operation.start does not share supplied runtime")
    require(bundle.backends["operation.cancel"].delegate.runtime is runtime, "operation.cancel does not share supplied runtime")

    bad_runtimes = (
        memory_runtime(),
        remote_runtime(repository="dream-xin/foreign"),
        remote_runtime(state_ref="refs/heads/foreign-state"),
        remote_runtime(repo_path=Path("/tmp/foreign-store")),
        remote_runtime(remote_name="foreign"),
        remote_runtime(verifier=StaticProtectionVerifier(status=PROTECTED)),
    )
    for bad in bad_runtimes:
        try:
            build_trusted_operator_backend_bundle_from_runtime(
                config=cfg,
                adapter_id="openai-responses",
                target_read_token="trusted-read-token",
                runtime=bad,
            )
        except ValueError:
            continue
        raise AssertionError("non-production or cross-boundary Store runtime was accepted")


def validate_feature_scope_and_policy_fences():
    original = config(include_second=True)
    scoped = _feature_scoped_adapter_config(
        original,
        feature_id=FEATURE_ID,
        target_ref=original.feature_ref(FEATURE_ID),
    )
    require(scoped.feature_ids == frozenset({FEATURE_ID}), "Vertical bundle was not narrowed to one Feature")
    require(scoped.feature_ref(FEATURE_ID) == TARGET_REF, "Feature/ref binding changed during narrowing")
    require(scoped.store_repository == original.store_repository, "scope narrowing changed Store authority")
    try:
        scoped.feature_ref(SECOND_FEATURE_ID)
    except Exception as exc:
        require(getattr(exc, "code", "") == "UNAUTHORIZED", exc)
    else:
        raise AssertionError("second Feature leaked into one-Feature Vertical bundle")

    _validate_decision_policy_binding(original, policy_verifier())
    for bad in (
        object(),
        policy_verifier(repository="dream-xin/foreign"),
        policy_verifier(state_ref="refs/heads/foreign-state"),
        policy_verifier(operation_profile="operator.other/v1"),
    ):
        try:
            _validate_decision_policy_binding(original, bad)
        except ValueError:
            continue
        raise AssertionError("Decision policy outside Store/profile trust domain was accepted")


def validate_deferred_persist_bridge():
    bridge = _DeferredExactVerticalPersistGateway()
    try:
        bridge.lookup_feature_event(event_id="EVT-UNBOUND", target_ref=TARGET_REF)
    except RuntimeError:
        pass
    else:
        raise AssertionError("deferred Persist bridge did not fail closed before binding")
    try:
        bridge.bind(object())
    except ValueError:
        pass
    else:
        raise AssertionError("deferred Persist bridge accepted non-authoritative gateway")
    exact_gateway = object.__new__(DurableVerticalFeaturePersistGateway)
    bridge.bind(exact_gateway)
    require(bridge.delegate is exact_gateway, "bridge lost exact Durable Persist delegate")
    try:
        bridge.bind(exact_gateway)
    except RuntimeError:
        pass
    else:
        raise AssertionError("deferred Persist bridge allowed second authority binding")


def validate_full_builder_construction():
    cfg = config(include_second=True)
    event_gateway = object.__new__(ProductionConfiguredFeatureEventGateway)
    bundle = build_v03_vertical_production_bundle(
        config=cfg,
        adapter_id="openai-responses",
        feature_id=FEATURE_ID,
        target_read_token="trusted-read-token",
        protection_verifier=InertProductionProtectionVerifier(),
        rollout_verifier=InertRolloutVerifier(),
        resolution_policy_verifier=InertResolutionVerifier(),
        feature_gateway=InertFeatureGateway(),
        feature_event_gateway=event_gateway,
        dispatch_gateway=InertDispatchGateway(),
        collector_content_loader=lambda *_args, **_kwargs: "inert-content",
        policy_verifier=policy_verifier(),
        trusted_context_digest="inert-trusted-context",
        collector_namespace_policy="collector/inert",
        trusted_role_policy="role/inert",
        reader_http_get=lambda *_args, **_kwargs: (500, {}),
    )

    require(isinstance(bundle.runtime.backend, RemoteGitStateRefBackend), "full builder did not create RemoteGit Store")
    require(isinstance(bundle.runtime.plan_guard, EffectLineageWriteFence), "full builder lost Effect Lineage fence")
    require(bundle.write_bundle.runtime is bundle.vertical_bundle.runtime, "adapter and Vertical split Store runtimes")
    require(bundle.write_bundle.config.feature_ids == frozenset({FEATURE_ID}), "full builder leaked second Feature")
    require(isinstance(bundle.executor, FailureClassifyingTrustedRecoveringVerticalExecutor), "full builder lost classified recovery")
    require(isinstance(bundle.backends["operation.start"], VerticalLoopStartBackend), "operation.start is not Vertical-bound")
    require(bundle.backends["operation.start"].executor is bundle.executor, "operation.start uses different executor")
    require("operation.resume" not in bundle.backends, "server-only operation.resume leaked to adapter")
    require(set(bundle.adapter_write_backends) == set(REQUIRED_V03_WRITE_SLICE), "adapter write slice expanded")
    require(bundle.decision_notification_coordinator.runtime is bundle.runtime, "Decision coordinator split Store runtime")

    persist_bridge = bundle.executor.base.persist_gateway
    require(isinstance(persist_bridge, _DeferredExactVerticalPersistGateway), "executor lacks deferred Persist bridge")
    require(isinstance(persist_bridge.delegate, DurableVerticalFeaturePersistGateway), "bridge not bound to Durable Persist")
    require(persist_bridge.delegate.runtime is bundle.runtime, "Durable Persist does not share Store runtime")
    require(persist_bridge.delegate.event_gateway is event_gateway, "Durable Persist changed Feature Event authority")


def call_names(function):
    tree = ast.parse(inspect.getsource(function))
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
    return names


def validate_composition_shape():
    calls = call_names(build_v03_vertical_production_bundle)
    require(calls.count("build_trusted_vertical_runtime") == 1, "must create Vertical runtime exactly once")
    require(calls.count("DurableVerticalFeaturePersistGateway") == 1, "must create Durable Persist exactly once")
    require(calls.count("build_trusted_operator_backend_bundle_from_runtime") == 1, "adapter must reuse existing Store runtime")
    require("build_trusted_operator_backend_bundle" not in calls, "composition creates independent adapter Store")
    require("build_trusted_operator_store_runtime" not in calls, "composition creates second Store runtime")
    require("build_github_operator_store_runtime" not in calls, "composition creates second GitHub Store runtime")
    require(calls.count("VerticalLoopStartBackend") == 1, "operation.start wrapped more than once")
    require(calls.count("extend_with_trusted_decision_writes") == 1, "Decision writes do not use accepted factory")
    source = inspect.getsource(build_v03_vertical_production_bundle)
    require(source.index("_validate_decision_policy_binding") < source.index("build_trusted_vertical_runtime"), "Decision policy checked after authority creation")
    require('"operation.resume" in write_bundle.backends' in source, "operation.resume leak fence missing")
    require("write_bundle.runtime is not vertical.runtime" in source, "shared-runtime identity fence missing")
    require("deferred_persist.delegate.runtime is not vertical.runtime" in source, "Persist shared-runtime fence missing")


def validate_client_surface_property():
    backend_map = {
        "operation.start": object(),
        "operation.cancel": object(),
        "decision.respond": object(),
        "notification.ack": object(),
        "project.inspect": object(),
    }
    fake_write = type("WriteBundle", (), {"backends": backend_map, "runtime": object()})()
    fake_vertical = type("VerticalBundle", (), {"executor": object(), "callback_coordinator": object()})()
    combined = TrustedV03VerticalProductionBundle(
        write_bundle=fake_write,
        vertical_bundle=fake_vertical,
        feature_id=FEATURE_ID,
    )
    require(set(combined.adapter_write_backends) == set(REQUIRED_V03_WRITE_SLICE), "adapter write surface expanded")
    require("operation.resume" not in combined.adapter_write_backends, "operation.resume leaked into write slice")


def main():
    validate_existing_runtime_helper()
    validate_feature_scope_and_policy_fences()
    validate_deferred_persist_bridge()
    validate_full_builder_construction()
    validate_composition_shape()
    validate_client_surface_property()
    print("v0.3 adapter + trusted Vertical production composition validation passed")
    print("- existing-runtime helper accepts only exact durable RemoteGit + non-test protection bindings")
    print("- one Store runtime is shared by adapter, Vertical, classified recovery, Durable Persist and Decision paths")
    print("- each bundle narrows multi-Feature installation scope to one exact Feature/ref")
    print("- Deferred Persist bridge is fail-closed and one-time bound to generation-aware Durable Persist")
    print("- operation.resume remains server-only and adapter write slice stays frozen")
    print("- composition creates no second Store, Vertical, Persist or dispatch authority")


if __name__ == "__main__":
    main()
