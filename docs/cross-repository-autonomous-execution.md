# Cross-repository autonomous execution

AI-SDLC can hand an executable Developer work unit from a private target repository to the trusted `gh-aw` runtime without moving lifecycle authority into the worker.

## Flow

```text
target Feature Issue / Manifest
  -> target AI-SDLC Issue Command Bridge
  -> trusted ai-sdlc profile gateway
  -> Commander + Runtime Router
  -> cross-repo gh-aw handoff
  -> trusted compiled gh-aw worker
  -> bounded branch in target repository
  -> Draft PR to the target Feature branch
  -> Worker Result
  -> trusted Feature Event collector / persistence
  -> existing review and verification lifecycle
```

The target command is provider-neutral:

```text
/ai-sdlc dispatch-gh-aw target_ref=<feature-branch> manifest=state/features/<feature-id>.yaml
```

The command cannot select an engine, provider, model, worker workflow, or Dispatch Policy. The target repository identity is taken from `GITHUB_REPOSITORY`; it is not accepted as comment input. The trusted profile gateway in `DREAM-XIN/ai-sdlc` resolves the compiled worker. Cross-repository routing always uses the trusted `dispatch/gh-aw-developer.yaml` policy from the control repository default branch.

## WORKING adoption

Normal TODO/READY work follows the existing lifecycle: the trusted gateway persists a START Feature Event, advances the Manifest revision, then dispatches the worker.

A Feature may already be `WORKING` before autonomous execution is installed. The cross-repository handoff can adopt that work unit only when all of these conditions hold:

- Commander independently recomputes `WAIT`;
- exactly one in-progress Feature stage exists;
- it is `workflow.current_stage` and remains `WORKING`;
- Runtime Router still resolves the work unit to `gh-aw/autonomous`;
- target repository and default branch match `.ai-sdlc/project.yaml`.

The adoption path does not write a second START event and does not mutate the Manifest. The Worker Result uses the current Manifest revision. This is a generic resume contract, not a Feature-specific recovery workflow.

## Repository identity

The trusted gateway validates three identities before dispatch:

1. the target repository received from the caller transport;
2. `.ai-sdlc/project.yaml -> repository.full_name`;
3. the repository stored in the generated `feature_context` and worker inputs.

Any mismatch fails closed. The Feature branch must be a valid non-default branch, and manifest/project paths reject absolute paths and parent traversal.

## Credentials and least privilege

There are separate credentials for separate trust boundaries.

### Target -> control dispatch

The target Issue Command Bridge uses `AI_SDLC_CONTROL_DISPATCH_TOKEN` only to start/read the trusted workflow in `DREAM-XIN/ai-sdlc`. Give this credential access only to the control repository and only the Actions/metadata permissions required to dispatch and resolve workflow runs. It does not need target-repository contents or pull-request write access.

Prefer a dedicated GitHub App installation credential or a fine-grained token scoped to the single control repository. Do not use a classic broad PAT.

### Trusted control -> target repository

The control repository stores `AI_SDLC_RUNTIME_APP_CLIENT_ID` and `AI_SDLC_RUNTIME_APP_PRIVATE_KEY` for a GitHub App installed only on repositories that opt into autonomous execution.

The gateway mints short-lived installation tokens for exactly one target repository per run:

- planning/identity checkout: `contents: read`;
- trusted START/result persistence: `contents: write` only when a lifecycle write is required.

The App installation must grant the minimum repository permissions needed by the worker Safe Output path (contents and pull requests) and read access to Issue/PR context. Runtime tokens are narrowed to the target repository for each execution.

The autonomous agent itself remains `permissions: read-all`; source writes happen through gh-aw Safe Output rather than direct `contents: write` job permission.

## Worker boundaries

The worker:

- checks out the exact target repository and Feature ref through the runtime GitHub App;
- creates `gh-aw/<feature>-<run>-v<revision>` from `origin/<target_ref>`;
- reads the Feature Issue, approved requirement/design artifacts, implementation plan, `AGENTS.md`, and `.ai-sdlc/project.yaml`;
- restricts edits to the assigned work unit and Developer-owned Project Adapter roots;
- verifies no `state/features/**` or `state/events/**` change is present;
- creates at most one Draft PR, with Safe Output fixing `target-repo` and `base-branch`;
- cannot pass/waive Gates, merge, release, or directly persist lifecycle state.

The trusted result collector converts the structured Worker Result into a Feature Event and persists it using the existing Event Inbox / optimistic-concurrency checks. Code Review and Verification remain independent later stages.

## Installation

In addition to the normal cross-repository Bootstrap/Plan/Persist installation:

1. install the current `templates/github/ai-sdlc-command.yml` on the target repository default branch;
2. configure `AI_SDLC_CONTROL_DISPATCH_TOKEN` in the target repository with only control-repository workflow dispatch/read permissions;
3. configure the trusted runtime GitHub App credentials in `DREAM-XIN/ai-sdlc`;
4. install that App on each private target repository that opts into autonomous execution, with the minimum contents/pull-request/read-context permissions;
5. keep the target `.ai-sdlc/project.yaml repository.full_name/default_branch` accurate.

No gh-aw provider secret is copied into the target repository. Provider/model selection remains behind the trusted runtime profile gateway.
