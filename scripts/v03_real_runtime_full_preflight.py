#!/usr/bin/env python3
"""Trusted pre-launch assembly for the v0.3 Issue #221 real-runtime driver."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_store_model import digest_json, normalize_repository
from operator_v03_reviewer_worker_readiness import ReviewerWorkerSelection, V03_REVIEWER_OPTIONS
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from provision_v03_real_runtime_fixture import FEATURE_ID as FIXTURE_FEATURE_ID, TARGET_REF as FIXTURE_TARGET_REF
from v03_real_runtime_full_composition import build_v03_real_runtime_full_composition
from v03_real_runtime_live_authority import TrustedMainExecution, V03LiveAuthority

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
# Developer uses the reviewed generic Codex Registry Worker. Reviewer/QA use the
# specialized Gate workers because their result contracts are Gate-specific.
DEVELOPER_WORKFLOW = "ai-sdlc-gh-aw-worker-codex.lock.yml"
QA_WORKFLOW = "ai-sdlc-gh-aw-qa-gemini.lock.yml"


class V03FullRuntimePreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class V03FullRuntimePreflight:
    execution: TrustedMainExecution
    live_authority: V03LiveAuthority
    reviewer_selection: ReviewerWorkerSelection
    workflows: GhAwVerticalWorkflowMap
    fixture_candidate: Any
    composition: Any
    trusted_context_digest: str


def _reviewer_workflows() -> frozenset[str]:
    return frozenset(option.workflow_file for option in V03_REVIEWER_OPTIONS)


def _require_installed_workflows(workflows: GhAwVerticalWorkflowMap) -> None:
    """Fail before any live authority if a configured production Worker is absent.

    The Actions transport dispatches these filenames directly through GitHub's
    workflow_dispatch REST endpoint. There is no alias or dynamic generation
    layer at runtime, so accepting a filename that is not installed would defer
    a deterministic configuration error until after Store reservation/launch
    authorization. Preflight must catch that mismatch first.
    """
    configured = (
        workflows.developer_workflow,
        workflows.reviewer_workflow,
        workflows.qa_workflow,
    )
    if len(set(configured)) != 3:
        raise V03FullRuntimePreflightError("production role workflows must remain distinct")
    for workflow in configured:
        path = WORKFLOW_DIR / workflow
        if not path.is_file() or path.is_symlink():
            raise V03FullRuntimePreflightError(
                f"configured production Worker is not installed as a regular workflow: {workflow}"
            )


def build_v03_full_runtime_preflight(
    *,
    execution: TrustedMainExecution,
    live_authority: V03LiveAuthority,
    reviewer_selection: ReviewerWorkerSelection,
    protection_verifier: Any,
    adapter_id: str,
    target_read_token: str,
    actions_token: str,
    event_write_token: str,
    clock: Callable[[], Any],
    store_checkout: Path = Path("."),
    github_api_base: str = "https://api.github.com",
) -> V03FullRuntimePreflight:
    """Bind all reviewed live prerequisites without launching a Worker."""
    if not isinstance(execution, TrustedMainExecution):
        raise ValueError("trusted-main execution is required")
    if not isinstance(live_authority, V03LiveAuthority):
        raise ValueError("exact live policy authority is required")
    if live_authority.execution != execution:
        raise V03FullRuntimePreflightError("live policy authority is not bound to this exact trusted-main execution")
    if live_authority.policy.installation_commit_sha != execution.installation_commit_sha:
        raise V03FullRuntimePreflightError("policy authority installation differs from exact trusted main")
    if not isinstance(reviewer_selection, ReviewerWorkerSelection) or not reviewer_selection.credential_present:
        raise V03FullRuntimePreflightError("frozen Reviewer Worker readiness is not positive")
    if reviewer_selection.workflow_file not in _reviewer_workflows():
        raise V03FullRuntimePreflightError("Reviewer workflow is outside frozen v0.3 provider set")
    if reviewer_selection.role != "reviewer" or reviewer_selection.stage != "code-review":
        raise V03FullRuntimePreflightError("Reviewer readiness role/stage binding drifted")
    if not callable(getattr(protection_verifier, "verify", None)):
        raise V03FullRuntimePreflightError("exact trusted protection verifier is required for protected Store CAS")
    if actions_token == event_write_token:
        raise V03FullRuntimePreflightError("Actions/read and canonical Feature Event write authority must remain split")
    if not all((adapter_id, target_read_token, actions_token, event_write_token)):
        raise V03FullRuntimePreflightError("full-runtime credentials/adapter identity are incomplete")
    if not callable(clock):
        raise V03FullRuntimePreflightError("trusted runtime clock is required")

    repository = normalize_repository(execution.repository)
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow=DEVELOPER_WORKFLOW,
        reviewer_workflow=reviewer_selection.workflow_file,
        qa_workflow=QA_WORKFLOW,
    )
    _require_installed_workflows(workflows)
    trusted_context_digest = digest_json(
        {
            "schema_version": "ai-sdlc.v03-real-runtime-preflight/v1",
            "repository": repository,
            "installation_commit_sha": execution.installation_commit_sha,
            "materialization_commit_sha": live_authority.materialization_commit_sha,
            "protected_state_ref_sha": live_authority.protected_state_ref_sha,
            "policy_bundle_digest": live_authority.policy.bundle_digest,
            "developer_workflow": workflows.developer_workflow,
            "reviewer_worker_id": reviewer_selection.worker_id,
            "reviewer_workflow": workflows.reviewer_workflow,
            "reviewer_selection_policy": reviewer_selection.selection_policy,
            "qa_workflow": workflows.qa_workflow,
            "fixture_feature_id": FIXTURE_FEATURE_ID,
            "fixture_target_ref": FIXTURE_TARGET_REF,
        }
    )
    config = TrustedOperatorRuntimeConfig(
        target_repository=repository,
        store_repository=repository,
        installation_ref="main",
        store_checkout=Path(store_checkout),
        principal="trusted-v03-release-controller",
        feature_bindings=(TrustedFeatureBinding(FIXTURE_FEATURE_ID, FIXTURE_TARGET_REF),),
    )
    composition = build_v03_real_runtime_full_composition(
        config=config,
        adapter_id=adapter_id,
        target_read_token=target_read_token,
        actions_token=actions_token,
        event_write_token=event_write_token,
        control_repository=repository,
        workflows=workflows,
        protection_verifier=protection_verifier,
        policy_authority=live_authority.policy,
        trusted_context_digest=trusted_context_digest,
        collector_namespace_policy="v03-first-attempt:" + live_authority.policy.bundle_digest,
        trusted_role_policy=(
            reviewer_selection.selection_policy + ":" + reviewer_selection.worker_id
        ),
        clock=clock,
        github_api_base=github_api_base,
    )
    fixture_candidate = composition.candidate_provider.current_candidate(
        operation_id="v03-real-runtime-preflight",
        repository=repository,
        feature_id=FIXTURE_FEATURE_ID,
        target_ref=FIXTURE_TARGET_REF,
    )
    if fixture_candidate.candidate_pr_number < 1 or len(fixture_candidate.candidate_head_sha) != 40:
        raise V03FullRuntimePreflightError("fixed fixture lacks exact PR/head authority")

    return V03FullRuntimePreflight(
        execution=execution,
        live_authority=live_authority,
        reviewer_selection=reviewer_selection,
        workflows=workflows,
        fixture_candidate=fixture_candidate,
        composition=composition,
        trusted_context_digest=trusted_context_digest,
    )
