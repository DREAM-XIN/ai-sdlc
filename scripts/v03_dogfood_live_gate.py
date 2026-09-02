#!/usr/bin/env python3
"""Closed trusted-main gate before any v0.3 real dogfood mutation.

The release dogfood runner must not start an Operation, call a model, dispatch a
Worker, or mutate the protected Store until both upstream live authorities are
proved on the exact same trusted-main SHA:

* Issue #221 final live ledger is exact 13/13 PASS from 11 distinct immutable
  successful workflow artifacts on this installation SHA;
* Developer / independent Reviewer / QA production bindings resolve from the
  frozen registry/routing policy with currently present credentials.

This module is intentionally read-only.  It creates no dogfood evidence and does
not itself make any provider call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
import os
import subprocess
from typing import Any, Callable, Mapping

from gh_aw_provider_registry import load_registry
from v03_dogfood_execution_bindings import (
    DogfoodExecutionBinding,
    presence_from_environment,
    require_trusted_main_context,
    resolve_dogfood_execution_bindings,
)
from v03_effect_safety_final_live_ledger import (
    GitHubReadApi,
    aggregate_selected_artifacts,
    producer_plan,
    select_exact_artifacts,
    validate_closed_plan,
)

ALLOWED_SCENARIOS = frozenset({
    "happy_path",
    "review_remediation",
    "session_recovery",
})


class V03DogfoodLiveGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Issue221Closure:
    trusted_main_head_sha: str
    accepted_record_count: int
    accepted_workflow_run_count: int
    satisfied_scenario_count: int
    workflow_run_ids: tuple[int, ...]
    ledger_digest: str


@dataclass(frozen=True)
class DogfoodLiveGate:
    scenario: str
    installation_commit_sha: str
    issue221: Issue221Closure
    bindings: tuple[DogfoodExecutionBinding, ...]


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise V03DogfoodLiveGateError("cannot resolve exact dogfood checkout HEAD")
    return completed.stdout.strip().lower()


def _digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def verify_issue_221_closed(
    *,
    repository: str,
    installation_sha: str,
    actions_read_token: str,
    api_base: str,
    api_factory: Callable[..., Any] = GitHubReadApi,
) -> Issue221Closure:
    """Re-run the closed #221 authority-set aggregator before dogfood mutation."""

    if not actions_read_token:
        raise V03DogfoodLiveGateError("dogfood gate lacks Actions read authority")
    plan = validate_closed_plan(producer_plan())
    api = api_factory(
        repository=repository,
        token=actions_read_token,
        api_base=api_base,
    )
    selections = select_exact_artifacts(
        plan=plan,
        installation_sha=installation_sha,
        list_runs=api.list_runs,
        list_artifacts=api.list_artifacts,
    )
    selection_doc, ledger = aggregate_selected_artifacts(
        plan=plan,
        selections=selections,
        download_artifact=api.download_artifact,
        installation_sha=installation_sha,
    )
    if (
        ledger.get("status") != "PASS"
        or ledger.get("overall_issue_221_pass") is not True
        or ledger.get("accepted_record_count") != 11
        or ledger.get("accepted_workflow_run_count") != 11
        or selection_doc.get("scenario_count") != 13
        or selection_doc.get("trusted_main_head_sha") != installation_sha
        or selection_doc.get("release_eligible") is not True
    ):
        raise V03DogfoodLiveGateError("Issue #221 is not exact-main 13/13 release PASS")
    run_ids = selection_doc.get("workflow_run_ids")
    if (
        not isinstance(run_ids, list)
        or len(run_ids) != 11
        or len(set(run_ids)) != 11
        or any(type(value) is not int or value < 1 for value in run_ids)
    ):
        raise V03DogfoodLiveGateError("Issue #221 final ledger lacks 11 distinct source runs")
    return Issue221Closure(
        trusted_main_head_sha=installation_sha,
        accepted_record_count=11,
        accepted_workflow_run_count=11,
        satisfied_scenario_count=13,
        workflow_run_ids=tuple(run_ids),
        ledger_digest=_digest(ledger),
    )


def assemble_dogfood_live_gate(
    *,
    scenario: str,
    env: Mapping[str, str],
    checkout_sha: str,
    issue221_verifier: Callable[..., Issue221Closure] = verify_issue_221_closed,
) -> DogfoodLiveGate:
    if scenario not in ALLOWED_SCENARIOS:
        raise V03DogfoodLiveGateError("unsupported v0.3 release dogfood scenario")
    installation_sha = require_trusted_main_context(
        event_name=str(env.get("GITHUB_EVENT_NAME") or ""),
        ref=str(env.get("GITHUB_REF") or ""),
        workflow_sha=str(env.get("GITHUB_SHA") or ""),
        checkout_sha=checkout_sha,
    )
    repository = str(env.get("GITHUB_REPOSITORY") or "").lower()
    if repository != "dream-xin/ai-sdlc":
        raise V03DogfoodLiveGateError("real v0.3 dogfood is bound to DREAM-XIN/ai-sdlc")

    issue221 = issue221_verifier(
        repository=repository,
        installation_sha=installation_sha,
        actions_read_token=str(env.get("AI_SDLC_ACTIONS_READ_TOKEN") or ""),
        api_base=str(env.get("GITHUB_API_URL") or "https://api.github.com"),
    )
    if issue221.trusted_main_head_sha != installation_sha:
        raise V03DogfoodLiveGateError("Issue #221 closure belongs to another trusted-main generation")

    registry = load_registry()
    presence = presence_from_environment(registry, env)
    bindings = resolve_dogfood_execution_bindings(presence)
    if len(bindings) != 3:
        raise V03DogfoodLiveGateError("production dogfood execution binding set is incomplete")
    return DogfoodLiveGate(
        scenario=scenario,
        installation_commit_sha=installation_sha,
        issue221=issue221,
        bindings=bindings,
    )


def public_gate(gate: DogfoodLiveGate) -> dict[str, Any]:
    return {
        "schema_version": "ai-sdlc.v03-dogfood-live-gate/v1",
        "status": "READY",
        "scenario": gate.scenario,
        "installation_commit_sha": gate.installation_commit_sha,
        "issue_221": {
            **asdict(gate.issue221),
            "workflow_run_ids": list(gate.issue221.workflow_run_ids),
        },
        "bindings": [
            {
                "role": row.role,
                "stage": row.stage,
                "selected_profile": row.selected_profile,
                "provider": row.provider,
                "engine": row.engine,
                "worker_workflow": row.worker_workflow,
                "credential_source": row.credential_source,
                "fallback": row.fallback,
            }
            for row in gate.bindings
        ],
        "model_called": False,
        "worker_dispatched": False,
        "operator_store_mutated": False,
        "feature_event_written": False,
        "dogfood_evidence_created": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(ALLOWED_SCENARIOS), required=True)
    args = parser.parse_args()
    try:
        gate = assemble_dogfood_live_gate(
            scenario=args.scenario,
            env=os.environ,
            checkout_sha=_git_head(),
        )
    except Exception as exc:
        print(json.dumps({
            "schema_version": "ai-sdlc.v03-dogfood-live-gate/v1",
            "status": "BLOCKED",
            "error": str(exc),
            "model_called": False,
            "worker_dispatched": False,
            "operator_store_mutated": False,
            "feature_event_written": False,
            "dogfood_evidence_created": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(public_gate(gate), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
