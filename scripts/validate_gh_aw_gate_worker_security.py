#!/usr/bin/env python3
"""Static security validation for autonomous Reviewer/QA worker sources and locks."""

from __future__ import annotations

from pathlib import Path

from gh_aw_role_workers import GATE_ROLE_STAGES, load_role_workers
from validate_gh_aw_gate_provenance import main as validate_gate_provenance
from v03_normalize_reviewer_comment import normalize_reviewer_comment

ROOT = Path(__file__).resolve().parents[1]
BANNED_SOURCE_TOKENS = ("create-pull-request:","push-to-pull-request-branch:","create_pull_request","push_to_pull_request_branch")
BANNED_LOCK_TOKENS = ("create-pull-request","push-to-pull-request-branch","create_pull_request","push_to_pull_request_branch")


def require(condition: bool, message: str):
    if not condition: raise AssertionError(message)


def main():
    workers=[w for w in load_role_workers() if (w.role,w.stage) in GATE_ROLE_STAGES]
    require(len(workers)==4,"expected exactly four Gate-role workers")
    for worker in workers:
        source_path=ROOT/worker.worker_source; lock_path=ROOT/".github"/"workflows"/worker.worker_workflow
        require(source_path.is_file(),f"missing Gate worker source: {worker.worker_source}"); require(lock_path.is_file(),f"missing Gate worker lock: {worker.worker_workflow}")
        source=source_path.read_text(encoding="utf-8"); lock=lock_path.read_text(encoding="utf-8")
        require("permissions:\n  contents: read\n  issues: read\n  pull-requests: read" in source,f"{worker.id}: source must use the minimal reviewed read permission set"); require("  add-comment:" in source,f"{worker.id}: add-comment Safe Output required"); require("ref: ${{ inputs.candidate_head_sha }}" in source,f"{worker.id}: checkout must pin candidate SHA")
        require("fromJSON(inputs.task_payload).task.id" in source,f"{worker.id}: trusted task id required"); require("SOURCE_RUN_ID: ${{ github.run_id }}" in source,f"{worker.id}: source run id required"); require("SOURCE_WORKFLOW_REF: ${{ github.workflow_ref }}" in source,f"{worker.id}: workflow ref required")
        for token in BANNED_SOURCE_TOKENS: require(token not in source,f"{worker.id}: banned source-write token: {token}")
        for token in BANNED_LOCK_TOKENS: require(token not in lock,f"{worker.id}: compiled lock banned source-write token: {token}")
        require("AI-SDLC-GATE-RESULT" in source,f"{worker.id}: Gate result marker required"); require("ai-sdlc-gh-aw-gate-result.yml" in source,f"{worker.id}: Gate collector required")
    local_source=(ROOT/".github/workflows/ai-sdlc-gh-aw-reviewer-copilot-v03-local.md").read_text(encoding="utf-8")
    local_lock=(ROOT/".github/workflows/ai-sdlc-gh-aw-reviewer-copilot-v03-local.lock.yml").read_text(encoding="utf-8")
    collector=(ROOT/".github/workflows/ai-sdlc-gh-aw-gate-result.yml").read_text(encoding="utf-8")
    require("engine:\n  id: copilot\n  harness:\n    max-retries: 6\n    initial-delay-ms: 10000\n    backoff-multiplier: 2\n    max-delay-ms: 120000" in local_source,"v0.3 local Reviewer must retain bounded same-run 429 retry policy")
    require("GH_AW_HARNESS_MAX_RETRIES: 6" in local_lock,"compiled v0.3 local Reviewer must retain six same-run harness retries")
    require("GH_AW_HARNESS_INITIAL_DELAY_MS: 10000" in local_lock,"compiled v0.3 local Reviewer must retain ten-second initial retry delay")
    require("GH_AW_HARNESS_BACKOFF_MULTIPLIER: 2" in local_lock,"compiled v0.3 local Reviewer must retain exponential retry backoff")
    require("GH_AW_HARNESS_MAX_DELAY_MS: 120000" in local_lock,"compiled v0.3 local Reviewer must cap same-run retry delay at two minutes")
    require("Normalize local Reviewer Safe Output into trusted Gate envelope" in local_source,"v0.3 local Reviewer trusted normalization step required")
    require("scripts/v03_normalize_reviewer_comment.py?ref=$SOURCE_SHA" in local_source,"v0.3 local Reviewer normalizer must be fetched from exact workflow SHA")
    require("issues: write" in local_source,"v0.3 local Reviewer conclusion must have bounded comment rewrite authority")
    require("verdict: PASS" in local_source and "verdict: REWORK" in local_source and "verdict: BLOCKED" in local_source,"v0.3 local Reviewer must request one bounded verdict field")
    require("gh api --method PATCH" in local_source,"v0.3 local Reviewer must rewrite the exact Safe Output comment")
    require("jq -j '.body' /tmp/reviewer-comment.patched.json > /tmp/reviewer-comment.actual" in local_source,"v0.3 local Reviewer must compare patched comment bytes without jq newline synthesis")
    require("jq -j '.body' /tmp/reviewer-comment.patched.json > /tmp/reviewer-comment.actual" in local_lock,"compiled v0.3 local Reviewer must preserve byte-exact patched-comment readback")
    require("TARGET_REF: ${{ inputs.target_ref }}" in local_lock,"compiled v0.3 local Reviewer must bind trusted target_ref input")
    require("TARGET_REPOSITORY: ${{ inputs.target_repository }}" in local_lock,"compiled v0.3 local Reviewer must bind trusted target_repository input")
    require("CANDIDATE_HEAD_SHA: ${{ inputs.candidate_head_sha }}" in local_lock,"compiled v0.3 local Reviewer must bind trusted candidate head input")
    require("Normalize local Reviewer Safe Output into trusted Gate envelope" in local_lock,"compiled v0.3 local Reviewer must retain trusted normalization step")
    require('target_repository_lc="${TARGET_REPOSITORY,,}"' in collector,"Gate collector must canonicalize target repository case")
    require('current_repository_lc="${GITHUB_REPOSITORY,,}"' in collector,"Gate collector must canonicalize control repository case")
    require('[[ "$target_repository_lc" == "$current_repository_lc" ]]' in collector,"Gate collector same-repo decision must be case-insensitive")
    require("if: ${{ steps.target.outputs.cross_repo == 'true' }}" in collector,"Runtime App token must remain cross-repo only")
    normalized=normalize_reviewer_comment(
        "review complete\nverdict: PASS\n",
        feature_id="F-TEST",
        task_id="vertical:code-review:" + "a"*40,
        expected_revision=1,
        target_repository="DREAM-XIN/ai-sdlc",
        target_ref="verification/test",
        candidate_pr_number=42,
        candidate_head_sha="a"*40,
        comment_url="https://github.com/DREAM-XIN/ai-sdlc/pull/42#issuecomment-1",
        occurred_at="2026-09-25T00:00:00Z",
    )
    require(normalized.startswith("<!-- AI-SDLC-GATE-RESULT\n"),"trusted Reviewer normalizer must emit closed Gate envelope")
    payload=normalized.split("\n",1)[1].split("\nAI-SDLC-GATE-RESULT -->",1)[0]
    import json
    parsed=json.loads(payload)
    require(parsed["verdict"]=="PASS","trusted Reviewer normalizer lost exact PASS recommendation")
    require(parsed["target_repository"]=="dream-xin/ai-sdlc","trusted Reviewer normalizer must canonicalize repository identity")
    require(parsed["target_ref"]=="verification/test","trusted Reviewer normalizer must use trusted ref")
    blocked=normalize_reviewer_comment(
        "ambiguous\nverdict: PASS\nverdict: REWORK\n",
        feature_id="F-TEST",
        task_id="vertical:code-review:" + "a"*40,
        expected_revision=1,
        target_repository="dream-xin/ai-sdlc",
        target_ref="verification/test",
        candidate_pr_number=42,
        candidate_head_sha="a"*40,
        comment_url="https://github.com/dream-xin/ai-sdlc/pull/42#issuecomment-1",
        occurred_at="2026-09-25T00:00:00Z",
    )
    blocked_payload=json.loads(blocked.split("\n",1)[1].split("\nAI-SDLC-GATE-RESULT -->",1)[0])
    require(blocked_payload["verdict"]=="BLOCKED" and blocked_payload.get("reason"),"ambiguous Reviewer verdict must fail closed")
    validate_gate_provenance(); print("gh-aw Gate-role read-only worker security validation passed")


if __name__=="__main__": main()
