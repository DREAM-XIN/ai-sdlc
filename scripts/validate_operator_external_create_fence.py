#!/usr/bin/env python3
"""Adversarial validation for the v0.3 durable one-shot external-create fence."""
from __future__ import annotations

from dataclasses import replace

from operator_store import (
    StoreCommandError,
    plan_cancel,
    plan_operation_start,
    plan_takeover,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import CasConflict, MemoryStateRefBackend
from operator_store_model import StoreSnapshot, digest_json, projection_path, rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_external_create_attempt import (
    find_external_create_attempt,
    plan_external_create_attempt,
)
from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
from operator_effect_lineage_fences import (
    LINEAGE_WRITER_CAPABILITY,
    plan_lineage_authorize_launch,
    plan_lineage_dispatch_claim,
    plan_lineage_external_create_attempt,
)
from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_effect_rollout import (
    EffectLineageWriteFence,
    REQUIRED_FENCED_CAPABILITIES,
    VerifiedEffectLineageRollout,
)

REPO = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-15T14:20:00Z"
TRUST = "external-create-fence-validator"
FEATURE = "F-EXTERNAL-CREATE-FENCE-TEST"
HEAD = "a" * 40
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
        policy_ref="default-branch://operator/effect-lineage/v1",
        policy_digest="policy-digest",
        writer_capability=LINEAGE_WRITER_CAPABILITY,
        writer_fence_receipt_ref="default-branch://operator/writer-fence/v1",
        writer_fence_receipt_digest="writer-fence-digest",
    )


def runtime_from_snapshot(snapshot=None):
    backend = MemoryStateRefBackend(
        repository=REPO,
        state_ref=STATE_REF,
        snapshot=snapshot,
    )
    runtime = OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout()),
        clock=lambda: NOW,
    )
    return backend, runtime


