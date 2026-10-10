---
name: AI-SDLC gh-aw Code Reviewer (deepseek v0.3 bounded local)
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
  steps:
    - name: Require first attempt and affirmative detection before Safe Outputs effects
      env:
        RUN_ATTEMPT: ${{ github.run_attempt }}
        DETECTION_SUCCESS: ${{ needs.detection.outputs.detection_success }}
        DETECTION_CONCLUSION: ${{ needs.detection.outputs.detection_conclusion }}
      run: |
        set -euo pipefail
        test "$RUN_ATTEMPT" = 1
        test "$DETECTION_SUCCESS" = true
        test "$DETECTION_CONCLUSION" = success
  threat-detection:
    enabled: true
    continue-on-error: false
    retries: 0
    engine:
      id: copilot
      version: "1.0.90"
      model: deepseek-chat
      max-turns: 50
      env:
        COPILOT_PROVIDER_BASE_URL: https://api.deepseek.com
        COPILOT_MODEL: deepseek-chat
        COPILOT_PROVIDER_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
        COPILOT_PROVIDER_TYPE: openai
        COPILOT_PROVIDER_WIRE_API: completions
      harness:
        max-retries: 0
    prompt: |
      Complete the full security analysis of the original instructions, agent output,
      patch and bundle. Preserve every built-in threat criterion and verdict rule.
      Do not repeat an identical failed inspection command. Inspect its error once,
      use a different bounded read-only inspection if necessary, and stop that path
      when it cannot provide new evidence. A Git bundle includes a header and may
      require prerequisite objects; do not repeatedly feed the entire bundle to
      git index-pack as though it were a standalone pack file.
      Missing or uninspectable required evidence is not evidence of safety. If a
      complete verdict cannot be established, fail closed through the detector's
      existing result protocol; never invent a clean verdict or suppress a finding.
      Track which evidence has already been inspected. Do not repeat an identical
      inspection against unchanged input, whether the previous command succeeded or
      failed. When a result adds no new evidence, move to the remaining required
      evidence or synthesize the complete analysis. Do not rescan the same comment
      body for identical indicators. Inspect execution/usage logs only to diagnose
      tool availability or errors, never as agent security evidence. After complete
      analysis, submit through threat_detection_result. If it returns
      THREAT_DETECTION_RESULT_ERROR, correct the stated schema problem without
      repeating completed scans. Stop immediately on THREAT_DETECTION_RESULT_RECORDED.
      Reaching a budget or deadline is never grounds for a clean verdict.
  github-token: ${{ secrets.GITHUB_TOKEN }}
  add-comment:
    max: 1
    target: ${{ inputs.candidate_pr_number }}
    target-repo: dream-xin/ai-sdlc
    footer: false
