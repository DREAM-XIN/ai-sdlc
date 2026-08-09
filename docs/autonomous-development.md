# Autonomous Development

AI-SDLC can execute Developer work manually or through an autonomous runtime without moving lifecycle authority into the worker.

The lifecycle is the same in both modes. What changes is **who executes the bounded work unit**.

## Two execution modes

### Manual / ChatGPT Web

```text
Feature Manifest
  -> Commander / Plan
  -> portable Task Package / prompt
  -> human or ChatGPT Web worker
  -> source/artifact/evidence result
  -> proposed Feature Event
  -> trusted Persist
  -> next Plan
```

Manual mode is useful when:

- a human wants interactive control;
- the work needs judgment or local context that is not available to an autonomous runtime;
- the target is not configured for autonomous execution;
- you want to inspect every transition manually.

ChatGPT Web is a worker transport, not the system of record. The durable result still belongs in GitHub.

### Autonomous gh-aw

```text
Feature
  ↓
Commander
  ↓
Runtime Router
  ↓
trusted control repository
  ↓
gh-aw Developer
  ↓
bounded worker branch
  ↓
Draft PR
  ↓
Feature branch
  ↓
Worker Result
  ↓
trusted Feature Event collector / Persist
  ↓
Code Review / Verification / Acceptance
```

The autonomous path is intentionally Developer-oriented. It does not collapse the rest of the lifecycle into one agent run.

## The autonomous command

When Commander/Runtime Router resolves the current executable work to the trusted gh-aw path, the target Issue Comment command is:

```text
/ai-sdlc dispatch-gh-aw target_ref=<feature-branch> manifest=state/features/<feature-id>.yaml
```

Example:

