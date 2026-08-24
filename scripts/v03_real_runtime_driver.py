#!/usr/bin/env python3
"""Trusted v0.3 real-runtime release-driver shell.

PR validation is permanently zero-effect. The only live mode currently exposed is
``preflight-only``: it establishes exact trusted-main protection/policy authority,
Reviewer readiness, fixed fixture candidate truth and the full production
composition, but it does not invoke operation.start or dispatch a Worker.
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
from v03_real_runtime_full_preflight import build_v03_full_runtime_preflight
from v03_real_runtime_live_authority import load_live_authority, require_trusted_main_execution

VALIDATE_ONLY = "validate-only"
PREFLIGHT_ONLY = "preflight-only"
ALLOWED_MODES = frozenset({VALIDATE_ONLY, PREFLIGHT_ONLY})


class V03ReleaseDriverError(RuntimeError):
    pass


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise V03ReleaseDriverError(f"missing trusted driver configuration: {name}")
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
        raise V03ReleaseDriverError("cannot resolve exact checkout HEAD")
    return completed.stdout.strip()


def _clock() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_driver_mode(*, mode: str, event_name: str, ref: str) -> str:
    """Pure mode gate; no GitHub, Store, ruleset or Worker side effects."""
    if mode not in ALLOWED_MODES:
        raise V03ReleaseDriverError("unsupported v0.3 release-driver mode")
    if mode == VALIDATE_ONLY:
        if event_name == "workflow_dispatch":
            raise V03ReleaseDriverError("workflow_dispatch may not masquerade as PR validate-only")
        return mode
    if event_name != "workflow_dispatch" or ref != "refs/heads/main":
        raise V03ReleaseDriverError("preflight-only is authorized only by workflow_dispatch on main")
    return mode


def assemble_live_preflight(
    *,
    env: Mapping[str, str],
    checkout_sha: str,
    live_loader: Callable[..., Any] = load_live_authority,
    reviewer_selector: Callable[..., Any] = selection_from_environment,
    preflight_builder: Callable[..., Any] = build_v03_full_runtime_preflight,
    protection_verifier_factory: Callable[..., Any] = GitHubRepositoryProtectionVerifier,
    clock: Callable[[], Any] = _clock,
):
    """Assemble live preflight only; never invokes operation.start."""
    require_driver_mode(
        mode=PREFLIGHT_ONLY,
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
        raise V03ReleaseDriverError("AI_SDLC_OPERATOR_APP_INTEGRATION_ID must be a positive integer")
    app_id = int(app_id_raw)
    api_base = _required(env, "GITHUB_API_URL")

    live = live_loader(
        execution=execution,
        admin_token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=app_id,
        api_base=api_base,
    )
    selection = reviewer_selector(
        registry_path=Path("runtimes/gh-aw/role-workers.yaml"),
        workflow_dir=Path(".github/workflows"),
    )
    protection = protection_verifier_factory(
        token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=app_id,
        api_base=api_base,
    )
    preflight = preflight_builder(
        execution=execution,
        live_authority=live,
        reviewer_selection=selection,
        protection_verifier=protection,
        adapter_id="v03-real-runtime-release-verifier",
        target_read_token=_required(env, "AI_SDLC_ACTIONS_READ_TOKEN"),
        actions_token=_required(env, "AI_SDLC_ACTIONS_READ_TOKEN"),
        event_write_token=_required(env, "AI_SDLC_EVENT_WRITE_TOKEN"),
        clock=clock,
        store_checkout=Path("."),
        github_api_base=api_base,
    )
    return preflight


def public_preflight(preflight: Any) -> dict[str, Any]:
    """Return only redacted readiness metadata; never credential values."""
    return {
        "schema_version": "ai-sdlc.v03-real-runtime-driver-preflight/v1",
        "mode": PREFLIGHT_ONLY,
        "repository": preflight.execution.repository,
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
        "protected_state_ref_sha": preflight.live_authority.protected_state_ref_sha,
        "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
        "reviewer": public_selection(preflight.reviewer_selection),
        "fixture": {
            "feature_id": preflight.composition.feature_id,
            "target_ref": preflight.composition.target_ref,
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
    args = parser.parse_args()
    mode = require_driver_mode(
        mode=args.mode,
        event_name=str(os.environ.get("GITHUB_EVENT_NAME") or ""),
        ref=str(os.environ.get("GITHUB_REF") or ""),
    )
    if mode == VALIDATE_ONLY:
        print(json.dumps({
            "schema_version": "ai-sdlc.v03-real-runtime-driver-validation/v1",
            "mode": VALIDATE_ONLY,
            "live_authority_loaded": False,
            "worker_dispatch_authorized": False,
            "release_evidence": False,
        }, sort_keys=True))
        return
    result = assemble_live_preflight(env=os.environ, checkout_sha=_head())
    print(json.dumps(public_preflight(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
