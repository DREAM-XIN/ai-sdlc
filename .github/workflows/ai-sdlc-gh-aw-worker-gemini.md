---
name: AI-SDLC gh-aw Worker (gemini)
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
engine:
  id: gemini
  version: "0.39.1"
  model: gemini-3.5-flash-lite
permissions: read-all
max-turn-cache-misses: 20
checkout:
  fetch-depth: 0
  fetch:
    - "*"
safe-outputs:
  create-pull-request:
    draft: true
    title-prefix: "[ai-sdlc gh-aw] "
    base-branch: ${{ inputs.target_ref }}
    github-token: ${{ secrets.GITHUB_TOKEN }}
    fallback-as-issue: false
    allowed-files:
      - docs/gh-aw-dogfood/**
    protected-files: blocked
    max: 1
jobs:
  conclusion:
    permissions:
      actions: write
      contents: read
      pull-requests: read
    pre-steps:
      - name: Dispatch structured worker result after Draft PR
        env:
          GH_TOKEN: ${{ github.token }}
          FEATURE_ID: ${{ inputs.feature_id }}
          EXPECTED_REVISION: ${{ inputs.expected_revision }}
          TARGET_REF: ${{ inputs.target_ref }}
          STAGE: ${{ inputs.stage }}
          TASK_PAYLOAD: ${{ inputs.task_payload }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          EXPECTED_HEAD_PREFIX="gh-aw/${FEATURE_ID}-${GITHUB_RUN_ID}-v${EXPECTED_REVISION}"
          PR_URL=$(gh pr list \
            --repo "$GITHUB_REPOSITORY" \
            --state open \
            --base "$TARGET_REF" \
            --limit 20 \
            --json url,title,isDraft,headRefName | \
            jq -r --arg prefix "$EXPECTED_HEAD_PREFIX" '
              map(select(
                .isDraft == true and
                (.title | startswith("[ai-sdlc gh-aw] ")) and
                (.headRefName | startswith($prefix))
              )) |
              if length == 1 then .[0].url else empty end
            ')
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
3. Before editing, create and switch to the local work branch `gh-aw/${{ inputs.feature_id }}-${{ github.run_id }}-v${{ inputs.expected_revision }}`. Confirm `git branch --show-current` is exactly that branch before making changes. `${{ inputs.target_ref }}` is the reserved Feature branch and **PR base only**; never use `${{ inputs.target_ref }}` as the local work branch or as `create_pull_request.branch`.
4. Create or update files **only under `docs/gh-aw-dogfood/`**. Do not modify source code, schemas, workflows, manifests, dependency files, security configuration, or any other path.
5. Produce one small documentation artifact that records the bounded task goal, what was changed, and how the change was verified. Keep it factual and concise.
6. Review the diff before finishing. If anything outside `docs/gh-aw-dogfood/` changed, revert it. Commit the bounded change on the local work branch. Do not push it yourself.
7. **Submission is mandatory:** call the `create_pull_request` safe-output tool exactly once with the bounded diff. Set its `branch` argument to exactly `gh-aw/${{ inputs.feature_id }}-${{ github.run_id }}-v${{ inputs.expected_revision }}`, which must also equal `git branch --show-current`. Do not set or override the PR base; the trusted Safe Output configuration already fixes the base to `${{ inputs.target_ref }}`. gh-aw may append a collision-avoidance salt to the remote PR head branch; that is expected. The head branch and `${{ inputs.target_ref }}` must be different.
8. After requesting `create_pull_request`, stop. Do not call any result-reporting tool and do not emit a completion `noop`. The deterministic `conclusion` job will independently verify the unique Draft PR whose remote head starts with this run/revision branch prefix, construct the structured Worker Result, and dispatch it to AI-SDLC.

If `create_pull_request` rejects the branch/base relationship, stop rather than retrying with `${{ inputs.target_ref }}` as the head branch. If you cannot request the Draft PR, do not claim completion. Do not edit `state/features/**` or `state/events/**`. Do not pass or waive any Gate. Do not merge the PR. Independent AI-SDLC review remains a later stage.
