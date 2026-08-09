#!/usr/bin/env python3
"""Render bounded read-only Reviewer/QA gh-aw worker sources from trusted registries."""

from __future__ import annotations

import argparse
from pathlib import Path

from gh_aw_provider_registry import load_registry
from gh_aw_role_workers import load_role_workers

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = r'''---
name: __NAME__
run-name: "AI-SDLC gh-aw gate __ROLE__ ${{ inputs.dispatch_key != '' && inputs.dispatch_key || github.run_id }}"
on:
  workflow_dispatch:
    inputs:
      feature_id:
        required: true
        type: string
      expected_revision:
        required: true
        type: string
      dispatch_key:
        required: false
        default: ''
        type: string
      target_repository:
        required: true
        type: string
      target_owner:
        required: true
        type: string
      target_repo_name:
        required: true
        type: string
      target_ref:
        required: true
        type: string
      stage:
        required: true
        type: string
      role:
        required: true
        type: string
      candidate_pr_number:
        required: true
        type: string
      candidate_head_sha:
        required: true
        type: string
      task_payload:
        required: true
        type: string
__ENGINE__
permissions: read-all
tools:
  github:
    toolsets: [repos, issues, pull_requests]
    github-app:
      client-id: ${{ vars.AI_SDLC_RUNTIME_APP_CLIENT_ID }}
      private-key: ${{ secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY }}
      owner: ${{ inputs.target_owner }}
      repositories: ["${{ inputs.target_repo_name }}"]
max-turn-cache-misses: 20
checkout:
  repository: ${{ inputs.target_repository }}
  ref: ${{ inputs.candidate_head_sha }}
  fetch-depth: 0
  current: true
  github-app:
    client-id: ${{ vars.AI_SDLC_RUNTIME_APP_CLIENT_ID }}
    private-key: ${{ secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY }}
    owner: ${{ inputs.target_owner }}
    repositories: ["${{ inputs.target_repo_name }}"]
  safe-outputs-github-app:
    client-id: ${{ vars.AI_SDLC_RUNTIME_APP_CLIENT_ID }}
    private-key: ${{ secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY }}
    owner: ${{ inputs.target_owner }}
    repositories: ["${{ inputs.target_repo_name }}"]
safe-outputs:
  github-app:
    client-id: ${{ vars.AI_SDLC_RUNTIME_APP_CLIENT_ID }}
    private-key: ${{ secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY }}
    owner: ${{ inputs.target_owner }}
    repositories: ["${{ inputs.target_repo_name }}"]
  add-comment:
    max: 1
    target: ${{ inputs.candidate_pr_number }}
    target-repo: ${{ inputs.target_repository }}
    footer: false
jobs:
  conclusion:
    permissions:
      actions: write
      contents: read
    pre-steps:
      - name: Dispatch non-authoritative Gate-role recommendation to trusted collector
        env:
          TRIGGER_TOKEN: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}
          TARGET_REPOSITORY: ${{ inputs.target_repository }}
          TARGET_REF: ${{ inputs.target_ref }}
          FEATURE_ID: ${{ inputs.feature_id }}
          EXPECTED_REVISION: ${{ inputs.expected_revision }}
          STAGE: ${{ inputs.stage }}
          ROLE: ${{ inputs.role }}
          CANDIDATE_PR_NUMBER: ${{ inputs.candidate_pr_number }}
          CANDIDATE_HEAD_SHA: ${{ inputs.candidate_head_sha }}
          COMMENT_ID: ${{ needs.safe_outputs.outputs.comment_id }}
          COMMENT_URL: ${{ needs.safe_outputs.outputs.comment_url }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          test -n "${TRIGGER_TOKEN:-}"
          test -n "$COMMENT_ID"
          test -n "$COMMENT_URL"
          GH_TOKEN="$TRIGGER_TOKEN" gh workflow run ai-sdlc-gh-aw-gate-result.yml \
            --repo "$GITHUB_REPOSITORY" \
            --ref "$DEFAULT_BRANCH" \
            --field target_repository="$TARGET_REPOSITORY" \
            --field target_ref="$TARGET_REF" \
            --field feature_id="$FEATURE_ID" \
            --field expected_revision="$EXPECTED_REVISION" \
            --field stage="$STAGE" \
            --field role="$ROLE" \
            --field candidate_pr_number="$CANDIDATE_PR_NUMBER" \
            --field candidate_head_sha="$CANDIDATE_HEAD_SHA" \
            --field comment_id="$COMMENT_ID" \
            --field comment_url="$COMMENT_URL" \
            --field persist=true
---
# AI-SDLC bounded autonomous __ROLE_LABEL__ worker

You are the independent AI-SDLC __ROLE_LABEL__ worker for stage `__STAGE__`. You are a read-only recommendation worker, not lifecycle authority.

1. Decode `${{ inputs.task_payload }}` and verify feature/stage/role/repository identity. Confirm the checked-out commit is exactly `${{ inputs.candidate_head_sha }}`. If any identity differs, stop without claiming PASS.
2. Read the Feature Issue, approved Requirement/Design/Plan, relevant implementation/review evidence, candidate PR/diff and required CI using only read-only tools.
3. Do not edit files, create branches, commit, push, create or update PRs, write Feature Manifest/Event state, pass or waive Gates, merge, release, or implement remediation.
4. Evaluate only the assigned `__STAGE__` responsibility. The candidate PR number `${{ inputs.candidate_pr_number }}` and SHA `${{ inputs.candidate_head_sha }}` are immutable trusted inputs; never substitute a newer PR head.
5. Call the `add_comment` Safe Output exactly once. The body must begin with `<!-- AI-SDLC-GATE-RESULT` on its own line, contain exactly one JSON object satisfying contract `__CONTRACT__`, then end the machine envelope with `AI-SDLC-GATE-RESULT -->` on its own line. Follow it with a concise human-readable summary.
6. The JSON must include the exact trusted feature/task/stage/role/revision/repository/ref/PR/head identities from the inputs and task payload. Evidence URIs must be durable references such as the candidate PR, repository artifact path, CI run, or this workflow run. Never include secrets or credentials.
7. A PASS recommendation is allowed only when the required independent evidence supports it. __VERDICT_RULES__
8. The posted comment is explicitly non-authoritative. After `add_comment`, stop. The trusted collector re-fetches the comment and candidate, validates the closed schema and current Manifest revision, and alone decides whether a Feature Event can be constructed.

If evidence is incomplete, candidate identity moved, required context cannot be read, or independent verification cannot establish the requested verdict, use the non-PASS verdict defined by the contract rather than guessing.
'''


