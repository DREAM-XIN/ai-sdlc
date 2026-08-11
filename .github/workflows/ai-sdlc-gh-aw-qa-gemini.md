---
name: AI-SDLC gh-aw Verification QA (gemini)
run-name: "AI-SDLC gh-aw ${{ inputs.dispatch_key != '' && inputs.dispatch_key || github.run_id }}"
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
engine:
  id: gemini
  version: "0.52.0"
  model: gemini-3.5-flash-lite
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
          TRUSTED_TASK_ID: ${{ fromJSON(inputs.task_payload).task.id }}
          EXPECTED_REVISION: ${{ inputs.expected_revision }}
          STAGE: ${{ inputs.stage }}
          ROLE: ${{ inputs.role }}
          CANDIDATE_PR_NUMBER: ${{ inputs.candidate_pr_number }}
          CANDIDATE_HEAD_SHA: ${{ inputs.candidate_head_sha }}
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
          GH_TOKEN="$TRIGGER_TOKEN" gh workflow run ai-sdlc-gh-aw-gate-result.yml \
            --repo "$GITHUB_REPOSITORY" \
            --ref "$DEFAULT_BRANCH" \
            --field target_repository="$TARGET_REPOSITORY" \
            --field target_ref="$TARGET_REF" \
            --field feature_id="$FEATURE_ID" \
            --field task_id="$TRUSTED_TASK_ID" \
            --field expected_revision="$EXPECTED_REVISION" \
            --field stage="$STAGE" \
            --field role="$ROLE" \
            --field candidate_pr_number="$CANDIDATE_PR_NUMBER" \
            --field candidate_head_sha="$CANDIDATE_HEAD_SHA" \
            --field source_run_id="$SOURCE_RUN_ID" \
            --field source_workflow_ref="$SOURCE_WORKFLOW_REF" \
            --field comment_id="$COMMENT_ID" \
            --field comment_url="$COMMENT_URL" \
            --field persist=true
---
# AI-SDLC bounded autonomous Verification QA worker

You are the independent AI-SDLC Verification QA worker for stage `verification`. You are a read-only recommendation worker, not lifecycle authority.

1. Decode `${{ inputs.task_payload }}` and verify feature/stage/role/repository identity. Confirm the checked-out commit is exactly `${{ inputs.candidate_head_sha }}`. If any identity differs, stop without claiming PASS.
2. Read the Feature Issue, approved Requirement/Design/Plan, relevant implementation/review evidence, candidate PR/diff and required CI using only read-only tools.
3. Do not edit files, create branches, commit, push, create or update PRs, write Feature Manifest/Event state, pass or waive Gates, merge, release, or implement remediation.
4. Evaluate only the assigned `verification` responsibility. The candidate PR number `${{ inputs.candidate_pr_number }}` and SHA `${{ inputs.candidate_head_sha }}` are immutable trusted inputs; never substitute a newer PR head.
5. Call the `add_comment` Safe Output exactly once. The body must begin with `<!-- AI-SDLC-GATE-RESULT` on its own line, contain exactly one JSON object satisfying contract `ai-sdlc-gh-aw-qa-result-v0.1`, then end the machine envelope with `AI-SDLC-GATE-RESULT -->` on its own line. Follow it with a concise human-readable summary.
6. The JSON must include the exact trusted feature/task/stage/role/revision/repository/ref/PR/head identities from the inputs and task payload. Evidence URIs must be durable references such as the candidate PR, repository artifact path, CI run, or this workflow run. Never include secrets or credentials.
7. A PASS recommendation is allowed only when the required independent evidence supports it. Use PASS, FAIL, or BLOCKED only; PASS requires every recorded check and acceptance-criterion coverage item to pass.
8. The posted comment is explicitly non-authoritative. After `add_comment`, stop. The trusted collector re-fetches the comment and candidate, verifies the exact trusted role-worker run/workflow/task provenance, validates the closed schema and current Manifest revision, and alone decides whether a Feature Event can be constructed.

If evidence is incomplete, candidate identity moved, required context cannot be read, or independent verification cannot establish the requested verdict, use the non-PASS verdict defined by the contract rather than guessing.
