#!/usr/bin/env python3
"""Adversarial validation for the durable v0.3 one-shot external-create fence."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from operator_store import StoreCommandError, plan_cancel, plan_operation_start, plan_takeover
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import CasConflict, GitStateRefBackend, MemoryStateRefBackend
from operator_store_model import StoreSnapshot, operation_events, projection_path, rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE
from operator_effect_lineage_fences import (
    plan_lineage_authorize_launch,
    plan_lineage_dispatch_claim,
    plan_lineage_external_create_attempt,
)
from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_effect_rollout import (
    EffectLineageWriteFence,
    LINEAGE_WRITER_CAPABILITY,
    REQUIRED_FENCED_CAPABILITIES,
    VerifiedEffectLineageRollout,
)
from operator_external_create_attempt import (
    external_create_attempt_path,
    find_external_create_attempt,
    plan_external_create_attempt,
)
from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway

REPO = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-15T14:20:00Z"
TRUST = "external-create-fence-validator"
FEATURE = "F-EXTERNAL-CREATE-FENCE-0001"
CANDIDATE = "a" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_code(code, fn):
    try:
        fn()
    except StoreCommandError as exc:
        require(exc.code == code, f"expected {code}, got {exc.code}: {exc}")
        return
    raise AssertionError(f"expected StoreCommandError {code}")


def rollout():
    return VerifiedEffectLineageRollout(
        repository=REPO.lower(),
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        effect_lineage_required=True,
        policy_ref="protected://validator/rollout",
        policy_digest="rollout-digest",
        writer_capability=LINEAGE_WRITER_CAPABILITY,
        writer_fence_receipt_ref="protected://validator/fence",
        writer_fence_receipt_digest="fence-digest",
        test_only=False,
    )


def runtime_for(backend):
    return OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
        plan_guard=EffectLineageWriteFence(rollout()),
    )


def seed_authorized(runtime, *, feature_id=FEATURE):
    started = runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id=feature_id,
            expected_revision=7,
            idempotency_key=f"start-{feature_id}",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            operation_profile=VERTICAL_PROFILE,
        )
    ).result
    operation_id = started["operation_id"]
    reservation = runtime.commit_replanned(
        lambda snapshot: plan_lineage_gated_reservation(
            snapshot,
            operation_id=operation_id,
            generation=rebuild_projection(snapshot, operation_id)["generation"],
            target_repository=REPO,
            feature_id=feature_id,
            expected_revision=7,
            current_stage="code-review",
            task_identity="vertical:code-review:reviewer",
            role="reviewer",
            candidate_head_sha=CANDIDATE,
            current_target_ref=f"feature/{feature_id}",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            logical_work_slot="CODE_REVIEW",
            task_id="reviewer-task",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            trusted_profile_digest="profile-digest",
        )
    ).result
    effect_key = reservation["semantic_effect_key"]
    lineage_id = reservation["effect_lineage_id"]
    generation = rebuild_projection(runtime.backend.read_snapshot(), operation_id)["generation"]
    claim = runtime.commit_replanned(
        lambda snapshot: plan_lineage_dispatch_claim(
            snapshot,
            effect_lineage_id=lineage_id,
            operation_id=operation_id,
            generation=generation,
            effect_key=effect_key,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    ).result
    dispatch_id = "vertical-one-shot-reviewer"
    runtime.commit_replanned(
        lambda snapshot: plan_lineage_authorize_launch(
            snapshot,
            effect_lineage_id=lineage_id,
            operation_id=operation_id,
            generation=generation,
            claim_id=claim["claim_id"],
            dispatch_id=dispatch_id,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=7,
            verified_stage="code-review",
            verified_candidate_head_sha=CANDIDATE,
        )
    )
    return {
        "operation_id": operation_id,
        "generation": generation,
        "lineage_id": lineage_id,
        "semantic_effect_key": effect_key,
        "external_dispatch_key": claim["external_dispatch_key"],
        "claim_id": claim["claim_id"],
        "dispatch_id": dispatch_id,
    }


def binding(workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml"):
    return {
        "worker_id": "code-review-reviewer-claude" if "claude" in workflow else "code-review-reviewer-copilot",
        "role": "reviewer",
        "profile": "claude" if "claude" in workflow else "copilot",
        "workflow_file": workflow,
        "selection_policy_id": "v03-frozen-reviewer-provider-order/v1",
        "default_branch": "main",
        "credential_name": "ANTHROPIC_API_KEY" if "claude" in workflow else "COPILOT_GITHUB_TOKEN",
    }


def attempt_plan(snapshot, seeded, *, generation=None, claim_id=None, dispatch_id=None, execution=None):
    return plan_lineage_external_create_attempt(
        snapshot,
        operation_id=seeded["operation_id"],
        generation=seeded["generation"] if generation is None else generation,
        claim_id=seeded["claim_id"] if claim_id is None else claim_id,
        dispatch_id=seeded["dispatch_id"] if dispatch_id is None else dispatch_id,
        semantic_effect_key=seeded["semantic_effect_key"],
        external_dispatch_key_value=seeded["external_dispatch_key"],
        execution_binding=execution or binding(),
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )


def validate_real_git_cas_single_winner():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-one-shot-") as directory:
        root = Path(directory)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "AI-SDLC Validator"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "validator@example.invalid"], cwd=root, check=True)
        backend = GitStateRefBackend(repo_path=root, repository=REPO, state_ref=STATE_REF)
        runtime = runtime_for(backend)
        seeded = seed_authorized(runtime)
        pre_effect = backend.read_snapshot()
        plan_a = attempt_plan(pre_effect, seeded)
        plan_b = attempt_plan(pre_effect, seeded)
        require(plan_a.result["acquired"] and plan_b.result["acquired"], "pre-CAS runners did not both preselect create ownership")
        guard = runtime.plan_guard
        guard(pre_effect, plan_a)
        guard(pre_effect, plan_b)
        receipt = runtime.protected_receipt()
        backend.commit(plan_a, receipt)
        try:
            backend.commit(plan_b, receipt)
        except CasConflict:
            pass
        else:
            raise AssertionError("second stale real-Git attempt commit did not lose CAS")
        replanned = runtime.commit_replanned(lambda snapshot: attempt_plan(snapshot, seeded))
        require(replanned.result["acquired"] is False, "CAS loser did not become lookup/adoption-only")
        path = external_create_attempt_path(seeded["semantic_effect_key"])
        require(path in backend.read_snapshot().files, "one-shot attempt artifact missing after real-Git CAS")


def validate_replay_forgery_takeover_and_projection_rebuild():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = runtime_for(backend)
    seeded = seed_authorized(runtime)
    first = runtime.commit_replanned(lambda snapshot: attempt_plan(snapshot, seeded))
    require(first.result["acquired"] is True, "first attempt did not acquire")
    replay = runtime.commit_replanned(lambda snapshot: attempt_plan(snapshot, seeded))
    require(replay.result["acquired"] is False, "exact replay minted second permission")
    expect_code(
        "POLICY_DENIED",
        lambda: attempt_plan(backend.read_snapshot(), seeded, dispatch_id="forged-dispatch"),
    )

    runtime.commit_replanned(
        lambda snapshot: plan_takeover(
            snapshot,
            operation_id=seeded["operation_id"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    generation = rebuild_projection(backend.read_snapshot(), seeded["operation_id"])["generation"]
    require(generation == seeded["generation"] + 1, "takeover did not advance generation")
    reservation = runtime.commit_replanned(
        lambda snapshot: plan_lineage_gated_reservation(
            snapshot,
            operation_id=seeded["operation_id"],
            generation=generation,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=7,
            current_stage="code-review",
            task_identity="vertical:code-review:reviewer",
            role="reviewer",
            candidate_head_sha=CANDIDATE,
            current_target_ref=f"feature/{FEATURE}",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            logical_work_slot="CODE_REVIEW",
            task_id="reviewer-task",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            trusted_profile_digest="profile-digest",
        )
    ).result
    require(reservation["semantic_effect_key"] == seeded["semantic_effect_key"], "takeover changed semantic effect identity")
    claim = runtime.commit_replanned(
        lambda snapshot: plan_lineage_dispatch_claim(
            snapshot,
            effect_lineage_id=seeded["lineage_id"],
            operation_id=seeded["operation_id"],
            generation=generation,
            effect_key=seeded["semantic_effect_key"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    ).result
    runtime.commit_replanned(
        lambda snapshot: plan_lineage_authorize_launch(
            snapshot,
            effect_lineage_id=seeded["lineage_id"],
            operation_id=seeded["operation_id"],
            generation=generation,
            claim_id=claim["claim_id"],
            dispatch_id="vertical-one-shot-reviewer-g1",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=7,
            verified_stage="code-review",
            verified_candidate_head_sha=CANDIDATE,
        )
    )
    adopted = runtime.commit_replanned(
        lambda snapshot: attempt_plan(
            snapshot,
            seeded,
            generation=generation,
            claim_id=claim["claim_id"],
            dispatch_id="vertical-one-shot-reviewer-g1",
        )
    )
    require(adopted.result["acquired"] is False, "takeover minted second create permission")
    require(adopted.result["created_generation"] == 0, "takeover replaced immutable creator generation")

    snapshot = backend.read_snapshot()
    files = dict(snapshot.files)
    files.pop(projection_path(seeded["operation_id"]), None)
    backend.snapshot = StoreSnapshot(ref_sha=snapshot.ref_sha, files=files)
    found = find_external_create_attempt(backend.read_snapshot(), external_dispatch_key=seeded["external_dispatch_key"])
    require(found is not None and found["created_generation"] == 0, "projection deletion lost one-shot authority")


def validate_raw_writer_is_fenced():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = runtime_for(backend)
    seeded = seed_authorized(runtime)
    raw = plan_external_create_attempt(
        backend.read_snapshot(),
        operation_id=seeded["operation_id"],
        generation=seeded["generation"],
        claim_id=seeded["claim_id"],
        dispatch_id=seeded["dispatch_id"],
        semantic_effect_key=seeded["semantic_effect_key"],
        external_dispatch_key_value=seeded["external_dispatch_key"],
        execution_binding=binding(),
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )
    try:
        runtime.plan_guard(backend.read_snapshot(), raw)
    except StoreCommandError as exc:
        require(exc.code == "MIXED_WRITER_FENCED", f"raw attempt failed with wrong fence code: {exc.code}")
    else:
        raise AssertionError("unmarked raw external-create attempt bypassed Effect Lineage writer fence")
    require("raw-external-create-attempt" in REQUIRED_FENCED_CAPABILITIES, "writer-fence capability inventory did not expand")


class FakeExternalGateway:
    def __init__(self, *, workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml", lost_ack=False):
        self.workflow = workflow
        self.lost_ack = lost_ack
        self.post_count = 0
        self.lookup_state = "NOT_LAUNCHED"
        self.receipt_id = None

    def execution_binding(self, *, dispatch):
        return binding(self.workflow)

    def lookup_execution_binding(self, *, execution_binding, external_dispatch_key):
        if execution_binding["workflow_file"] != self.workflow and self.lookup_state == "NOT_LAUNCHED":
            return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
        return {"lookup_state": self.lookup_state, "receipt_id": self.receipt_id}

    def lookup(self, *, external_dispatch_key):
        return {"lookup_state": self.lookup_state, "receipt_id": self.receipt_id}

    def launch(self, *, dispatch):
        self.post_count += 1
        if self.lost_ack:
            raise RuntimeError("accepted POST acknowledgement lost")
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}


def dispatch_dict(seeded):
    return {
        "operation_id": seeded["operation_id"],
        "operation_generation": seeded["generation"],
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": seeded["semantic_effect_key"],
        "external_dispatch_key": seeded["external_dispatch_key"],
        "dispatch_id": seeded["dispatch_id"],
        "role": "reviewer",
    }


def validate_lost_ack_fresh_process_provider_drift_and_cancel():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = runtime_for(backend)
    seeded = seed_authorized(runtime)
    delegate = FakeExternalGateway(lost_ack=True)
    gateway = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime,
        delegate=delegate,
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    dispatch = dispatch_dict(seeded)
    try:
        gateway.launch(dispatch=dispatch)
    except RuntimeError:
        pass
    else:
        raise AssertionError("lost-ACK delegate did not simulate transport failure")
    require(delegate.post_count == 1, "first owner did not issue exactly one POST")
    require(find_external_create_attempt(backend.read_snapshot(), external_dispatch_key=seeded["external_dispatch_key"]) is not None, "POST happened before durable attempt")

    fresh = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime,
        delegate=delegate,
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    retry = fresh.launch(dispatch=dispatch)
    require(retry["lookup_state"] == "UNKNOWN", "attempt + invisible run became retry-eligible")
    require(delegate.post_count == 1, "fresh process issued second POST after lost ACK")
    require(fresh.lookup(external_dispatch_key=seeded["external_dispatch_key"])["lookup_state"] == "UNKNOWN", "attempt-aware NOT_LAUNCHED did not fail closed")

    delegate.lookup_state = "LAUNCHED"
    delegate.receipt_id = "run-original"
    adopted = fresh.lookup(external_dispatch_key=seeded["external_dispatch_key"])
    require(adopted == {"lookup_state": "LAUNCHED", "receipt_id": "run-original"}, "later visibility did not adopt exact original run")

    drift = FakeExternalGateway(workflow="ai-sdlc-gh-aw-reviewer-copilot.lock.yml")
    drift.lookup_state = "NOT_LAUNCHED"
    drifted = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime,
        delegate=drift,
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    require(drifted.launch(dispatch=dispatch)["lookup_state"] == "UNKNOWN", "provider drift re-armed external create")
    require(drift.post_count == 0, "provider drift crossed POST boundary")

    backend2 = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime2 = runtime_for(backend2)
    seeded2 = seed_authorized(runtime2, feature_id="F-EXTERNAL-CREATE-CANCEL-0002")
    runtime2.commit_replanned(
        lambda snapshot: plan_cancel(
            snapshot,
            operation_id=seeded2["operation_id"],
            reason="cancel after launch authorization",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    delegate2 = FakeExternalGateway()
    gateway2 = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime2,
        delegate=delegate2,
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    receipt = gateway2.launch(dispatch=dispatch_dict(seeded2))
    require(receipt["lookup_state"] == "LAUNCHED" and delegate2.post_count == 1, "authorization-before-cancel ordering was incorrectly revoked")


def main():
    validate_real_git_cas_single_winner()
    validate_replay_forgery_takeover_and_projection_rebuild()
    validate_raw_writer_is_fenced()
    validate_lost_ack_fresh_process_provider_drift_and_cancel()
    print("Operator one-shot external-create fence validation passed")
    print("- real Git CAS elects one durable attempt creator; loser replans lookup-only")
    print("- replay, forged authorization, takeover and projection rebuild preserve immutable authority")
    print("- raw attempt writer is fenced and capability inventory includes raw-external-create-attempt")
    print("- lost ACK, fresh process, provider drift and cancellation orderings never mint a second POST")


if __name__ == "__main__":
    main()