```text
/ai-sdlc dispatch-gh-aw target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

The command deliberately does **not** accept:

- target repository selectors;
- Dispatch Policy selectors;
- provider selectors;
- model selectors;
- engine-profile selectors;
- credential selectors;
- compiled-worker workflow selectors.

Target identity comes from `GITHUB_REPOSITORY`. Trusted routing and compiled-worker selection remain in `DREAM-XIN/ai-sdlc`.

## What the Developer worker may do

A trusted gh-aw Developer worker may:

- read the exact target repository and Feature ref;
- read `AGENTS.md` and `.ai-sdlc/project.yaml`;
- read the Feature Manifest and linked Feature Issue;
- read approved Requirement/Design artifacts and the implementation Plan appropriate to the profile;
- change source, tests, and other files allowed by the assigned work unit and ownership roots;
- run allowed verification commands under the runtime policy;
- create a bounded implementation branch;
- create at most one Draft PR back to the Feature branch;
- return a structured Worker Result for the trusted collector.

The bounded branch follows the trusted runtime pattern:

```text
gh-aw/<feature>-<run>-v<revision>
```

The exact branch is execution metadata. The long-lived integration branch remains:

```text
feature/<feature-id>
```

## What the Developer worker may not do

The autonomous Developer worker must not:

- modify `state/features/**`;
- modify `state/events/**`;
- directly persist lifecycle state;
- PASS or waive a Gate;
- approve its own code as independent Code Review;
- skip Verification;
- merge the Draft PR;
- merge the Feature branch;
- release/deploy merely because implementation completed;
- broaden the target repository or base branch chosen by the trusted runtime.

The worker's job is implementation. The collector/control plane owns lifecycle ingestion and persistence. Independent reviewers and QA own later Gates.

## Feature branch vs worker branch

This distinction is essential:

```text
feature/F-DEMO-LOGIN-0001
```

is the durable Feature branch containing the Feature's integrated source and authoritative lifecycle state.

A branch such as:

```text
gh-aw/F-DEMO-LOGIN-0001-<run>-v<revision>
```

is a bounded implementation branch for one autonomous execution. Its Draft PR targets the Feature branch, not `main`.

After implementation is integrated into the Feature branch, the Feature still proceeds through Code Review, Verification, Acceptance, and normal final merge policy.

## Fresh dispatch vs adopting existing `WORKING`

Normally, an autonomous dispatch begins from TODO/READY lifecycle work through the trusted START transition and then launches the worker.

v0.2.0 also supports a bounded resume/adoption case when a Feature is already `WORKING`. The gateway may adopt the existing work only when trusted planning independently confirms all of these conditions:

- Commander returns `WAIT`;
- exactly one current Feature stage is in progress;
- that stage is `workflow.current_stage` and remains `WORKING`;
- Runtime Router still resolves the work unit to `gh-aw/autonomous`;
- target repository/default branch identity matches the Project Adapter.

The resume path does not write a second START event. Do not manually force a Manifest into `WORKING` just to make autonomous dispatch eligible.

## Private target: credentials and trust boundaries

Cross-repository autonomous execution uses two separate credential boundaries.

### 1. Target repository -> trusted control repository

Configure this secret in the target repository:

```text
AI_SDLC_CONTROL_DISPATCH_TOKEN
```

It is used by `ai-sdlc-command.yml` to dispatch/read the trusted workflow in `DREAM-XIN/ai-sdlc`.

Scope it to the single control repository and the minimum required Actions/metadata access. It does not need target source-write or pull-request-write access.

Prefer a dedicated GitHub App installation credential or fine-grained token. Do not use a broad classic PAT as a shortcut.

### 2. Trusted control repository -> exact target repository

Configure these in the trusted `DREAM-XIN/ai-sdlc` repository:

```text
AI_SDLC_RUNTIME_APP_CLIENT_ID
AI_SDLC_RUNTIME_APP_PRIVATE_KEY
```

Install the corresponding Runtime GitHub App only on target repositories that opt in.

The trusted gateway mints short-lived installation tokens for exactly one target repository per run. Planning/identity access is read-only; lifecycle persistence or the Safe Output path receives only the permissions required for that exact operation.

Do not copy Runtime App private-key material into target repositories or Task Packages.

## Run the Cross-Repo Runtime Preflight first

Before the first autonomous dispatch for a private target, run this control-repository workflow:

```text
AI-SDLC gh-aw Cross-Repo Runtime Preflight
```

Provide the exact target repository in `owner/repo` form.

A successful `READY` result proves the control-to-target Runtime GitHub App installation/read transport, including that:

- `AI_SDLC_RUNTIME_APP_CLIENT_ID` is configured;
- `AI_SDLC_RUNTIME_APP_PRIVATE_KEY` is configured without exposing its value;
- the Runtime App is installed on the exact target;
- a narrow exact-target token can read repository metadata.

It intentionally does **not** prove provider/model quota, target write capability, or actual implementation success.

## Run the engine/provider preflight separately

The control repository also contains:

```text
AI-SDLC gh-aw Runtime Preflight
```

This is separate from the cross-repository Runtime App preflight. Use it to validate the trusted gh-aw execution engine/profile/provider readiness.

Think of the two checks as:

```text
Cross-Repo Runtime Preflight
    -> Can trusted control reach this exact target through the Runtime App?

Runtime Preflight
    -> Is the configured gh-aw execution engine/provider path ready?
```

Passing one does not imply the other passed.

## Public target while control repository is private

The same public/private transport rule described in [Set up a new project](new-project-setup.md) applies.

A public target cannot download the private AI-SDLC lifecycle Action directly. Its installed `ai-sdlc-command.yml` dispatches trusted control-repository workflows using:

```text
AI_SDLC_CONTROL_DISPATCH_TOKEN
```

The trusted control repository then reaches the exact public target through the Runtime GitHub App boundary.

Autonomous dispatch already uses the trusted control-repository path. Do not replace the separated credentials with a broad token shared between target and control.

See [Public target lifecycle transport](public-target-lifecycle-transport.md).

## Provider and model selection stay behind trusted routing

AI-SDLC separates:

```text
runtime  != provider != model
```

For example, `gh-aw` is a runtime. A provider/model is selected behind trusted engine/profile/routing configuration.

The target Issue Comment cannot choose an arbitrary provider or compiled worker. This prevents a target Feature branch/comment from replacing the reviewed autonomous execution boundary.

No AI-provider secret needs to be copied into the target repository merely to use the cross-repository command surface.

## What happens after the Draft PR exists

A Draft PR is **not** the end of the Feature lifecycle.

The expected sequence is:

```text
Draft PR created by Developer
        ↓
Worker Result returned
        ↓
trusted collector creates/applies Feature Event
        ↓
Feature Manifest reflects implementation result
        ↓
Plan again
        ↓
independent Code Review
        ↓
independent Verification
        ↓
Acceptance (standard-feature)
        ↓
Feature DONE
```

If the Draft PR exists but the Manifest did not advance, do not manually edit the Manifest. Diagnose the Worker Result / collector / Persist path. See [Troubleshooting](troubleshooting.md).

## Review remediation remains lifecycle work

If an independent reviewer finds a problem, AI-SDLC can represent remediation as durable work rather than silently reopening history.

The safe pattern is:

```text
Reviewer findings
  -> durable remediation task
  -> Developer fix
  -> new result/revision
  -> independent re-review
```

Do not let the same Developer worker convert its own fix directly into Code Gate PASS.

## Autonomous safety checklist

Before dispatch:

- [ ] target installation contract is valid;
- [ ] current Manifest/revision was re-read;
- [ ] current stage is eligible Developer work according to Commander/Runtime Router;
- [ ] Feature branch is non-default;
- [ ] `AI_SDLC_CONTROL_DISPATCH_TOKEN` is present when cross-repository dispatch requires it;
- [ ] Runtime GitHub App is installed on the exact target;
- [ ] `AI_SDLC_RUNTIME_APP_CLIENT_ID` and `AI_SDLC_RUNTIME_APP_PRIVATE_KEY` are configured in trusted control;
- [ ] Cross-Repo Runtime Preflight is `READY` for a private target before first dispatch;
- [ ] engine/provider Runtime Preflight is ready;
- [ ] Project Adapter repository identity and ownership boundaries are correct.

After dispatch:

- [ ] worker branch is bounded;
- [ ] Draft PR base is the Feature branch;
- [ ] no `state/features/**` or `state/events/**` diff exists in worker output;
- [ ] Worker Result reached the trusted collector;
- [ ] Manifest advanced only through validated persistence;
- [ ] independent Code Review and Verification still occur.

## Deep references

This guide is an operator summary. For the complete runtime/security contract, read:

- [Cross-repository autonomous execution](cross-repository-autonomous-execution.md)
- [Cross-repository installation](cross-repository-installation.md)
- [Security model](security-model.md)
- [gh-aw integration](integrations/gh-aw.md)
- [`runtimes/gh-aw/README.md`](../runtimes/gh-aw/README.md)

For the day-to-day stage loop, return to [Feature lifecycle guide](feature-lifecycle-guide.md).
