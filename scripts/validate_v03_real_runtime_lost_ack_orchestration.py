#!/usr/bin/env python3
"""End-to-end deterministic validation for the two-process lost-ACK harness.

This is harness validation, not Issue #221 release evidence. The Store uses the
real reducers/Effect-Lineage fence and both phases use the accepted Vertical
executor/recovery code, while the external runtime is an in-memory lookup-first
model of the production GitHub Actions transport.
"""
from __future__ import annotations

from types import SimpleNamespace

from operator_effect_resolution import (
    ALLOWED_RESOLUTION_CHOICES,
    EFFECT_RESOLUTION_POLICY_SCHEMA,
    ProtectedEffectResolutionPolicyVerifier,
)
from operator_effect_rollout import EffectLineageWriteFence, LINEAGE_WRITER_CAPABILITY, VerifiedEffectLineageRollout
from operator_store_backends import OperationStartBackend, OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import digest_json, operation_events, rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from operator_vertical_runtime import VerticalLoopStartBackend
from v03_real_runtime_fault_injection import LostAckCrashAfterLaunchDispatchGateway
from v03_real_runtime_lost_ack_orchestration import (
    LostAckOrchestrationError,
    derive_lost_ack_dispatch_binding,
    run_phase1_start_and_crash,
    run_phase2_takeover_and_adopt,
)

REPOSITORY = "dream-xin/fixture"
FEATURE = "F-LOST-ACK-ORCHESTRATION-0001"
REF = "feature/F-LOST-ACK-ORCHESTRATION-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
CANDIDATE = "a" * 40
TRUSTED_DIGEST = "trusted-lost-ack-orchestration"
IDEMPOTENCY = "fi-lost-ack-g0-g1"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return f"2026-08-11T00:00:{self.value:02d}Z"


class FeatureGateway:
    def __init__(self, manifest):
        self.manifest = manifest

    def read_feature(self, *, operation_id):
        return (
            FeatureSnapshot.from_manifest(
                repository=REPOSITORY,
                target_ref=REF,
                manifest=self.manifest,
                candidate_pr_number=230,
                candidate_head_sha=CANDIDATE,
            ),
            self.manifest,
        )


class UnusedPersistGateway:
    def persist_feature_event(self, *, event, target_ref):
        raise AssertionError("lost-ACK dispatch harness must not Persist a Feature Event")

    def lookup_feature_event(self, *, event_id, target_ref):
        raise AssertionError("lost-ACK dispatch harness must not reconcile Persist")


class ExternalRuntime:
    def __init__(self):
        self.runs = {}
        self.post_count = 0


class LookupFirstDispatchGateway:
    """Production-shaped dispatch: exact-key lookup before any external POST."""

    def __init__(self, external_runtime):
        self.external_runtime = external_runtime
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        key = str(dispatch["external_dispatch_key"])
        self.launch_calls.append(key)
        existing = self.external_runtime.runs.get(key)
        if existing is not None:
            return {"lookup_state": "LAUNCHED", "receipt_id": existing}
        self.external_runtime.post_count += 1
        receipt = f"run-{self.external_runtime.post_count}"
        self.external_runtime.runs[key] = receipt
        return {"lookup_state": "LAUNCHED", "receipt_id": receipt}

    def lookup(self, *, external_dispatch_key):
        key = str(external_dispatch_key)
        self.lookup_calls.append(key)
        receipt = self.external_runtime.runs.get(key)
        if receipt is None:
            return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
        return {"lookup_state": "LAUNCHED", "receipt_id": receipt}


class TrustedContextProvider:
    def for_request(self, target):
        require(target == {"repository": REPOSITORY, "feature_id": FEATURE}, "trusted context requested for wrong target")
        return {
            "trusted_context_digest": TRUSTED_DIGEST,
            "feature_verification": {
                "repository": REPOSITORY,
                "feature_id": FEATURE,
                "revision": 11,
            },
        }