def create_authorized_runtime():
    backend, runtime = runtime_from_snapshot()
    started = runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=7,
            idempotency_key="external-create-fence",
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
            generation=0,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=7,
            current_stage="code-review",
            task_identity="vertical:code-review:" + HEAD,
            role="reviewer",
            candidate_head_sha=HEAD,
            current_target_ref="feature/external-create-fence",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            logical_work_slot="CODE_REVIEW",
            task_id="code-review",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            trusted_profile_digest="trusted-profile-digest",
        )
    ).result
    effect_key = reservation["semantic_effect_key"]
    external_key = reservation["external_dispatch_key"]
    lineage_id = reservation["effect_lineage_id"]
    claim = runtime.commit_replanned(
        lambda snapshot: plan_lineage_dispatch_claim(
            snapshot,
            effect_lineage_id=lineage_id,
            operation_id=operation_id,
            generation=0,
            effect_key=effect_key,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    ).result
    dispatch_id = "vertical-" + digest_json(
        {"operation_id": operation_id, "generation": 0, "external_dispatch_key": external_key}
    )[:32]
    runtime.commit_replanned(
        lambda snapshot: plan_lineage_authorize_launch(
            snapshot,
            effect_lineage_id=lineage_id,
            operation_id=operation_id,
            generation=0,
            claim_id=claim["claim_id"],
            dispatch_id=dispatch_id,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=7,
            verified_stage="code-review",
            verified_candidate_head_sha=HEAD,
        )
    )
    return backend, runtime, {
        "operation_id": operation_id,
        "generation": 0,
        "lineage_id": lineage_id,
        "effect_key": effect_key,
        "external_key": external_key,
        "claim_id": claim["claim_id"],
        "dispatch_id": dispatch_id,
    }


def binding(workflow=CLAUDE):
    if workflow == CLAUDE:
        return {
            "worker_id": "code-review-reviewer-claude",
            "role": "reviewer",
            "profile": "claude",
            "workflow_file": CLAUDE,
            "selection_policy": "v03-frozen-reviewer-provider-order/v1",
            "default_branch": "main",
            "credential_name": "ANTHROPIC_API_KEY",
        }
    return {
        "worker_id": "code-review-reviewer-copilot",
        "role": "reviewer",
        "profile": "copilot",
        "workflow_file": COPILOT,
        "selection_policy": "v03-frozen-reviewer-provider-order/v1",
        "default_branch": "main",
        "credential_name": "COPILOT_GITHUB_TOKEN",
    }


def acquire_plan(snapshot, ids, *, generation=None, claim_id=None, dispatch_id=None, selected=None):
    return plan_lineage_external_create_attempt(
        snapshot,
        operation_id=ids["operation_id"],
        generation=ids["generation"] if generation is None else generation,
        claim_id=ids["claim_id"] if claim_id is None else claim_id,
        dispatch_id=ids["dispatch_id"] if dispatch_id is None else dispatch_id,
        semantic_effect_key=ids["effect_key"],
        external_dispatch_key_value=ids["external_key"],
        execution_binding=selected or binding(),
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )


def validate_capability_inventory_and_raw_fence():
    require(
        "raw-external-create-attempt" in REQUIRED_FENCED_CAPABILITIES,
        "writer-fence capability inventory omitted the POST-enabling attempt authority",
    )
    backend, _runtime, ids = create_authorized_runtime()
    snapshot = backend.read_snapshot()
    raw = plan_external_create_attempt(
        snapshot,
        operation_id=ids["operation_id"],
        generation=0,
        claim_id=ids["claim_id"],
        dispatch_id=ids["dispatch_id"],
        semantic_effect_key=ids["effect_key"],
        external_dispatch_key_value=ids["external_key"],
        execution_binding=binding(),
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )
    try:
        EffectLineageWriteFence(rollout())(snapshot, raw)
        raise AssertionError("raw/unmarked external-create attempt bypassed the production writer fence")
    except StoreCommandError as exc:
        require(exc.code == "MIXED_WRITER_FENCED", "raw attempt failed with the wrong safety code")
    marked = acquire_plan(snapshot, ids)
    EffectLineageWriteFence(rollout())(snapshot, marked)
    require(marked.result["acquired"] is True, "lineage-aware first attempt did not acquire")


def validate_same_snapshot_cas_election():
    backend, runtime, ids = create_authorized_runtime()
    pre = backend.read_snapshot()
    plan_a = acquire_plan(pre, ids)
    plan_b = acquire_plan(pre, ids)
    receipt = runtime.protected_receipt()
    first = backend.commit(plan_a, receipt)
    require(first.result["acquired"] is True, "first CAS contender did not acquire")
    try:
        backend.commit(plan_b, receipt)
        raise AssertionError("second stale CAS plan unexpectedly committed")
    except CasConflict:
        pass
    second = runtime.commit_replanned(lambda snapshot: acquire_plan(snapshot, ids))
    require(second.result["acquired"] is False, "CAS loser replan minted a second create permission")
    require(
        second.result["attempt_id"] == first.result["attempt_id"],
        "CAS loser did not reuse the one global attempt identity",
    )


def add_generation_one_authorization(runtime, ids):
    runtime.commit_replanned(
        lambda snapshot: plan_takeover(
            snapshot,
            operation_id=ids["operation_id"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    reservation = runtime.commit_replanned(
        lambda snapshot: plan_lineage_gated_reservation(
            snapshot,
            operation_id=ids["operation_id"],
            generation=1,
            target_repository=REPO,
            feature_id=FEATURE,
            expected_revision=7,
            current_stage="code-review",
            task_identity="vertical:code-review:" + HEAD,
            role="reviewer",
            candidate_head_sha=HEAD,
            current_target_ref="feature/external-create-fence",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            logical_work_slot="CODE_REVIEW",
            task_id="code-review",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            trusted_profile_digest="trusted-profile-digest",
        )
    ).result
    require(reservation["semantic_effect_key"] == ids["effect_key"], "takeover changed semantic effect identity")
    claim = runtime.commit_replanned(
        lambda snapshot: plan_lineage_dispatch_claim(
            snapshot,
            effect_lineage_id=ids["lineage_id"],
            operation_id=ids["operation_id"],
            generation=1,
            effect_key=ids["effect_key"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    ).result
    dispatch_id = "vertical-" + digest_json(
        {"operation_id": ids["operation_id"], "generation": 1, "external_dispatch_key": ids["external_key"]}
    )[:32]
    runtime.commit_replanned(
        lambda snapshot: plan_lineage_authorize_launch(
            snapshot,
            effect_lineage_id=ids["lineage_id"],
            operation_id=ids["operation_id"],
            generation=1,
            claim_id=claim["claim_id"],
            dispatch_id=dispatch_id,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=7,
            verified_stage="code-review",
            verified_candidate_head_sha=HEAD,
        )
    )
    return claim["claim_id"], dispatch_id


def validate_generation_independent_takeover_and_provider_binding():
    backend, runtime, ids = create_authorized_runtime()
    g1_claim, g1_dispatch = add_generation_one_authorization(runtime, ids)
    acquired = runtime.commit_replanned(lambda snapshot: acquire_plan(snapshot, ids, selected=binding(CLAUDE)))
    require(acquired.result["acquired"] is True, "G0 failed to acquire the global attempt")
    loser = runtime.commit_replanned(
        lambda snapshot: acquire_plan(
            snapshot,
            ids,
            generation=1,
            claim_id=g1_claim,
            dispatch_id=g1_dispatch,
            selected=binding(COPILOT),
        )
    )
    require(loser.result["acquired"] is False, "G1 takeover minted a second create permission")
    require(
        loser.result["execution_binding"]["workflow_file"] == CLAUDE,
        "provider drift replaced the winning attempt workflow",
    )
    attempt = find_external_create_attempt(backend.read_snapshot(), external_dispatch_key=ids["external_key"])
    require(attempt["created_generation"] == 0, "takeover rewrote attempt creator generation")
    require(attempt["execution_binding"]["profile"] == "claude", "attempt did not preserve winning provider")


def validate_cancel_ordering():
    backend, runtime, ids = create_authorized_runtime()
    runtime.commit_replanned(
        lambda snapshot: plan_cancel(
            snapshot,
            operation_id=ids["operation_id"],
            reason="cancel after durable launch authorization",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    after = runtime.commit_replanned(lambda snapshot: acquire_plan(snapshot, ids))
    require(after.result["acquired"] is True, "post-authorization cancellation revoked the linearized one-shot attempt")

    backend2, runtime2 = runtime_from_snapshot()
    started = runtime2.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id="F-CANCEL-BEFORE-AUTH",
            expected_revision=1,
            idempotency_key="cancel-before-auth",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            operation_profile=VERTICAL_PROFILE,
        )
    ).result
    runtime2.commit_replanned(
        lambda snapshot: plan_cancel(
            snapshot,
            operation_id=started["operation_id"],
            reason="cancel before launch authorization",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
    )
    try:
        plan_external_create_attempt(
            backend2.read_snapshot(),
            operation_id=started["operation_id"],
            generation=0,
            claim_id="forged-claim",
            dispatch_id="forged-dispatch",
            semantic_effect_key="0" * 64,
            external_dispatch_key_value="dispatch-forged",
            execution_binding=binding(),
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
        raise AssertionError("cancel-before-authorization reached one-shot create authority")
    except StoreCommandError:
        pass


class FakeTransport:
    def __init__(self):
        self.dispatch_calls = 0
        self.accepted = False
        self.visible = False
        self.lookup_workflows = []
        self.raise_after_accept = False

    def dispatch(self, *, workflow, ref, inputs):
        self.dispatch_calls += 1
        self.accepted = True
        if self.raise_after_accept:
            raise RuntimeError("simulated accepted POST with lost acknowledgement")
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}

    def lookup(self, *, workflow, ref, dispatch_key):
        self.lookup_workflows.append(workflow)
        if self.visible and self.accepted and workflow == CLAUDE:
            return {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}
        return None


def delegate(transport, reviewer_workflow):
    return GhAwVerticalRoleDispatchGateway(
        transport=transport,
        workflows=GhAwVerticalWorkflowMap(
            default_branch="main",
            developer_workflow="ai-sdlc-gh-aw-worker-codex.lock.yml",
            reviewer_workflow=reviewer_workflow,
            qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
        ),
    )


def dispatch(ids, *, generation=0, dispatch_id=None):
    return {
        "operation_id": ids["operation_id"],
        "operation_generation": generation,
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": ids["effect_key"],
        "external_dispatch_key": ids["external_key"],
        "dispatch_id": dispatch_id or ids["dispatch_id"],
        "target_repository": REPO,
        "target_ref": "feature/external-create-fence",
        "feature_id": FEATURE,
        "expected_revision": 7,
        "feature_stage": "code-review",
        "task_id": "code-review",
        "task_identity": "vertical:code-review:" + HEAD,
        "role": "reviewer",
        "candidate_pr_number": 286,
        "candidate_head_sha": HEAD,
    }


def validate_lost_ack_fresh_process_and_projection_rebuild():
    backend, runtime, ids = create_authorized_runtime()
    transport = FakeTransport()
    transport.raise_after_accept = True
    first_gateway = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime,
        delegate=delegate(transport, CLAUDE),
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    try:
        first_gateway.launch(dispatch=dispatch(ids))
        raise AssertionError("lost-ACK delegate unexpectedly returned normally")
    except RuntimeError:
        pass
    require(transport.dispatch_calls == 1, "first elected caller did not cross POST exactly once")
    attempt = find_external_create_attempt(backend.read_snapshot(), external_dispatch_key=ids["external_key"])
    require(attempt is not None, "POST attempt was not durable before transport call")

    fresh_provider_b = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime,
        delegate=delegate(transport, COPILOT),
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    hidden = fresh_provider_b.launch(dispatch=dispatch(ids))
    require(hidden["lookup_state"] == "UNKNOWN", "durable attempt plus invisible run re-armed NOT_LAUNCHED")
    require(transport.dispatch_calls == 1, "fresh process issued a second POST after lost ACK")
    require(CLAUDE in transport.lookup_workflows, "fresh process did not use recorded winning workflow for lookup")

    snapshot = backend.read_snapshot()
    files = dict(snapshot.files)
    files.pop(projection_path(ids["operation_id"]), None)
    rebuilt_backend, rebuilt_runtime = runtime_from_snapshot(StoreSnapshot(ref_sha=snapshot.ref_sha, files=files))
    rebuilt_gateway = StoreBackedOneShotExternalCreateGateway(
        runtime=rebuilt_runtime,
        delegate=delegate(transport, COPILOT),
        trusted_context_digest=TRUST,
        effect_lineage_required=True,
    )
    require(rebuild_projection(rebuilt_backend.read_snapshot(), ids["operation_id"])["operation_id"] == ids["operation_id"], "projection cache deletion prevented deterministic rebuild")
    hidden_again = rebuilt_gateway.launch(dispatch=dispatch(ids))
    require(hidden_again["lookup_state"] == "UNKNOWN", "projection rebuild re-armed external create")
    require(transport.dispatch_calls == 1, "projection rebuild issued a second POST")

    transport.visible = True
    adopted = rebuilt_gateway.lookup(external_dispatch_key=ids["external_key"])
    require(adopted == {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}, "later visible original run was not adopted")
    require(transport.dispatch_calls == 1, "adoption created another POST")


def validate_forged_authority_fails_closed():
    backend, _runtime, ids = create_authorized_runtime()
    snapshot = backend.read_snapshot()
    try:
        plan_external_create_attempt(
            snapshot,
            operation_id=ids["operation_id"],
            generation=0,
            claim_id=ids["claim_id"],
            dispatch_id="forged-dispatch-id",
            semantic_effect_key=ids["effect_key"],
            external_dispatch_key_value=ids["external_key"],
            execution_binding=binding(),
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        )
        raise AssertionError("forged launch authorization acquired external-create permission")
    except StoreCommandError as exc:
        require(exc.code == "POLICY_DENIED", "forged authority failed with wrong code")


def main():
    validate_capability_inventory_and_raw_fence()
    validate_same_snapshot_cas_election()
    validate_generation_independent_takeover_and_provider_binding()
    validate_cancel_ordering()
    validate_lost_ack_fresh_process_and_projection_rebuild()
    validate_forged_authority_fails_closed()
    print("Operator external-create one-shot fence validation passed")


if __name__ == "__main__":
    main()
