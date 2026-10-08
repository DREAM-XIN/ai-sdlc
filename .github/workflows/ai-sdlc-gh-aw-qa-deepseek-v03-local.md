---
name: AI-SDLC gh-aw Verification QA (deepseek v0.3 local)
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
  id: copilot
  model: "deepseek-chat"
  env:
    COPILOT_PROVIDER_BASE_URL: https://api.deepseek.com
    COPILOT_MODEL: deepseek-chat
    COPILOT_PROVIDER_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
    COPILOT_PROVIDER_TYPE: openai
    COPILOT_PROVIDER_WIRE_API: completions
  harness:
    max-retries: 6
    initial-delay-ms: 10000
    backoff-multiplier: 2
    max-delay-ms: 120000
network:
  allowed:
    - defaults
    - api.deepseek.com
permissions:
  contents: read
  issues: read
  pull-requests: read
tools:
  bash: false
  cli-proxy: false
  github:
    toolsets: [repos, issues, pull_requests]
    github-token: ${{ secrets.GITHUB_TOKEN }}
    allowed-repos: ["dream-xin/ai-sdlc"]
    min-integrity: none
max-turn-cache-misses: 20
checkout:
  repository: dream-xin/ai-sdlc
  ref: ${{ inputs.candidate_head_sha }}
  fetch-depth: 0
  current: true
  github-token: ${{ secrets.GITHUB_TOKEN }}
safe-outputs:
  github-token: ${{ secrets.GITHUB_TOKEN }}
  add-comment:
    max: 1
    target: ${{ inputs.candidate_pr_number }}
    target-repo: dream-xin/ai-sdlc
    footer: false
jobs:
  agent:
    pre-steps:
      - name: Validate release-only local Worker identity
        env:
          TARGET_REPOSITORY: ${{ inputs.target_repository }}
          TARGET_REF: ${{ inputs.target_ref }}
          FEATURE_ID: ${{ inputs.feature_id }}
          STAGE: ${{ inputs.stage }}
          ROLE: ${{ inputs.role }}
        run: |
          set -euo pipefail
          test "${TARGET_REPOSITORY,,}" = "dream-xin/ai-sdlc"
          test "$STAGE" = "verification"
          test "$ROLE" = "qa"
          case "$FEATURE_ID:$TARGET_REF" in
            F-OPERATOR-V03-DOGFOOD-HAPPY-0001:dogfood/v0.3-happy-path-0001|F-OPERATOR-V03-DOGFOOD-REMEDIATION-0001:dogfood/v0.3-review-remediation-0001|F-OPERATOR-V03-DOGFOOD-SESSION-0001:dogfood/v0.3-session-recovery-0001) ;;
            *) echo "::error::Worker identity is outside the fixed v0.3 fixture pool"; exit 1 ;;
          esac
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