jobs:
  agent:
    pre-steps:
      - name: Reject rerun before model execution
        env:
          RUN_ATTEMPT: ${{ github.run_attempt }}
        run: test "$RUN_ATTEMPT" = 1
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
          test "$STAGE" = "code-review"
          test "$ROLE" = "reviewer"
          case "$FEATURE_ID:$TARGET_REF" in
            F-OPERATOR-V03-DOGFOOD-HAPPY-0001:dogfood/v0.3-happy-path-0001|F-OPERATOR-V03-DOGFOOD-REMEDIATION-0001:dogfood/v0.3-review-remediation-0001|F-OPERATOR-V03-DOGFOOD-SESSION-0001:dogfood/v0.3-session-recovery-0001) ;;
            *) echo "::error::Worker identity is outside the fixed v0.3 fixture pool"; exit 1 ;;
          esac
  conclusion:
    permissions:
      contents: read
    pre-steps:
      - name: Record non-authoritative Gate execution identity
        env:
          SOURCE_RUN_ID: ${{ github.run_id }}
          SOURCE_WORKFLOW_REF: ${{ github.workflow_ref }}
          SOURCE_HEAD_SHA: ${{ github.sha }}
          TARGET_REPOSITORY: ${{ inputs.target_repository }}
          TARGET_REF: ${{ inputs.target_ref }}
          FEATURE_ID: ${{ inputs.feature_id }}
          EXPECTED_REVISION: ${{ inputs.expected_revision }}
          STAGE: ${{ inputs.stage }}
          ROLE: ${{ inputs.role }}
          CANDIDATE_PR_NUMBER: ${{ inputs.candidate_pr_number }}
          CANDIDATE_HEAD_SHA: ${{ inputs.candidate_head_sha }}
          TRUSTED_TASK_ID: ${{ fromJSON(inputs.task_payload).task.id }}
          TASK_PAYLOAD: ${{ inputs.task_payload }}
          DISPATCH_KEY: ${{ inputs.dispatch_key }}
          COMMENT_ID: ${{ needs.safe_outputs.outputs.comment_id }}
          COMMENT_URL: ${{ needs.safe_outputs.outputs.comment_url }}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json, os, re
          names = ("SOURCE_RUN_ID", "SOURCE_WORKFLOW_REF", "SOURCE_HEAD_SHA",
                   "TARGET_REPOSITORY", "TARGET_REF", "FEATURE_ID", "EXPECTED_REVISION",
                   "STAGE", "ROLE", "CANDIDATE_PR_NUMBER", "CANDIDATE_HEAD_SHA",
                   "TRUSTED_TASK_ID", "TASK_PAYLOAD", "DISPATCH_KEY", "COMMENT_ID", "COMMENT_URL")
          values = {name: os.environ[name] for name in names}
          assert all(value and "\n" not in value and "\r" not in value for value in values.values())
          assert values["TARGET_REPOSITORY"].lower() == "dream-xin/ai-sdlc"
          assert values["ROLE"] == "reviewer" and values["STAGE"] == "code-review"
          for name in ("SOURCE_RUN_ID", "CANDIDATE_PR_NUMBER", "COMMENT_ID"):
              assert re.fullmatch(r"[1-9][0-9]*", values[name])
          assert re.fullmatch(r"0|[1-9][0-9]*", values["EXPECTED_REVISION"])
          for name in ("SOURCE_HEAD_SHA", "CANDIDATE_HEAD_SHA"):
              assert re.fullmatch(r"[0-9a-f]{40}", values[name])
          assert re.fullmatch(r"dispatch-[0-9a-f]{40}", values["DISPATCH_KEY"])
          assert values["SOURCE_WORKFLOW_REF"].lower() == (
              "dream-xin/ai-sdlc/.github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml@refs/heads/main")
          payload = json.loads(values["TASK_PAYLOAD"])
          assert payload["task"]["id"] == values["TRUSTED_TASK_ID"]
          assert re.fullmatch(
              r"https://github.com/[Dd][Rr][Ee][Aa][Mm]-[Xx][Ii][Nn]/ai-sdlc/(pull|issues)/"
              + re.escape(values["CANDIDATE_PR_NUMBER"]) + r"#issuecomment-"
              + re.escape(values["COMMENT_ID"]), values["COMMENT_URL"])
          print("Gate execution identity validated; recommendation remains non-authoritative.")
          PY

---
# AI-SDLC bounded autonomous Code Reviewer worker

You are the independent AI-SDLC Code Reviewer worker for stage `code-review`. You are a read-only recommendation worker, not lifecycle authority.

The trusted candidate checkout is nested at `$GITHUB_WORKSPACE/ai-sdlc`; the outer workflow checkout is the controller source, not candidate evidence. Bash and local shell are unavailable. Use the allowed read-only GitHub tools bound to `${{ inputs.candidate_head_sha }}` and candidate PR `${{ inputs.candidate_pr_number }}`; never infer candidate identity from the default working directory. If those tools cannot establish the required candidate evidence, emit BLOCKED.

1. Decode `${{ inputs.task_payload }}` and verify feature/stage/role/repository identity. Confirm the checked-out commit is exactly `${{ inputs.candidate_head_sha }}`. If any identity differs, stop without claiming PASS.
2. Read the Feature Issue, approved Requirement/Design/Plan, relevant implementation/review evidence, candidate PR/diff and required CI using only read-only tools.
3. Do not edit files, create branches, commit, push, create or update PRs, write Feature Manifest/Event state, pass or waive Gates, merge, release, or implement remediation.
4. Evaluate only the assigned `code-review` responsibility. The candidate PR number `${{ inputs.candidate_pr_number }}` and SHA `${{ inputs.candidate_head_sha }}` are immutable trusted inputs; never substitute a newer PR head.
5. Call the `add_comment` Safe Output exactly once. The body must begin with `<!-- AI-SDLC-GATE-RESULT` on its own line, contain exactly one JSON object satisfying contract `ai-sdlc-gh-aw-reviewer-result-v0.1`, then end the machine envelope with `AI-SDLC-GATE-RESULT -->` on its own line. Follow it with a concise human-readable summary.
6. The JSON must include the exact trusted feature/task/stage/role/revision/repository/ref/PR/head identities from the inputs and task payload. Evidence URIs must be durable references such as the candidate PR, repository artifact path, CI run, or this workflow run. Never include secrets or credentials.
7. A PASS recommendation is allowed only when the required independent evidence supports it. Use PASS, REWORK, or BLOCKED only; PASS cannot coexist with BLOCKER/MAJOR findings.
8. The posted comment is explicitly non-authoritative. After `add_comment`, stop. The trusted collector re-fetches the comment and candidate, verifies the exact trusted role-worker run/workflow/task provenance, validates the closed schema and current Manifest revision, and alone decides whether a Feature Event can be constructed.

If evidence is incomplete, candidate identity moved, required context cannot be read, or independent verification cannot establish the requested verdict, use the non-PASS verdict defined by the contract rather than guessing.
