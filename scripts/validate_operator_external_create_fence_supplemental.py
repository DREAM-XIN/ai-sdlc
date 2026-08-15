#!/usr/bin/env python3
"""Supplemental cross-generation/provider ordering checks for Issue #286."""
from __future__ import annotations

from operator_store import StoreCommandError, plan_cancel, plan_operation_start, plan_takeover
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE
from operator_effect_lineage_fences import (
    plan_lineage_authorize_launch,
    plan_lineage_dispatch_claim,
    plan_lineage_external_create_attempt,
)
from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_effect_rollout import EffectLineageWriteFence, LINEAGE_WRITER_CAPABILITY, VerifiedEffectLineageRollout
from operator_external_create_attempt import plan_external_create_attempt
from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway

REPO = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-15T14:40:00Z"
TRUST = "external-create-fence-supplemental"
FEATURE = "F-EXTERNAL-CREATE-FENCE-SUPPLEMENTAL"
CANDIDATE = "b" * 40
CLAUDE = "ai-sdlc-gh-aw-reviewer-claude.lock.yml"
COPILOT = "ai-sdlc-gh-aw-reviewer-copilot.lock.yml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


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


def reviewer_binding(workflow):
    return {
        "worker_id": "code-review-reviewer-claude" if workflow == CLAUDE else "code-review-reviewer-copilot",
        "role": "reviewer",
        "profile": "claude" if workflow == CLAUDE else "copilot",
        "workflow_file": workflow,
        "selection_policy_id": "v03-frozen-reviewer-provider-order/v1",
        "default_branch": "main",
        "credential_name": "ANTHROPIC_API_KEY" if workflow == CLAUDE else "COPILOT_GITHUB_TOKEN",
    }


