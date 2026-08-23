#!/usr/bin/env python3
"""Trusted zero-effect preflight for one closed #310 scenario runtime slot."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_store_model import digest_json, normalize_repository
from operator_v03_reviewer_worker_readiness import ReviewerWorkerSelection, V03_REVIEWER_OPTIONS
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from v03_real_runtime_live_authority import TrustedMainExecution, V03LiveAuthority
from v03_scenario_fixture_pool import SlotSpec
from v03_scenario_runtime_composition import (
    V03ScenarioRuntimeComposition,
    build_v03_scenario_runtime_composition,
    slot_for_scenario,
)

DEVELOPER_WORKFLOW = "ai-sdlc-gh-aw-developer-codex.lock.yml"
QA_WORKFLOW = "ai-sdlc-gh-aw-qa-gemini.lock.yml"


class V03ScenarioRuntimePreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class V03ScenarioRuntimePreflight:
    scenario: str
    slot: SlotSpec
    execution: TrustedMainExecution
    live_authority: V03LiveAuthority
    reviewer_selection: ReviewerWorkerSelection
    workflows: GhAwVerticalWorkflowMap
    fixture_candidate: Any
    composition: V03ScenarioRuntimeComposition
    trusted_context_digest: str


def _reviewer_workflows() -> frozenset[str]:
    return frozenset(option.workflow_file for option in V03_REVIEWER_OPTIONS)


def build_v03_scenario_runtime_preflight(
    *,
    scenario: str,
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
) -> V03ScenarioRuntimePreflight:
    """Bind one frozen scenario to exact trusted-main runtime authority without launch."""
    slot = slot_for_scenario(scenario)
    if not isinstance(execution, TrustedMainExecution):
        raise ValueError("trusted-main execution is required")
    if not isinstance(live_authority, V03LiveAuthority):
        raise ValueError("exact live policy authority is required")
    if live_authority.execution != execution:
        raise V03ScenarioRuntimePreflightError(
            "live policy authority is not bound to this exact trusted-main execution"
        )
    if live_authority.policy.installation_commit_sha != execution.installation_commit_sha:
        raise V03ScenarioRuntimePreflightError(
            "policy authority installation differs from exact trusted main"
        )
    if not isinstance(reviewer_selection, ReviewerWorkerSelection) or not reviewer_selection.credential_present:
        raise V03ScenarioRuntimePreflightError("frozen Reviewer Worker readiness is not positive")
    if reviewer_selection.workflow_file not in _reviewer_workflows():
        raise V03ScenarioRuntimePreflightError(
            "Reviewer workflow is outside frozen v0.3 provider set"
        )
    if reviewer_selection.role != "reviewer" or reviewer_selection.stage != "code-review":
        raise V03ScenarioRuntimePreflightError("Reviewer readiness role/stage binding drifted")
    if not callable(getattr(protection_verifier, "verify", None)):
        raise V03ScenarioRuntimePreflightError(
            "exact trusted protection verifier is required for protected Store CAS"
        )
    if actions_token == event_write_token:
        raise V03ScenarioRuntimePreflightError(
            "Actions/read and canonical Feature Event write authority must remain split"
        )
    if not all((adapter_id, target_read_token, actions_token, event_write_token)):
        raise V03ScenarioRuntimePreflightError(
            "scenario runtime credentials/adapter identity are incomplete"
        )
    if not callable(clock):
        raise V03ScenarioRuntimePreflightError("trusted runtime clock is required")

    repository = normalize_repository(execution.repository)
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow=DEVELOPER_WORKFLOW,
        reviewer_workflow=reviewer_selection.workflow_file,
        qa_workflow=QA_WORKFLOW,
    )
    trusted_context_digest = digest_json(
        {
            "schema_version": "ai-sdlc.v03-scenario-runtime-preflight/v1",
            "scenario": slot.scenario,
            "repository": repository,
            "installation_commit_sha": execution.installation_commit_sha,
            "materialization_commit_sha": live_authority.materialization_commit_sha,
            "protected_state_ref_sha": live_authority.protected_state_ref_sha,
            "policy_bundle_digest": live_authority.policy.bundle_digest,
            "reviewer_worker_id": reviewer_selection.worker_id,
            "reviewer_selection_policy": reviewer_selection.selection_policy,
            "fixture_feature_id": slot.feature_id,
            "fixture_target_ref": slot.target_ref,
        }
    )
    config = TrustedOperatorRuntimeConfig(
        target_repository=repository,
        store_repository=repository,
        installation_ref="main",
        store_checkout=Path(store_checkout),
        principal="trusted-v03-release-controller",
        feature_bindings=(TrustedFeatureBinding(slot.feature_id, slot.target_ref),),
    )
    composition = build_v03_scenario_runtime_composition(
        scenario=slot.scenario,
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
    if (
        composition.scenario != slot.scenario
        or composition.slot != slot
        or composition.feature_id != slot.feature_id
        or composition.target_ref != slot.target_ref
    ):
        raise V03ScenarioRuntimePreflightError(
            "scenario composition identity escaped the closed #310 slot"
        )
    fixture_candidate = composition.candidate_provider.current_candidate(
        operation_id="v03-scenario-runtime-preflight:" + slot.scenario,
        repository=repository,
        feature_id=slot.feature_id,
        target_ref=slot.target_ref,
    )
    if (
        fixture_candidate.candidate_pr_number < 1
        or len(fixture_candidate.candidate_head_sha) != 40
    ):
        raise V03ScenarioRuntimePreflightError(
            "scenario fixture lacks exact PR/head authority"
        )

    return V03ScenarioRuntimePreflight(
        scenario=slot.scenario,
        slot=slot,
        execution=execution,
        live_authority=live_authority,
        reviewer_selection=reviewer_selection,
        workflows=workflows,
        fixture_candidate=fixture_candidate,
        composition=composition,
        trusted_context_digest=trusted_context_digest,
    )
