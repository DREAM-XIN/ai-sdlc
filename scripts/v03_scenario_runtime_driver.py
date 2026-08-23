#!/usr/bin/env python3
"""Trusted-main preflight driver for one frozen #310 scenario slot.

The driver exposes only the closed scenario id inventory.  It never accepts a
Feature id or target ref from the caller; those identities are derived by the
#312 preflight/composition layer.  ``preflight-only`` performs no operation.start
or Worker dispatch and is therefore safe to reuse as the authority bootstrap for
later scenario-specific trusted-main runners.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping

from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_v03_reviewer_worker_readiness import public_selection, selection_from_environment
from v03_real_runtime_live_authority import load_live_authority, require_trusted_main_execution
from v03_scenario_fixture_pool import EXPECTED_SCENARIOS
from v03_scenario_runtime_preflight import build_v03_scenario_runtime_preflight

VALIDATE_ONLY = "validate-only"
PREFLIGHT_ONLY = "preflight-only"
ALLOWED_MODES = frozenset({VALIDATE_ONLY, PREFLIGHT_ONLY})
ADAPTER_ID = "v03-scenario-release-verifier"


class V03ScenarioRuntimeDriverError(RuntimeError):
    pass


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise V03ScenarioRuntimeDriverError(f"missing trusted scenario driver configuration: {name}")
    return value


def _head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise V03ScenarioRuntimeDriverError("cannot resolve exact checkout HEAD")
    return completed.stdout.strip()


def _clock() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_scenario_driver_mode(*, mode: str, scenario: str, event_name: str, ref: str) -> tuple[str, str]:
    """Pure mode/scenario gate; no GitHub, Store, policy, or Worker effects."""
    if mode not in ALLOWED_MODES:
        raise V03ScenarioRuntimeDriverError("unsupported v0.3 scenario-driver mode")
    if scenario not in EXPECTED_SCENARIOS:
        raise V03ScenarioRuntimeDriverError("scenario is outside the closed #310 inventory")
    if mode == VALIDATE_ONLY:
        if event_name == "workflow_dispatch":
            raise V03ScenarioRuntimeDriverError("workflow_dispatch may not masquerade as validate-only")
        return mode, scenario
    if event_name != "workflow_dispatch" or ref != "refs/heads/main":
        raise V03ScenarioRuntimeDriverError(
            "scenario preflight-only is authorized only by workflow_dispatch on main"
        )
    return mode, scenario


def assemble_scenario_live_preflight(
    *,
    scenario: str,
    env: Mapping[str, str],
    checkout_sha: str,
    live_loader: Callable[..., Any] = load_live_authority,
    reviewer_selector: Callable[..., Any] = selection_from_environment,
    preflight_builder: Callable[..., Any] = build_v03_scenario_runtime_preflight,
    protection_verifier_factory: Callable[..., Any] = GitHubRepositoryProtectionVerifier,
    clock: Callable[[], Any] = _clock,
):
    """Assemble one exact scenario authority graph without operation.start/dispatch."""
    require_scenario_driver_mode(
        mode=PREFLIGHT_ONLY,
        scenario=scenario,
        event_name=_required(env, "GITHUB_EVENT_NAME"),
        ref=_required(env, "GITHUB_REF"),
    )
    execution = require_trusted_main_execution(
        event_name=env["GITHUB_EVENT_NAME"],
        ref=env["GITHUB_REF"],
        repository=_required(env, "GITHUB_REPOSITORY"),
        workflow_sha=_required(env, "GITHUB_SHA"),
        checkout_sha=checkout_sha,
    )
    admin_token = _required(env, "AI_SDLC_OPERATOR_ADMIN_TOKEN")
    app_slug = _required(env, "AI_SDLC_OPERATOR_APP_SLUG")
    app_id_raw = _required(env, "AI_SDLC_OPERATOR_APP_INTEGRATION_ID")
    if not app_id_raw.isdigit() or int(app_id_raw) < 1:
        raise V03ScenarioRuntimeDriverError(
            "AI_SDLC_OPERATOR_APP_INTEGRATION_ID must be a positive integer"
        )
    api_base = _required(env, "GITHUB_API_URL")
    live = live_loader(
        execution=execution,
        admin_token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=int(app_id_raw),
        api_base=api_base,
    )
    selection = reviewer_selector(
        registry_path=Path("runtimes/gh-aw/role-workers.yaml"),
        workflow_dir=Path(".github/workflows"),
    )
    protection = protection_verifier_factory(
        token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=int(app_id_raw),
        api_base=api_base,
    )
    store_checkout = Path(str(env.get("AI_SDLC_STORE_CHECKOUT") or ".")).resolve()
    return preflight_builder(
        scenario=scenario,
        execution=execution,
        live_authority=live,
        reviewer_selection=selection,
        protection_verifier=protection,
        adapter_id=ADAPTER_ID,
        target_read_token=_required(env, "AI_SDLC_ACTIONS_READ_TOKEN"),
        actions_token=_required(env, "AI_SDLC_ACTIONS_READ_TOKEN"),
        event_write_token=_required(env, "AI_SDLC_EVENT_WRITE_TOKEN"),
        clock=clock,
        store_checkout=store_checkout,
        github_api_base=api_base,
    )


def public_scenario_preflight(preflight: Any) -> dict[str, Any]:
    """Return redacted authority metadata only; never credential values."""
    return {
        "schema_version": "ai-sdlc.v03-scenario-runtime-driver-preflight/v1",
        "mode": PREFLIGHT_ONLY,
        "scenario": preflight.scenario,
        "repository": preflight.execution.repository,
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
        "protected_state_ref_sha": preflight.live_authority.protected_state_ref_sha,
        "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
        "reviewer": public_selection(preflight.reviewer_selection),
        "fixture": {
            "feature_id": preflight.slot.feature_id,
            "target_ref": preflight.slot.target_ref,
            "candidate_pr_number": preflight.fixture_candidate.candidate_pr_number,
            "candidate_head_sha": preflight.fixture_candidate.candidate_head_sha,
        },
        "workflows": {
            "developer": preflight.workflows.developer_workflow,
            "reviewer": preflight.workflows.reviewer_workflow,
            "qa": preflight.workflows.qa_workflow,
        },
        "trusted_context_digest": preflight.trusted_context_digest,
        "worker_dispatch_authorized": False,
        "release_evidence": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(ALLOWED_MODES), required=True)
    parser.add_argument("--scenario", choices=list(EXPECTED_SCENARIOS), required=True)
    args = parser.parse_args()
    mode, scenario = require_scenario_driver_mode(
        mode=args.mode,
        scenario=args.scenario,
        event_name=str(os.environ.get("GITHUB_EVENT_NAME") or ""),
        ref=str(os.environ.get("GITHUB_REF") or ""),
    )
    if mode == VALIDATE_ONLY:
        print(json.dumps({
            "schema_version": "ai-sdlc.v03-scenario-runtime-driver-validation/v1",
            "mode": VALIDATE_ONLY,
            "scenario": scenario,
            "live_authority_loaded": False,
            "worker_dispatch_authorized": False,
            "release_evidence": False,
        }, sort_keys=True))
        return
    preflight = assemble_scenario_live_preflight(
        scenario=scenario,
        env=os.environ,
        checkout_sha=_head(),
    )
    print(json.dumps(public_scenario_preflight(preflight), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
