#!/usr/bin/env python3
"""Trusted pre-launch assembly for one v0.3 real release dogfood scenario."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_store_model import digest_json, normalize_repository
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from gh_aw_role_workers import resolve_role_worker
from v03_dogfood_fixture_pool import DogfoodSlot
from v03_dogfood_full_composition import V03DogfoodFullComposition, build_v03_dogfood_full_composition
from v03_dogfood_live_gate import DogfoodLiveGate, CURRENT_DOGFOOD_POLICY, CURRENT_DOGFOOD_WORKFLOWS
from v03_real_runtime_live_authority import TrustedMainExecution, V03LiveAuthority

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


class V03DogfoodRuntimePreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class V03DogfoodRuntimePreflight:
    execution: TrustedMainExecution
    live_authority: V03LiveAuthority
    live_gate: DogfoodLiveGate
    slot: DogfoodSlot
    workflows: GhAwVerticalWorkflowMap
    candidate_pr_number: int
    candidate_head_sha: str
    composition: V03DogfoodFullComposition
    trusted_context_digest: str


def _workflow_map(gate: DogfoodLiveGate) -> GhAwVerticalWorkflowMap:
    by_role = {row.role: row for row in gate.bindings}
    if set(by_role) != {"developer", "reviewer", "qa"}:
        raise V03DogfoodRuntimePreflightError("dogfood production role binding set is incomplete")
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow=by_role["developer"].worker_workflow,
        reviewer_workflow=by_role["reviewer"].worker_workflow,
        qa_workflow=by_role["qa"].worker_workflow,
    )
    configured = (workflows.developer_workflow, workflows.reviewer_workflow, workflows.qa_workflow)
    if len(set(configured)) != 3:
        raise V03DogfoodRuntimePreflightError("dogfood production role workflows must remain distinct")
    for workflow in configured:
        path = WORKFLOW_DIR / workflow
        if not path.is_file() or path.is_symlink():
            raise V03DogfoodRuntimePreflightError(f"configured dogfood Worker is not installed: {workflow}")
    return workflows


def _execution_bindings(gate: DogfoodLiveGate, workflows: GhAwVerticalWorkflowMap) -> dict[str, dict[str, str]]:
    """Project the already-resolved production routes into dogfood-only one-shot identities."""
    by_role = {row.role: row for row in gate.bindings}
    if set(by_role) != {"developer", "reviewer", "qa"}:
        raise V03DogfoodRuntimePreflightError("dogfood execution binding set is incomplete")
    result: dict[str, dict[str, str]] = {}
    for role in ("developer", "reviewer", "qa"):
        row = by_role[role]
        workflow = workflows.workflow_for(role)
        if row.worker_workflow != workflow:
            raise V03DogfoodRuntimePreflightError(f"{role} workflow differs from resolved production binding")
        if (row.rule_id != CURRENT_DOGFOOD_POLICY or row.selected_profile != "deepseek"
                or row.candidate_order != ("deepseek",) or row.fallback
                or row.worker_workflow != CURRENT_DOGFOOD_WORKFLOWS[role]
                or row.accepted_credential_identities != ("DEEPSEEK_API_KEY",)
                or row.present_credential_identities != ("DEEPSEEK_API_KEY",)):
            raise V03DogfoodRuntimePreflightError("current dogfood execution selection drifted")
        binding = {
            "worker_id": workflow.removesuffix(".lock.yml"),
            "role": role, "profile": "deepseek", "workflow_file": workflow,
            "selection_policy_id": CURRENT_DOGFOOD_POLICY,
            "default_branch": workflows.default_branch,
            "credential_name": "DEEPSEEK_API_KEY",
        }
        result[role] = binding
    if len({row["workflow_file"] for row in result.values()}) != 3:
        raise V03DogfoodRuntimePreflightError("dogfood execution workflows are not distinct")
    return result


def build_v03_dogfood_runtime_preflight(
    *,
    execution: TrustedMainExecution,
    live_authority: V03LiveAuthority,
    live_gate: DogfoodLiveGate,
    slot: DogfoodSlot,
    protection_verifier: Any,
    adapter_id: str,
    target_read_token: str,
    actions_token: str,
    event_write_token: str,
    clock: Callable[[], Any],
    store_checkout: Path = Path("."),
    github_api_base: str = "https://api.github.com",
) -> V03DogfoodRuntimePreflight:
    if not isinstance(execution, TrustedMainExecution) or not isinstance(live_authority, V03LiveAuthority):
        raise ValueError("dogfood preflight requires exact trusted-main/live authority")
    if live_authority.execution != execution:
        raise V03DogfoodRuntimePreflightError("dogfood live authority belongs to another execution")
    if live_authority.policy.installation_commit_sha != execution.installation_commit_sha:
        raise V03DogfoodRuntimePreflightError("dogfood policy installation differs from trusted main")
    if live_gate.installation_commit_sha != execution.installation_commit_sha:
        raise V03DogfoodRuntimePreflightError("dogfood #221/binding gate belongs to another main generation")
    if live_gate.scenario != slot.scenario:
        raise V03DogfoodRuntimePreflightError("dogfood live gate scenario differs from fixed fixture slot")
    if not callable(getattr(protection_verifier, "verify", None)):
        raise V03DogfoodRuntimePreflightError("dogfood preflight requires exact protection verifier")
    if actions_token == event_write_token:
        raise V03DogfoodRuntimePreflightError("Actions/read and Feature Event write authority must remain split")
    if not all((adapter_id, target_read_token, actions_token, event_write_token)) or not callable(clock):
        raise V03DogfoodRuntimePreflightError("dogfood runtime credentials/adapter/clock are incomplete")

    repository = normalize_repository(execution.repository)
    workflows = _workflow_map(live_gate)
    execution_bindings = _execution_bindings(live_gate, workflows)
    trusted_context_digest = digest_json({
        "schema_version": "ai-sdlc.v03-dogfood-runtime-preflight/v1",
        "repository": repository,
        "installation_commit_sha": execution.installation_commit_sha,
        "materialization_commit_sha": live_authority.materialization_commit_sha,
        "protected_state_ref_sha": live_authority.protected_state_ref_sha,
        "policy_bundle_digest": live_authority.policy.bundle_digest,
        "issue_221_ledger_digest": live_gate.issue221.ledger_digest,
        "issue_221_evidence_head_sha": live_gate.issue221.evidence_head_sha,
        "issue_221_compatibility_digest": live_gate.issue221.compatibility_digest,
        "issue_221_workflow_run_ids": list(live_gate.issue221.workflow_run_ids),
        "scenario": slot.scenario,
        "feature_id": slot.feature_id,
        "target_ref": slot.target_ref,
        "adapter_id": adapter_id,
        "workflows": {
            "developer": workflows.developer_workflow,
            "reviewer": workflows.reviewer_workflow,
            "qa": workflows.qa_workflow,
        },
        "execution_bindings": execution_bindings,
    })
    config = TrustedOperatorRuntimeConfig(
        target_repository=repository,
        store_repository=repository,
        installation_ref="main",
        store_checkout=Path(store_checkout),
        principal="trusted-v03-release-dogfood-controller",
        feature_bindings=(TrustedFeatureBinding(slot.feature_id, slot.target_ref),),
    )
    composition = build_v03_dogfood_full_composition(
        slot=slot,
        config=config,
        adapter_id=adapter_id,
        target_read_token=target_read_token,
        actions_token=actions_token,
        event_write_token=event_write_token,
        control_repository=repository,
        workflows=workflows,
        execution_bindings=execution_bindings,
        protection_verifier=protection_verifier,
        policy_authority=live_authority.policy,
        trusted_context_digest=trusted_context_digest,
        collector_namespace_policy="v03-dogfood-first-attempt:" + live_authority.policy.bundle_digest,
        trusted_role_policy="v03-dogfood-production-bindings:" + digest_json({row.role: row.worker_workflow for row in live_gate.bindings}),
        clock=clock,
        github_api_base=github_api_base,
    )
    if slot.scenario == "happy_path":
        from v03_dogfood_full_composition import POST_HANDOFF_PATH, REVIEWER_PREDECESSOR_SOURCE, validate_reviewer_controller_bridge, recovery_execution_binding
        snapshot = composition.runtime.backend.read_snapshot()
        old = snapshot.get(POST_HANDOFF_PATH)
        if (isinstance(old, dict) and old.get("consumer_execution_binding", {}).get("execution_source_head_sha") == REVIEWER_PREDECESSOR_SOURCE
                and execution.installation_commit_sha != REVIEWER_PREDECESSOR_SOURCE):
            validate_reviewer_controller_bridge(snapshot, recovery_execution_binding(composition.policy_authority), inspection_only=True)
    candidate = composition.candidate_provider.current_candidate(
        operation_id="v03-dogfood-preflight",
        repository=repository,
        feature_id=slot.feature_id,
        target_ref=slot.target_ref,
    )
    if candidate.candidate_pr_number < 1 or len(candidate.candidate_head_sha) != 40:
        raise V03DogfoodRuntimePreflightError("dogfood fixed fixture lacks exact PR/head authority")
    return V03DogfoodRuntimePreflight(
        execution=execution,
        live_authority=live_authority,
        live_gate=live_gate,
        slot=slot,
        workflows=workflows,
        candidate_pr_number=candidate.candidate_pr_number,
        candidate_head_sha=candidate.candidate_head_sha,
        composition=composition,
        trusted_context_digest=trusted_context_digest,
    )