def seed_to_claim(runtime, feature_id):
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
    generation = rebuild_projection(runtime.backend.read_snapshot(), operation_id)["generation"]
    claim = runtime.commit_replanned(
        lambda snapshot: plan_lineage_dispatch_claim(
            snapshot,
            effect_lineage_id=reservation["effect_lineage_id"],
            operation_id=operation_id,
            generation=generation,
            effect_key=reservation["semantic_effect_key"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    ).result
    return {
        "operation_id": operation_id,
        "generation": generation,
        "lineage_id": reservation["effect_lineage_id"],
        "semantic_effect_key": reservation["semantic_effect_key"],
        "external_dispatch_key": reservation["external_dispatch_key"],
        "claim_id": claim["claim_id"],
    }


def authorize(runtime, seeded, dispatch_id, generation=None, claim_id=None):
    generation = seeded["generation"] if generation is None else generation
    claim_id = seeded["claim_id"] if claim_id is None else claim_id
    runtime.commit_replanned(
        lambda snapshot: plan_lineage_authorize_launch(
            snapshot,
            effect_lineage_id=seeded["lineage_id"],
            operation_id=seeded["operation_id"],
            generation=generation,
            claim_id=claim_id,
            dispatch_id=dispatch_id,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=7,
            verified_stage="code-review",
            verified_candidate_head_sha=CANDIDATE,
        )
    )


def acquire(runtime, seeded, *, generation, claim_id, dispatch_id, workflow):
    return runtime.commit_replanned(
        lambda snapshot: plan_lineage_external_create_attempt(
            snapshot,
            operation_id=seeded["operation_id"],
            generation=generation,
            claim_id=claim_id,
            dispatch_id=dispatch_id,
            semantic_effect_key=seeded["semantic_effect_key"],
            external_dispatch_key_value=seeded["external_dispatch_key"],
            execution_binding=reviewer_binding(workflow),
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    ).result


class ProviderBDelegate:
    def __init__(self):
        self.post_count = 0
        self.lookup_bindings = []

    def execution_binding(self, *, dispatch):
        return reviewer_binding(COPILOT)

    def lookup_execution_binding(self, *, execution_binding, external_dispatch_key):
        self.lookup_bindings.append(dict(execution_binding))
        return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}

    def lookup(self, *, external_dispatch_key):
        return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}

    def launch(self, *, dispatch):
        self.post_count += 1
        return {"lookup_state": "LAUNCHED", "receipt_id": "unexpected-run"}


def validate_g0_provider_a_to_g1_provider_b():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = runtime_for(backend)
    seeded = seed_to_claim(runtime, FEATURE)
    g0_dispatch = "vertical-reviewer-g0"
    authorize(runtime, seeded, g0_dispatch)
    first = acquire(
        runtime,
        seeded,
        generation=0,
        claim_id=seeded["claim_id"],
        dispatch_id=g0_dispatch,
        workflow=CLAUDE,
    )
    require(first["acquired"] is True, "G0 provider A did not acquire the one global attempt")
    require(first["execution_binding"]["workflow_file"] == CLAUDE, "G0 attempt did not bind provider A")

    runtime.commit_replanned(
        lambda snapshot: plan_takeover(
            snapshot,
            operation_id=seeded["operation_id"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    generation = rebuild_projection(backend.read_snapshot(), seeded["operation_id"])["generation"]
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
    require(reservation["semantic_effect_key"] == seeded["semantic_effect_key"], "takeover changed semantic identity")
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
    g1_dispatch = "vertical-reviewer-g1"
    authorize(runtime, seeded, g1_dispatch, generation=generation, claim_id=claim["claim_id"])
    second = acquire(
        runtime,
        seeded,
        generation=generation,
        claim_id=claim["claim_id"],
        dispatch_id=g1_dispatch,
        workflow=COPILOT,
    )
    require(second["acquired"] is False, "G1 provider B minted a second create permission")
    require(second["execution_binding"]["workflow_file"] == CLAUDE, "G1 provider drift replaced winning provider A")

    delegate = ProviderBDelegate()
    gateway = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime,
        delegate=delegate,
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    result = gateway.launch(
        dispatch={
            "operation_id": seeded["operation_id"],
            "operation_generation": generation,
            "operation_profile": VERTICAL_PROFILE,
            "semantic_effect_key": seeded["semantic_effect_key"],
            "external_dispatch_key": seeded["external_dispatch_key"],
            "dispatch_id": g1_dispatch,
            "role": "reviewer",
        }
    )
    require(result["lookup_state"] == "UNKNOWN", "G1 provider drift did not remain lookup-only")
    require(delegate.post_count == 0, "G1 provider drift crossed the POST boundary")
    require(delegate.lookup_bindings and delegate.lookup_bindings[-1]["workflow_file"] == CLAUDE, "G1 lookup did not pin provider A")


def validate_cancel_before_authorization():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = runtime_for(backend)
    seeded = seed_to_claim(runtime, "F-CANCEL-BEFORE-AUTH-286")
    runtime.commit_replanned(
        lambda snapshot: plan_cancel(
            snapshot,
            operation_id=seeded["operation_id"],
            reason="cancel before launch authorization",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    try:
        plan_external_create_attempt(
            backend.read_snapshot(),
            operation_id=seeded["operation_id"],
            generation=seeded["generation"],
            claim_id=seeded["claim_id"],
            dispatch_id="never-authorized",
            semantic_effect_key=seeded["semantic_effect_key"],
            external_dispatch_key_value=seeded["external_dispatch_key"],
            execution_binding=reviewer_binding(CLAUDE),
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
        raise AssertionError("cancel-before-authorization unexpectedly acquired external-create authority")
    except StoreCommandError as exc:
        require(exc.code == "POLICY_DENIED", f"cancel-before-authorization failed with wrong code: {exc.code}")


def main():
    validate_g0_provider_a_to_g1_provider_b()
    validate_cancel_before_authorization()
    print("Operator external-create supplemental validation passed")
    print("- G0 provider A remains the only attempt authority across G1 provider B takeover")
    print("- cancel-before-authorization cannot reach external-create acquisition")


if __name__ == "__main__":
    main()