def manifest(*, current_stage="implementation", stage_status="WORKING"):
    return {
        "revision": 11,
        "feature": {"id": FEATURE, "title": "lost ack fixture"},
        "workflow": {
            "status": "ACTIVE",
            "current_stage": current_stage,
            "stages": [
                {"id": "requirement", "status": "DONE"},
                {"id": "requirement-review", "status": "DONE"},
                {"id": "requirement-gate", "status": "PASS"},
                {"id": "design", "status": "DONE"},
                {"id": "design-review", "status": "DONE"},
                {"id": "design-gate", "status": "PASS"},
                {"id": "plan", "status": "DONE"},
                {"id": "implementation", "status": stage_status if current_stage == "implementation" else "DONE"},
                {"id": "code-review", "status": stage_status if current_stage == "code-review" else "TODO"},
                {"id": "code-gate", "status": "PENDING"},
                {"id": "verification", "status": stage_status if current_stage == "verification" else "TODO"},
                {"id": "verification-gate", "status": "PENDING"},
                {"id": "acceptance", "status": "TODO"},
                {"id": "release-gate", "status": "PENDING"},
            ],
        },
    }


def resolution_policy():
    policy = {
        "schema_version": EFFECT_RESOLUTION_POLICY_SCHEMA,
        "repository": REPOSITORY,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": "protected://fixture/effect-resolution",
        "policy_epoch": "fixture-v1",
        "authority_id": "fixture-resolution-authority",
        "allowed_choices": sorted(ALLOWED_RESOLUTION_CHOICES),
        "allowed_resolvers": ["fixture-resolver"],
        "trusted_profile_digest": "fixture-profile",
        "strong_evidence_types": [],
        "evidence_source_id": "fixture-evidence",
        "evidence_source_digest": "fixture-evidence-digest",
    }
    policy["policy_digest"] = digest_json(policy)
    return policy


def resolution_verifier():
    policy = resolution_policy()
    return ProtectedEffectResolutionPolicyVerifier(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_args: policy,
        evidence_fact_loader=lambda *_args: {"type": "INSUFFICIENT"},
    )


def rollout():
    return VerifiedEffectLineageRollout(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        effect_lineage_required=True,
        policy_ref="protected://fixture/effect-lineage",
        policy_digest="fixture-lineage-policy",
        writer_capability=LINEAGE_WRITER_CAPABILITY,
        writer_fence_receipt_ref="protected://fixture/writer-fence",
        writer_fence_receipt_digest="fixture-writer-fence-digest",
        test_only=False,
    )


def make_runtime(backend, clock):
    return OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=clock,
        plan_guard=EffectLineageWriteFence(rollout()),
    )


def make_executor(runtime, feature_gateway, dispatch_gateway):
    base = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=UnusedPersistGateway(),
        dispatch_gateway=dispatch_gateway,
        config=TrustedVerticalExecutorConfig(
            target_ref=REF,
            trusted_context_digest=TRUSTED_DIGEST,
            effect_lineage_required=True,
            old_writers_quiesced=True,
            rollout_policy_digest="fixture-lineage-policy",
            writer_fence_receipt_digest="fixture-writer-fence-digest",
            max_auto_steps=4,
        ),
        resolution_policy_verifier=resolution_verifier(),
    )
    return TrustedRecoveringVerticalExecutor(
        base_executor=base,
        content_loader=lambda *_args, **_kwargs: "unused",
        trusted_role_policy="fixture-role-policy",
        collector_namespace_policy="fixture-collector-policy",
    )


def make_bundle(runtime, executor):
    start = VerticalLoopStartBackend(
        delegate=OperationStartBackend(runtime, operation_profile=VERTICAL_PROFILE),
        executor=executor,
    )
    read_bundle = SimpleNamespace(trusted_context_provider=TrustedContextProvider())
    write_bundle = SimpleNamespace(read_bundle=read_bundle)
    return SimpleNamespace(
        runtime=runtime,
        executor=executor,
        backends={"operation.start": start},
        write_bundle=write_bundle,
    )


def event_count(backend, operation_id, event_type, generation=None):
    rows = [event for event in operation_events(backend.read_snapshot(), operation_id) if event["event_type"] == event_type]
    if generation is not None:
        rows = [event for event in rows if event["operation_generation"] == generation]
    return len(rows)


