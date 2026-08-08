---
name: AI-SDLC gh-aw Worker (claude)
on:
  workflow_dispatch:
    inputs:
      feature_id:
        description: AI-SDLC Feature id
        required: true
        type: string
      expected_revision:
        description: Feature revision reserved for this worker result
        required: true
        type: string
      target_ref:
        description: Non-default Feature branch that owns authoritative state
        required: true
        type: string
      stage:
        description: Assigned AI-SDLC stage
        required: true
        type: string
      role:
        description: Assigned AI-SDLC role
        required: true
        type: string
      task_payload:
        description: Compact ai-sdlc-task-v0.1 JSON payload
        required: true
        type: string
engine: claude
permissions: read-all
max-turn-cache-misses: 20
safe-outputs:
  create-pull-request:
    draft: true
    title-prefix: "[ai-sdlc gh-aw] "
    base-branch: ${{ inputs.target_ref }}
    allowed-files:
      - docs/gh-aw-dogfood/**
    protected-files: blocked
    max: 1
  jobs:
    report-result:
      description: Request result handoff only after requesting create_pull_request earlier in the same run. The deterministic job will fail closed unless a real AI-SDLC gh-aw Draft PR exists for the reserved target branch.
      needs: safe_outputs
      runs-on: ubuntu-latest
      permissions:
        actions: write
        contents: read
        pull-requests: read
      inputs:
        summary:
          description: Concise factual completion summary. This does not prove completion; the post-safe-output job independently requires the real Draft PR.
          required: true
          type: string
      steps:
        - name: Dispatch structured worker result
          env:
            GH_TOKEN: ${{ github.token }}
            FEATURE_ID: ${{ inputs.feature_id }}
            EXPECTED_REVISION: ${{ inputs.expected_revision }}
            TARGET_REF: ${{ inputs.target_ref }}
            STAGE: ${{ inputs.stage }}
            TASK_PAYLOAD: ${{ inputs.task_payload }}
            SUMMARY: ${{ inputs.summary }}
            RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
            DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
          run: |
            set -euo pipefail
            test -n "$SUMMARY"
            PR_URL=$(gh pr list \
              --repo "$GITHUB_REPOSITORY" \
              --state open \
              --base "$TARGET_REF" \
              --search 'is:draft in:title "[ai-sdlc gh-aw]"' \
              --limit 2 \
              --json url,title,isDraft \
              --jq 'map(select(.isDraft == true and (.title | startswith("[ai-sdlc gh-aw] ")))) | if length == 1 then .[0].url else empty end')
            test -n "$PR_URL"
            task_id=$(python - <<'PY'
            import json, os
            payload = json.loads(os.environ['TASK_PAYLOAD'])
            print(payload['task']['id'])
            PY
            )
            occurred_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
            TASK_ID="$task_id" OCCURRED_AT="$occurred_at" PR_URL="$PR_URL" python - <<'PY' > worker-result.json
            import json, os
            print(json.dumps({
              'version': '0.1.0',
              'id': f"GHAW-{os.environ['FEATURE_ID']}-{os.environ['STAGE']}-{os.environ['GITHUB_RUN_ID']}",
              'feature_id': os.environ['FEATURE_ID'],
              'task_id': os.environ['TASK_ID'],
              'stage': os.environ['STAGE'],
              'expected_revision': int(os.environ['EXPECTED_REVISION']),
              'status': 'COMPLETED',
              'occurred_at': os.environ['OCCURRED_AT'],
              'artifacts': [{
                'id': f"ART-PR-{os.environ['GITHUB_RUN_ID']}",
                'type': 'pull-request',
                'uri': os.environ['PR_URL'],
              }],
              'evidence': [{
                'id': f"EVID-GHAW-RUN-{os.environ['GITHUB_RUN_ID']}",
                'type': 'runtime-run',
                'status': 'pass',
                'uri': os.environ['RUN_URL'],
              }],
            }, separators=(',', ':')))
            PY
            result=$(cat worker-result.json)
            gh workflow run ai-sdlc-gh-aw-result.yml \
              --repo "$GITHUB_REPOSITORY" \
              --ref "$DEFAULT_BRANCH" \
              --field target_ref="$TARGET_REF" \
              --field worker_result_json="$result" \
              --field persist=true
---
# AI-SDLC bounded autonomous worker

You are an autonomous execution worker inside the AI-SDLC protocol. Treat the workflow inputs and the decoded `task_payload` as authoritative task context, but **not** as authority to modify AI-SDLC lifecycle state.

Your job for this reference dogfood is deliberately narrow:

1. Decode and inspect `${{ inputs.task_payload }}`.
2. Confirm the task is for `${{ inputs.feature_id }}`, stage `${{ inputs.stage }}`, role `${{ inputs.role }}`, and that the task goal is consistent with an implementation work unit.
3. Create or update files **only under `docs/gh-aw-dogfood/`**. Do not modify source code, schemas, workflows, manifests, dependency files, security configuration, or any other path.
4. Produce one small documentation artifact that records the bounded task goal, what was changed, and how the change was verified. Keep it factual and concise.
5. Review the diff before finishing. If anything outside `docs/gh-aw-dogfood/` changed, revert it.
6. **Submission phase 1 is mandatory:** call the `create_pull_request` safe-output tool with the bounded diff. The requested PR must be a Draft PR targeting `${{ inputs.target_ref }}`. Do not claim completion, emit `noop`, or call `report_result` before requesting this PR.
7. **Submission phase 2 is mandatory and ordered:** only after the `create_pull_request` request has been emitted, call the `report_result` safe-output tool with a non-empty concise completion summary. The deterministic post-safe-output job independently verifies that exactly one matching open Draft PR exists before it can dispatch a COMPLETED Worker Result.

If you cannot request the Draft PR, do not report completion. Do not edit `state/features/**` or `state/events/**`. Do not pass or waive any Gate. Do not merge the PR. Independent AI-SDLC review remains a later stage.
