---
name: AI-SDLC gh-aw Orchestrator Plan (codex)
run-name: "AI-SDLC gh-aw authoring orchestrator ${{ inputs.feature_id }}:${{ fromJSON(inputs.task_payload).task.id }}:r${{ inputs.expected_revision }}"
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
      feature_issue_number:
        required: true
        type: string
      task_payload:
        required: true
        type: string
engine: codex
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
  ref: ${{ inputs.target_ref }}
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
    target: ${{ inputs.feature_issue_number }}
    target-repo: ${{ inputs.target_repository }}
    footer: false
jobs:
  conclusion:
    permissions:
      actions: write
      contents: read
    pre-steps:
      - name: Dispatch authoring payload to trusted canonical writer
        env:
          TRIGGER_TOKEN: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}
          TARGET_REPOSITORY: ${{ inputs.target_repository }}
          TARGET_REF: ${{ inputs.target_ref }}
          FEATURE_ID: ${{ inputs.feature_id }}
          FEATURE_ISSUE_NUMBER: ${{ inputs.feature_issue_number }}
          TRUSTED_TASK_ID: ${{ fromJSON(inputs.task_payload).task.id }}
          EXPECTED_REVISION: ${{ inputs.expected_revision }}
          STAGE: ${{ inputs.stage }}
          ROLE: ${{ inputs.role }}
          SOURCE_RUN_ID: ${{ github.run_id }}
          SOURCE_WORKFLOW_REF: ${{ github.workflow_ref }}
          COMMENT_ID: ${{ needs.safe_outputs.outputs.comment_id }}
          COMMENT_URL: ${{ needs.safe_outputs.outputs.comment_url }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          test -n "${TRIGGER_TOKEN:-}"
          test -n "$TRUSTED_TASK_ID"
          test -n "$SOURCE_RUN_ID"
          test -n "$SOURCE_WORKFLOW_REF"
          test -n "$COMMENT_ID"
          test -n "$COMMENT_URL"
          GH_TOKEN="$TRIGGER_TOKEN" gh workflow run ai-sdlc-gh-aw-authoring-result.yml \
            --repo "$GITHUB_REPOSITORY" \
            --ref "$DEFAULT_BRANCH" \
            --field target_repository="$TARGET_REPOSITORY" \
            --field target_ref="$TARGET_REF" \
            --field feature_id="$FEATURE_ID" \
            --field feature_issue_number="$FEATURE_ISSUE_NUMBER" \
            --field task_id="$TRUSTED_TASK_ID" \
            --field expected_revision="$EXPECTED_REVISION" \
            --field stage="$STAGE" \
            --field role="$ROLE" \
            --field source_run_id="$SOURCE_RUN_ID" \
            --field source_workflow_ref="$SOURCE_WORKFLOW_REF" \
            --field comment_id="$COMMENT_ID" \
            --field comment_url="$COMMENT_URL" \
            --field persist=true
---
# AI-SDLC bounded autonomous Orchestrator Plan authoring worker

You are the AI-SDLC Orchestrator Plan authoring worker for stage `plan`. You produce artifact content only; you are not repository-write or lifecycle authority.

1. Decode `${{ inputs.task_payload }}` and verify exact feature/task/stage/role/repository identity. Read Feature Issue `${{ inputs.feature_issue_number }}`, AGENTS/project rules, and every approved prerequisite artifact listed in the trusted task context.
2. Do not edit files, create branches, commit, push, create/update PRs, modify Feature Manifest/Event state, pass/waive Gates, merge, or release. Do not choose a destination path, artifact id, provider, model, profile, worker, or lifecycle transition.
3. Author only the bounded `Plan` content required by the assigned task. For remediation, address only durable review feedback in the trusted task payload.
4. Call `add_comment` Safe Output exactly once on the trusted Feature Issue. The body must begin with `<!-- AI-SDLC-AUTHORING-RESULT` on its own line, contain exactly one JSON object satisfying `ai-sdlc-gh-aw-authoring-result-v0.1`, then end with `AI-SDLC-AUTHORING-RESULT -->` on its own line. A concise human summary may follow.
5. The JSON must echo exact trusted identities and contain only: contract, feature_id, task_id, work_kind, expected_revision, target_repository, target_ref, stage, role, status, artifact_body, summary, and optional reason. `artifact_body` is Markdown content, never a path or executable Event.
6. Use `COMPLETED` only when the artifact is complete and bounded. If required context is unavailable or the task cannot be safely completed, use `BLOCKED` and explain `reason` without inventing facts.
7. The comment is non-authoritative transport. The trusted collector re-fetches it, validates exact role-worker run/workflow/task/revision provenance, derives the one canonical path from trusted code, writes the document with separate credentials, registers only a draft artifact, and emits only bounded stage/task state. Independent review Gates remain separate.

After posting the Safe Output comment, stop.