def main():
    fixture_manifest = manifest()
    binding = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        manifest=fixture_manifest,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
        idempotency_key=IDEMPOTENCY,
        occurred_at="2026-08-11T00:00:00Z",
    )
    require(binding.current_stage == "implementation" and binding.role == "developer", "selector-derived dispatch identity drifted")
    require(
        len(binding.semantic_effect_key) == 64
        and all(ch in "0123456789abcdef" for ch in binding.semantic_effect_key),
        "binding lacks canonical SHA-256 semantic effect identity",
    )
    require(binding.external_dispatch_key.startswith("dispatch-"), "binding lacks external dispatch identity")

    # READY code-review requires a Persist stage-start first and therefore must not
    # be accepted by this exact pre-bound launch fault scenario.
    try:
        derive_lost_ack_dispatch_binding(
            repository=REPOSITORY,
            feature_id=FEATURE,
            target_ref=REF,
            manifest=manifest(current_stage="code-review", stage_status="READY"),
            candidate_pr_number=230,
            candidate_head_sha=CANDIDATE,
            idempotency_key="fi-not-dispatch-ready",
            occurred_at="2026-08-11T00:00:00Z",
        )
    except LostAckOrchestrationError:
        pass
    else:
        raise AssertionError("stage-start Persist fixture was accepted as immediate lost-ACK dispatch")

    backend = MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF)
    clock = Clock()
    external = ExternalRuntime()
    feature_gateway = FeatureGateway(fixture_manifest)

    phase1_transport = LookupFirstDispatchGateway(external)
    phase1_fault = LostAckCrashAfterLaunchDispatchGateway(
        delegate=phase1_transport,
        expected_external_dispatch_key=binding.external_dispatch_key,
    )
    runtime_g0 = make_runtime(backend, clock)
    executor_g0 = make_executor(runtime_g0, feature_gateway, phase1_fault)
    phase1 = run_phase1_start_and_crash(
        bundle=make_bundle(runtime_g0, executor_g0),
        binding=binding,
        adapter_id="fixture-adapter",
    )
    require(phase1["generation"] == 0, "phase 1 evidence generation changed")
    require(external.post_count == 1, "phase 1 did not create exactly one modeled external effect")
    require(phase1_transport.launch_calls == [binding.external_dispatch_key], "phase 1 launch used wrong external key")
    require(phase1_transport.lookup_calls == [], "phase 1 performed same-process lookup after injected crash")
    require(event_count(backend, binding.operation_id, "dispatch.launch.authorized", 0) == 1, "G0 launch authorization count drifted")
    require(event_count(backend, binding.operation_id, "dispatch.launch.lookup-recorded", 0) == 0, "G0 unexpectedly recorded launch lookup")

    # Fresh process objects: new runtime, executor and dispatch gateway, but the
    # same durable backend and modeled external authority.
    phase2_transport = LookupFirstDispatchGateway(external)
    runtime_g1 = make_runtime(backend, clock)
    executor_g1 = make_executor(runtime_g1, feature_gateway, phase2_transport)
    phase2 = run_phase2_takeover_and_adopt(
        bundle=make_bundle(runtime_g1, executor_g1),
        binding=binding,
    )
    require(phase2["generation"] == 1, "phase 2 evidence generation changed")
    require(phase2["external_dispatch_key"] == binding.external_dispatch_key, "phase 2 changed external effect identity")
    require(phase2["runtime_receipt_identity"] == "run-1", "phase 2 did not adopt original runtime receipt")
    require(external.post_count == 1, "G1 takeover created a duplicate modeled external effect")
    require(phase2_transport.launch_calls == [binding.external_dispatch_key], "G1 did not re-enter accepted same-key launch path exactly once")
    require(event_count(backend, binding.operation_id, "operation.generation.started", 1) == 1, "trusted takeover did not durably start G1")
    require(event_count(backend, binding.operation_id, "dispatch.launch.authorized", 1) == 1, "G1 launch authorization count drifted")
    require(event_count(backend, binding.operation_id, "dispatch.launch.lookup-recorded", 1) == 1, "G1 exact receipt adoption was not durable")

    projection = rebuild_projection(backend.read_snapshot(), binding.operation_id)
    require(projection["generation"] == 1, "final projection is not G1")
    require(projection["status"] == "WAITING_EXTERNAL", f"same-key adoption should wait for callback, got {projection['status']}")

    print("v0.3 lost-ACK G0 crash -> G1 same-key adoption orchestration validation passed")
    print("- exact first dispatch identity is derived from accepted Feature selector and Effect identity functions")
    print("- G0 durably authorizes launch, creates one external run, then crashes before local launch lookup evidence")
    print("- fresh G1 uses trusted plan_vertical_takeover and the accepted lineage-required executor")
    print("- G1 re-enters launch with the same external key; lookup-first transport adopts run-1 with zero additional POST")
    print("- final durable state is generation 1 WAITING_EXTERNAL with one exact G1 launch lookup receipt")
    print("- this is harness validation only and does not constitute Issue #221 real-runtime release evidence")


if __name__ == "__main__":
    main()
