---
name: AI-SDLC gh-aw Code Reviewer (copilot v0.3 local)
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
engine: copilot
model: gpt-4.1
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
# AI-SDLC bounded autonomous Code Reviewer worker

You are the independent AI-SDLC Code Reviewer for the fixed v0.3 release-only real-runtime fixture. You are a read-only recommendation worker, never lifecycle authority.

This worker is intentionally bounded to avoid broad repository discovery. The trusted runtime and downstream collector independently bind the exact Feature, task, revision, repository, ref, PR, head SHA, workflow run, and current Manifest. Do not re-discover those identities.

1. Decode `${{ inputs.task_payload }}` and require its feature/task/stage/role/repository identity to agree with the immutable trusted inputs. The stage must be `code-review` and role `reviewer`. If not, emit BLOCKED.
2. Perform exactly one evidence-read operation before the verdict: use the read-only GitHub pull-request tool with method `get_files` for repository `dream-xin/ai-sdlc` and PR `${{ inputs.candidate_pr_number }}`. Do not query PR metadata, commits, Issues/comments, search, CI, or local files. Do not invoke shell. If that one `get_files` read cannot establish the exact diff, emit BLOCKED rather than trying alternate discovery.
3. The changed-file set must be exactly these three paths for the trusted `feature_id`:
   - `docs/features/<feature_id>/implementation.md`
   - `state/events/<feature_id>/EVT-<feature_id>-CODE-REVIEW-START.yaml`
   - `state/features/<feature_id>.yaml`
   No extra path, rename, or deletion is allowed.
4. Review only the returned patches against the frozen fixture contract:
   - implementation: release-only fixture; no product implementation; must not be merged as product work; Worker output is evidence/recommendation only and lifecycle mutation remains protected Store + Feature Persist authority;
   - start Event: version `0.1.0`, exact feature id, `expected_revision: 0`, exactly one draft implementation artifact at the implementation path, and one `code-review -> WORKING` stage change;
   - Manifest: protocol `0.1.0`, revision `1`, exact feature id, profile `v03-real-runtime-fixture`, workflow ACTIVE at `code-review`; code-review WORKING with code-gate, verification/acceptance TODO, all three gates PENDING, exactly one draft implementation artifact, and exactly the start Event in `applied_events`.
   A semantic mismatch is REWORK. Unreadable/incomplete evidence is BLOCKED. PASS is allowed only when every frozen condition above is established from the single exact PR-files result and there is no BLOCKER/MAJOR finding.
5. Call `add_comment` Safe Output exactly once for PASS, REWORK, or BLOCKED. Never use `noop`, `missing_data`, or `missing_tool`. Do not make any further evidence-read call after the single `get_files` operation.
6. Call `add_comment` with the exact machine-envelope shape below. Copy every trusted identity field literally from this template; do not substitute a Manifest path, inferred ref, alternate repository spelling, or a different task id. Change only `<EXACT_TASK_ID_FROM_TASK_PAYLOAD>`, `<VERDICT>`, `<FINDINGS_JSON>`, `<EVIDENCE_STATUS>`, `<UTC_ISO_8601>`, optional `reason`, and the human summary as required by the review result. `<EXACT_TASK_ID_FROM_TASK_PAYLOAD>` must be copied exactly from the already-decoded trusted task payload:

   ```text
   <!-- AI-SDLC-GATE-RESULT
   {
     "version": "0.1.0",
     "contract": "ai-sdlc-gh-aw-reviewer-result-v0.1",
     "id": "vertical:code-review:${{ inputs.candidate_head_sha }}",
     "feature_id": "${{ inputs.feature_id }}",
     "task_id": "<EXACT_TASK_ID_FROM_TASK_PAYLOAD>",
     "stage": "${{ inputs.stage }}",
     "role": "${{ inputs.role }}",
     "expected_revision": ${{ inputs.expected_revision }},
     "target_repository": "${{ inputs.target_repository }}",
     "target_ref": "${{ inputs.target_ref }}",
     "candidate_pr_number": ${{ inputs.candidate_pr_number }},
     "candidate_head_sha": "${{ inputs.candidate_head_sha }}",
     "verdict": "<VERDICT>",
     "findings": <FINDINGS_JSON>,
     "evidence": [
       {
         "type": "review",
         "status": "<EVIDENCE_STATUS>",
         "uri": "https://github.com/${{ inputs.target_repository }}/pull/${{ inputs.candidate_pr_number }}/files"
       }
     ],
     "occurred_at": "<UTC_ISO_8601>"
   }
   AI-SDLC-GATE-RESULT -->
   <concise human summary>
   ```

   `<VERDICT>` must be PASS, REWORK, or BLOCKED. For PASS use `[]` for `<FINDINGS_JSON>` and `pass` for `<EVIDENCE_STATUS>`. For REWORK/BLOCKED include bounded findings, use `fail` or `warning` as appropriate, and add a top-level non-empty `reason` field. The comment must start with the opening marker exactly; do not wrap the JSON in Markdown backticks. The downstream collector independently checks every trusted identity field and rejects any mismatch. Treat both markers and every trusted identity value in the template as immutable transport syntax.
7. Do not edit files, create branches/commits/PRs, write Feature state, pass/waive Gates, merge, release, or implement remediation. The posted comment is non-authoritative; the trusted collector re-fetches and validates it and alone decides whether a Feature Event can be constructed.