def engine_block(profile):
    if profile.engine == "gemini":
        lines = ["engine:", "  id: gemini"]
        if profile.engine_version:
            lines.append(f'  version: "{profile.engine_version}"')
        if profile.model:
            lines.append(f"  model: {profile.model}")
        return "\n".join(lines)
    if profile.engine == "copilot" and profile.model:
        return f"engine:\n  id: copilot\n  model: {profile.model}"
    return f"engine: {profile.engine}"


def render(worker, profile):
    reviewer = worker.role == "reviewer"
    label = "Code Reviewer" if reviewer else "Verification QA"
    contract = "ai-sdlc-gh-aw-reviewer-result-v0.1" if reviewer else "ai-sdlc-gh-aw-qa-result-v0.1"
    verdict_rules = (
        "Use PASS, REWORK, or BLOCKED only; PASS cannot coexist with BLOCKER/MAJOR findings."
        if reviewer
        else "Use PASS, FAIL, or BLOCKED only; PASS requires every recorded check and acceptance-criterion coverage item to pass."
    )
    return (TEMPLATE
        .replace("__NAME__", f"AI-SDLC gh-aw {label} ({worker.profile})")
        .replace("__ROLE__", worker.role)
        .replace("__ROLE_LABEL__", label)
        .replace("__STAGE__", worker.stage)
        .replace("__CONTRACT__", contract)
        .replace("__VERDICT_RULES__", verdict_rules)
        .replace("__ENGINE__", engine_block(profile)))


def materialize(*, check: bool):
    registry = load_registry()
    failures = []
    for worker in load_role_workers():
        profile = registry.require_profile(worker.profile)
        expected = render(worker, profile)
        path = ROOT / worker.worker_source
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                failures.append(worker.worker_source)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    if failures:
        raise SystemExit("role-worker source drift: " + ", ".join(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    materialize(check=args.check)
    print("gh-aw Gate-role worker sources are deterministic")


if __name__ == "__main__":
    main()
