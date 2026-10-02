#!/usr/bin/env python3
"""Closed trusted-main gate before any v0.3 real dogfood mutation.

The release dogfood runner must not start an Operation, call a model, dispatch a
Worker, or mutate the protected Store until both upstream live authorities are
proved on the exact trusted-main SHA or the pinned source with a verified
existing-runtime-identical dogfood-only tree delta:

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
from urllib import request

from gh_aw_provider_registry import load_registry
from v03_dogfood_issue221_compatibility import SOURCE_MAIN, verify_installation
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


REVIEW_PASS_MARKER = "Independent Runtime / Dogfood Release-Evidence Review — PASS"
REVIEW_ANCHOR_PREFIX = "Issue221-Compatibility-Anchor: "


def _github_json_any(*, url: str, token: str) -> Any:
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-dogfood-review-anchor",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise V03DogfoodLiveGateError("cannot read independent dogfood review anchor") from exc


def select_review_anchor(*, pulls: Any, reviews_by_pr: Mapping[int, Any], installation_sha: str, reviewed_delta_digest: str) -> dict[str, Any]:
    if not isinstance(pulls, list):
        raise V03DogfoodLiveGateError("associated pull-request response is malformed")
    candidates: list[dict[str, Any]] = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        head = pr.get("head") or {}
        head_sha = str(head.get("sha") or "").lower()
        if type(number) is not int or number < 1 or not head_sha:
            continue
        reviews = reviews_by_pr.get(number)
        if not isinstance(reviews, list):
            continue
        for review in reviews:
            if not isinstance(review, dict):
                continue
            body = str(review.get("body") or "")
            state = str(review.get("state") or "")
            commit_id = str(review.get("commit_id") or "").lower()
            if (
                state in {"COMMENTED", "APPROVED"}
                and commit_id == head_sha
                and REVIEW_PASS_MARKER in body
                and REVIEW_ANCHOR_PREFIX + reviewed_delta_digest in body
            ):
                candidates.append({
                    "pull_number": number,
                    "review_id": review.get("id"),
                    "review_commit_id": commit_id,
                    "reviewed_delta_digest": reviewed_delta_digest,
                    "installation_commit_sha": installation_sha,
                })
    if len(candidates) != 1:
        raise V03DogfoodLiveGateError("exact installation lacks one independent reviewed-delta anchor")
    return candidates[0]


def verify_review_anchor(*, repository: str, installation_sha: str, reviewed_delta_digest: str, token: str, api_base: str) -> dict[str, Any]:
    if installation_sha == SOURCE_MAIN:
        return {
            "source_main": True,
            "reviewed_delta_digest": reviewed_delta_digest,
            "installation_commit_sha": installation_sha,
        }
    pulls = _github_json_any(
        url=f"{api_base.rstrip('/')}/repos/{repository}/commits/{installation_sha}/pulls?per_page=100",
        token=token,
    )
    reviews_by_pr: dict[int, Any] = {}
    if isinstance(pulls, list):
        for pr in pulls:
            if not isinstance(pr, dict) or type(pr.get("number")) is not int:
                continue
            number = int(pr["number"])
            reviews_by_pr[number] = _github_json_any(
                url=f"{api_base.rstrip('/')}/repos/{repository}/pulls/{number}/reviews?per_page=100",
                token=token,
            )
    return select_review_anchor(
        pulls=pulls,
        reviews_by_pr=reviews_by_pr,
        installation_sha=installation_sha,
        reviewed_delta_digest=reviewed_delta_digest,
    )


@dataclass(frozen=True)
class Issue221Closure:
    trusted_main_head_sha: str
    accepted_record_count: int
    accepted_workflow_run_count: int
    satisfied_scenario_count: int
    workflow_run_ids: tuple[int, ...]
    ledger_digest: str
    evidence_head_sha: str = ""
    compatibility_digest: str = ""
    reviewed_delta_digest: str = ""
    review_anchor_pr_number: int = 0
    review_anchor_review_id: int = 0
    review_anchor_commit_id: str = ""


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
    """Re-verify original immutable #221 artifacts; never rewrite their SHA."""

    if not actions_read_token:
        raise V03DogfoodLiveGateError("dogfood gate lacks Actions read authority")
    compatibility = verify_installation(installation_sha)
    review_anchor = verify_review_anchor(
        repository=repository,
        installation_sha=installation_sha,
        reviewed_delta_digest=str(compatibility["reviewed_delta_digest"]),
        token=actions_read_token,
        api_base=api_base,
    )
    evidence_sha = SOURCE_MAIN
    plan = validate_closed_plan(producer_plan())
    api = api_factory(
        repository=repository,
        token=actions_read_token,
        api_base=api_base,
    )
    selections = select_exact_artifacts(
        plan=plan,
        installation_sha=evidence_sha,
        list_runs=api.list_runs,
        list_artifacts=api.list_artifacts,
    )
    selection_doc, ledger = aggregate_selected_artifacts(
        plan=plan,
        selections=selections,
        download_artifact=api.download_artifact,
        installation_sha=evidence_sha,
    )
    if (
        ledger.get("status") != "PASS"
        or ledger.get("overall_issue_221_pass") is not True
        or ledger.get("accepted_record_count") != 11
        or ledger.get("accepted_workflow_run_count") != 11
        or selection_doc.get("scenario_count") != 13
        or selection_doc.get("trusted_main_head_sha") != evidence_sha
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
        evidence_head_sha=evidence_sha,
        compatibility_digest=compatibility["compatibility_digest"],
        reviewed_delta_digest=str(compatibility["reviewed_delta_digest"]),
        review_anchor_pr_number=int(review_anchor.get("pull_number") or 0),
        review_anchor_review_id=int(review_anchor.get("review_id") or 0),
        review_anchor_commit_id=str(review_anchor.get("review_commit_id") or ""),
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
