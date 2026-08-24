#!/usr/bin/env python3
"""Real GitHub Actions lost-ACK smoke for Issue #221.

This is deliberately a partial real-runtime probe. It proves stable-key Actions
receipt recovery only after the complete trusted-main production prerequisite set,
an explicit immediate-dispatch Feature/PR fixture, and a reviewed manual execution
from trusted `main`. It cannot become release PASS until the same scenario is bound
to the protected durable Operator Store and exact Feature Persist path.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib import error, request
from urllib.parse import quote

import yaml

from operator_github_actions_transport import GitHubActionsTransportConfig, GitHubActionsWorkflowTransport
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from v03_effect_safety_release_evidence import SCHEMA_VERSION, validate_release_evidence
from v03_real_runtime_execution_authority import (
    RealRuntimeExecutionAuthorityError,
    require_real_runtime_execution_authority,
)
from v03_real_runtime_prerequisites import (
    CONTROL_REF,
    REVIEWER_WORKFLOW,
    collect_trusted_main_prerequisites,
    missing_prerequisites,
)
from v03_real_runtime_smoke_fixture import RealRuntimeSmokeFixtureError, prepare_real_runtime_smoke_fixture

SCENARIO = "lost-ack-crash-takeover"
EVIDENCE_PATH = Path("evidence/v03-real-runtime-effect-safety-smoke.json")


def _request_json(url: str, token: str) -> object:
    req = request.Request(url, method="GET")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def get_json(url: str, token: str) -> object:
    return _request_json(url, token)


def get_json_optional(url: str, token: str) -> object | None:
    try:
        return _request_json(url, token)
    except error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def write_evidence(record: dict) -> None:
    validate_release_evidence(record)
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    token = os.environ["GITHUB_TOKEN"]
    pr_number = os.environ["FI_PR_NUMBER"]
    feature_id = os.environ["FI_FEATURE_ID"]
    target_ref = os.environ["FI_TARGET_REF"]
    sha = os.environ["GITHUB_SHA"]
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")

    pr = get_json(f"{api}/repos/{repo}/pulls/{pr_number}", token)
    candidate_head = str(pr["head"]["sha"])

    manifest_path = f"state/features/{feature_id}.yaml"
    content = get_json(f"{api}/repos/{repo}/contents/{manifest_path}?ref={quote(target_ref, safe='')}", token)
    manifest = yaml.safe_load(base64.b64decode(content["content"]).decode())
    revision = int(manifest["revision"])

    operation_id = f"op-fi-real-{sha[:20]}"
    # Before a runnable selector action is accepted, no production semantic
    # effect identity exists for this smoke. Keep BLOCKED evidence explicitly
    # unbound rather than inventing a production-looking effect key.
    semantic_key = f"preflight-unbound-{sha[:40]}"
    external_key = f"preflight-unbound-{sha[:24]}"

    subject = {
        "repository": repo,
        "feature_id": feature_id,
        "target_ref": target_ref,
        "feature_revision": revision,
        "candidate_pr_number": int(pr_number),
        "candidate_head_sha": candidate_head,
        "operation_id": operation_id,
        "operation_generation": 0,
    }
    effect = {
        "semantic_effect_key": semantic_key,
        "external_dispatch_key": external_key,
    }
    prerequisites = collect_trusted_main_prerequisites(
        repository=repo,
        api_base=api,
        get_json_optional=lambda url: get_json_optional(url, token),
    )

    def nonpass(status: str, *remaining: str, runtime: dict | None = None, observations: dict | None = None) -> dict:
        record = {
            "schema_version": SCHEMA_VERSION,
            "scenario_id": SCENARIO,
            "status": status,
            "release_eligible": False,
            "control_ref": CONTROL_REF,
            "subject": subject,
            "effect": effect,
            "github_run": {
                "id": int(run_id) if run_id.isdigit() else 0,
                "url": f"{server}/{repo}/actions/runs/{run_id}" if run_id else "unavailable",
                "workflow": "v0.3 Real Runtime Effect Safety Smoke",
                "head_sha": sha,
            },
            "runtime": runtime or {"adapter": "gh-aw/github-actions"},
            "persist": {},
            "observations": observations or {},
            "prerequisites": dict(prerequisites),
            "remaining_release_proof": list(remaining),
        }
        write_evidence(record)
        return record

    # Gate 1: every trusted-main production prerequisite must be present before
    # this smoke is allowed to proceed toward any real external authority.
    missing = missing_prerequisites(prerequisites)
    if missing:
        nonpass(
            "BLOCKED",
            f"trusted main full-runtime prerequisites are still missing: {', '.join(missing)}",
            "do not switch dispatch authority to the verification ref or partially bypass the prerequisite set",
            "after every trusted-main prerequisite lands, revalidate the explicitly configured existing Feature/PR fixture before any dispatch",
            "bind the recovered runtime receipt to a protected durable Store Operation/generation and prove exact Feature Persist at most once",
            observations={
                "external_dispatch_attempted": False,
                "duplicate_external_effect_count": 0,
                "speculative_retry_under_unknown_count": 0,
            },
        )
        raise SystemExit(
            "TRUSTED_MAIN_PREREQUISITES_INCOMPLETE: full prerequisite set is not ready; no external dispatch attempted"
        )

    # Gate 2: derive the exact next action through the accepted Vertical selector.
    # Historical completed Features and READY states that require a bounded Persist
    # before dispatch are rejected; this transport-only smoke never skips that step.
    try:
        prepared = prepare_real_runtime_smoke_fixture(
            repository=repo,
            feature_id=feature_id,
            target_ref=target_ref,
            candidate_pr_number=int(pr_number),
            pull_request=pr,
            manifest=manifest,
            occurred_at="2026-08-11T00:00:00Z",
        )
    except RealRuntimeSmokeFixtureError as exc:
        nonpass(
            "BLOCKED",
            f"configured real-runtime fixture is not currently immediate-dispatch runnable: {exc}",
            "select an explicit existing ACTIVE Feature whose accepted next Vertical action is already dispatch",
            "do not reuse a completed/acceptance Feature or skip a required Persist stage-start as a live fault-injection target",
            observations={
                "external_dispatch_attempted": False,
                "duplicate_external_effect_count": 0,
                "speculative_retry_under_unknown_count": 0,
            },
        )
        raise SystemExit("REAL_RUNTIME_FIXTURE_NOT_DISPATCH_READY: no external dispatch attempted")

    semantic_key = str(prepared["semantic_effect_key"])
    external_key = str(prepared["external_dispatch_key"])
    effect["semantic_effect_key"] = semantic_key
    effect["external_dispatch_key"] = external_key
    if prepared["candidate_head_sha"] != candidate_head or prepared["feature_revision"] != revision:
        raise RuntimeError("selector-derived fixture identity drifted before runtime composition")

    # Gate 3: even a fully provisioned, dispatch-ready fixture is observation-only
    # on automatic push/PR runs. Real external launch authority exists only in the
    # explicit workflow_dispatch job of the reviewed workflow on refs/heads/main.
    try:
        require_real_runtime_execution_authority(
            github_event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
            github_ref=os.environ.get("GITHUB_REF", ""),
            external_execution_authorized=os.environ.get("FI_EXTERNAL_EXECUTION_AUTHORIZED", ""),
        )
    except RealRuntimeExecutionAuthorityError as exc:
        nonpass(
            "BLOCKED",
            f"real external execution authority is not active for this run: {exc}",
            "merge/review the harness before using its trusted-main workflow_dispatch execution job",
            "automatic push/PR validation remains observation-only even when prerequisites and a runnable fixture are available",
            observations={
                "external_dispatch_attempted": False,
                "duplicate_external_effect_count": 0,
                "speculative_retry_under_unknown_count": 0,
            },
        )
        raise SystemExit("REAL_RUNTIME_EXTERNAL_EXECUTION_NOT_AUTHORIZED: no external dispatch attempted")

    workflows = GhAwVerticalWorkflowMap(
        default_branch=CONTROL_REF,
        developer_workflow="ai-sdlc-gh-aw-worker-codex.lock.yml",
        reviewer_workflow=REVIEWER_WORKFLOW,
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    workflow = workflows.workflow_for(str(prepared["role"]))
    config = GitHubActionsTransportConfig(
        repository=repo,
        token=token,
        api_url=api,
        receipt_poll_attempts=4,
        receipt_poll_seconds=1.0,
    )

    def make_dispatch(generation: int, dispatch_id: str) -> dict:
        return {
            "operation_id": operation_id,
            "operation_generation": generation,
            "semantic_effect_key": semantic_key,
            "external_dispatch_key": external_key,
            "dispatch_id": dispatch_id,
            "target_repository": repo,
            "feature_id": feature_id,
            "expected_revision": revision,
            "target_ref": target_ref,
            "candidate_pr_number": int(pr_number),
            "candidate_head_sha": candidate_head,
            "task_id": str(prepared["task_id"]),
            "task_identity": str(prepared["task_identity"]),
            "role": str(prepared["role"]),
            "current_stage": str(prepared["current_stage"]),
            "operation_profile": VERTICAL_PROFILE,
            "trusted_context_digest": "v03-real-runtime-fi",
            "worker_adapter": "gh-aw",
        }

    transport_g0 = GitHubActionsWorkflowTransport(config)
    gateway_g0 = GhAwVerticalRoleDispatchGateway(workflows=workflows, transport=transport_g0)
    _discarded_ack = gateway_g0.launch(dispatch=make_dispatch(0, f"fi-g0-{sha[:16]}"))

    fresh = GitHubActionsWorkflowTransport(config)
    receipt = None
    last_observed = None
    for _ in range(12):
        observed = fresh.lookup(workflow=workflow, ref=CONTROL_REF, dispatch_key=external_key)
        last_observed = observed
        if observed["lookup_state"] == "LAUNCHED":
            receipt = observed
            break
        # UNKNOWN/NOT_LAUNCHED are observations only. No second dispatch is issued.
        time.sleep(2)
    if receipt is None:
        nonpass(
            "BLOCKED",
            "real launch acknowledgement was lost and the exact external dispatch key has not converged to a trusted LAUNCHED receipt",
            "resolve the same external dispatch key through trusted lookup before any retry or successor activation",
            "after receipt recovery, bind it to protected durable Store generation takeover and exact Feature Persist evidence",
            runtime={
                "adapter": "gh-aw/github-actions",
                "workflow": workflow,
                "lookup_state": (last_observed or {}).get("lookup_state", "UNKNOWN"),
            },
            observations={
                "external_dispatch_attempted": True,
                "speculative_retry_under_unknown_count": 0,
            },
        )
        raise SystemExit("real gh-aw dispatch was not durably correlated after lost ACK; no speculative retry attempted")

    transport_g1 = GitHubActionsWorkflowTransport(config)
    gateway_g1 = GhAwVerticalRoleDispatchGateway(workflows=workflows, transport=transport_g1)
    takeover = gateway_g1.launch(dispatch=make_dispatch(1, f"fi-g1-{sha[:16]}"))
    if takeover.get("lookup_state") != "LAUNCHED" or str(takeover.get("receipt_id")) != str(receipt["receipt_id"]):
        nonpass(
            "BLOCKED",
            "fresh generation transport did not adopt the exact previously recovered receipt",
            "do not issue a new external dispatch key; investigate same-key takeover correlation",
            runtime={
                "adapter": "gh-aw/github-actions",
                "workflow": workflow,
                "receipt_id": str(receipt["receipt_id"]),
                "takeover_receipt_id": str(takeover.get("receipt_id") or ""),
            },
        )
        raise SystemExit(f"generation takeover did not adopt exact existing receipt: {takeover} vs {receipt}")

    expected_title = f"AI-SDLC gh-aw {external_key}"
    runs = fresh._runs(workflow=workflow, ref=CONTROL_REF)
    exact = [row for row in runs if fresh._run_title(row) == expected_title]
    if len(exact) != 1:
        nonpass(
            "BLOCKED",
            f"expected exactly one real workflow run for the stable external key, observed {len(exact)}",
            "do not promote this scenario; inspect duplicate/missing real runtime executions",
            runtime={"adapter": "gh-aw/github-actions", "workflow": workflow, "receipt_id": str(receipt["receipt_id"])},
            observations={"matching_real_run_count": len(exact)},
        )
        raise SystemExit(f"duplicate real Worker effect count is {len(exact)}, expected 1")

    nonpass(
        "PENDING",
        "re-execute lost-ACK/takeover through a protected durable Operator Store Operation and record exact Store snapshot/projection evidence",
        "prove the exact correlated result can cause Feature Persist at most once through the trusted Persist path",
        "full trusted-main prerequisites, immediate-dispatch fixture and trusted-main manual execution authority were present for this transport slice; protected Store/Persist execution remains separate",
        runtime={
            "adapter": "gh-aw/github-actions",
            "workflow": workflow,
            "receipt_id": str(receipt["receipt_id"]),
            "takeover_receipt_id": str(takeover["receipt_id"]),
        },
        observations={
            "external_dispatch_attempted": True,
            "matching_real_run_count": 1,
            "duplicate_external_effect_count": 0,
            "speculative_retry_under_unknown_count": 0,
            "transport_lost_ack_recovered": True,
            "transport_takeover_adopted_same_receipt": True,
        },
    )


if __name__ == "__main__":
    main()
