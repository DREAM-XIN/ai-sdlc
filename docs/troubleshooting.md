# Troubleshooting and FAQ

Use this guide when an AI-SDLC installation, Feature transition, or autonomous dispatch does not behave as expected.

Start from the symptom, then re-read the authoritative GitHub state. Do not repair lifecycle problems by directly editing `state/features/**` around the validators.

## Installation Check fails

The v0.2.0 Installation Check deliberately fails closed. Common causes include:

- invalid `.ai-sdlc/project.yaml` schema/semantics;
- repository identity mismatch;
- missing `AGENTS.md`;
- missing `context.rules` / `context.read` files;
- invalid command working directory;
- unresolved AI-SDLC Action placeholder;
- moving AI-SDLC Action ref.

Read the workflow summary/error first. The detailed validation contract is in [Cross-repository installation preflight](cross-repository-installation-preflight.md).

### Public target note

While the control repository is private, a public target cannot execute the installed `ai-sdlc-installation-check.yml` because that workflow consumes a private shared Action. Use the implemented deterministic `scripts/validate_target_installation.py` from a reviewed control checkout against the target workspace before the first Feature, then use the public-safe command bridge for Bootstrap/Plan/Persist.

See [Set up a new project](new-project-setup.md) and [Public target lifecycle transport](public-target-lifecycle-transport.md).

## `.ai-sdlc/project.yaml` repository identity does not match

Symptom: installation or cross-repository dispatch reports a repository/default-branch mismatch.

Check:

```yaml
repository:
  provider: github
  full_name: owner/repository
  default_branch: main
```

`full_name` must match the actual caller/target repository in `owner/repo` form. `default_branch` must match GitHub's current default branch.

If the repository was renamed or the default branch changed, update the Project Adapter through normal review and re-run installation validation. Do not disable identity checking.

See [Project Adapter](project-adapter.md).

## `AGENTS.md` is missing

The installed cross-repository contract requires a repository-wide `AGENTS.md`, and `.ai-sdlc/project.yaml` must include it under:

```yaml
context:
  rules:
    - AGENTS.md
```

Create the file with the project's durable worker rules, commit it, then re-run Installation Check.

Do not create an empty placeholder just to pass validation; the file is part of the worker contract.

## A `context.rules` or `context.read` path is missing

Every path listed in those arrays is required in the current Project Adapter protocol and must resolve to a regular non-symlink file within the workspace.

If a document is optional, remove it from the list until it actually exists. v0.2.0 does not have an optional-context path representation.

## Caller workflow still contains `REPLACE_WITH_AI_SDLC_FULL_SHA`

This is an intentionally invalid installation marker.

Replace **every** occurrence with one reviewed 40-character commit SHA, for example the published v0.2.0 baseline:

```text
44e68d4ec6517135b0008ba4cf14fdb625f9481d
```

Then re-run installation validation.

Search both `.yml` and `.yaml` caller files. The validator checks AI-SDLC caller workflows under `.github/workflows/ai-sdlc-*`.

## A caller uses `@main`, `@v0.2.0`, or another moving ref

That is invalid for installed AI-SDLC Action references.

Use a reviewed full Git commit SHA:

```yaml
uses: DREAM-XIN/ai-sdlc/.github/actions/control@<40-character-sha>
```

Do not solve private/public transport problems by switching to `@main`. Moving refs break the reviewed immutable control-plane boundary.

See [Security model](security-model.md).

## Private target cannot consume the AI-SDLC Action

When `DREAM-XIN/ai-sdlc` is private, the control repository must allow the intended private repositories to consume its shared Actions.

Check the control repository's **Settings → Actions → General → Access** policy and repository ownership/access.

The private lifecycle path does not require a broad PAT simply to download the shared Action.

See [Cross-repository installation](cross-repository-installation.md).

## Public target cannot run Bootstrap/Plan/Persist through installed private Actions

This is expected while the control repository is private. GitHub does not permit a public caller to download a private Action/reusable workflow.

Use the installed public-safe `ai-sdlc-command.yml`. For public repositories, it routes Bootstrap/Plan/Persist to the trusted private control workflow, which writes back through the exact-target Runtime GitHub App.

Required target secret:

```text
AI_SDLC_CONTROL_DISPATCH_TOKEN
```

Required trusted-control Runtime App configuration:

```text
AI_SDLC_RUNTIME_APP_CLIENT_ID
AI_SDLC_RUNTIME_APP_PRIVATE_KEY
```

See [Public target lifecycle transport](public-target-lifecycle-transport.md).

## Feature revision conflict / stale event

Typical error:

```text
stale event revision: manifest=13 event_expected=12
```

Another valid event advanced the Manifest after your worker read revision 12.

Correct recovery:

1. re-read the current Manifest from the Feature branch;
2. re-read any changed Artifact/Gate/task context;
3. decide whether the worker's old result is still valid;
4. if valid, create a **new** event using the current revision;
5. Persist the new event.

Do not mechanically change only `expected_revision` from 12 to 13. The state may have changed in a way that invalidates the old result.

See [Optimistic concurrency](optimistic-concurrency.md).

## `expected_revision` is missing

The generic low-level protocol retains compatibility with older events, but the repository Event Inbox for new persistence operations requires explicit optimistic concurrency information.

Add the current revision deliberately:

```yaml
expected_revision: <current-manifest-revision>
```

Also ensure the event has an explicit stable `id`.

See [Feature Bootstrap and Event Inbox](feature-bootstrap-event-inbox.md).

## Event Inbox path or id is rejected

Repository events must be shaped as:

```text
state/events/<feature-id>/<event-id>.yaml
```

Check all three identities:

- directory name equals `event.feature_id`;
- filename stem equals `event.id`;
- `event.feature_id` equals the Manifest Feature id.

Also reject parent-traversal/absolute-path workarounds. Put the event in the expected durable location.

## Persist did not apply the event

First determine which transport you used.

### Manual workflow dispatch

`ai-sdlc-persist.yml` defaults to:

```text
dry_run: true
```

A dry run validates/materializes without committing the authoritative Manifest update. If you need a real manual persistence run, explicitly use the non-dry-run option under your repository's normal trust/permission process.

### Issue Comment command

The command bridge form:

```text
/ai-sdlc persist target_ref=<branch> manifest=state/features/<file>.yaml event=state/events/<feature-id>/<file>.yaml
```

dispatches Persist with real persistence enabled and default-branch writes disabled.

### Event push

The installed Persist workflow listens for pushes to:

```text
state/events/**/*.yaml
state/events/**/*.yml
```

It resolves the pushed event and applies it when eligible. Already-applied archived events become a no-op rather than being replayed.

If no mutation occurred, inspect:

- event path/id;
- `expected_revision`;
- transition legality;
- Feature id;
- target branch;
- whether the event was already applied;
- Git remote branch freshness;
- workflow conclusion/logs.

## Persist reports a stale Git branch / remote write precondition failure

AI-SDLC protects two different freshness boundaries:

```text
Feature revision
    -> semantic lifecycle freshness

Git branch SHA
    -> repository write freshness
```

A checked-out branch can become stale after event validation. Write-capable transports verify the local checkout HEAD still equals the live remote target ref immediately before persistence.

Recovery:

1. fetch/re-read the latest Feature branch;
2. re-run Plan or persistence validation from current state;
3. regenerate the event if the Manifest revision changed;
4. retry through the trusted persistence path.

Do not force-push around the precondition.

## Current stage is not `READY`

Read:

```text
state/features/<feature-id>.yaml
```

and re-run Plan.

Common cases:

- stage is `WORKING`: a worker is already in progress; Commander may return `WAIT`;
- earlier dependency is not complete;
- a review remediation task has priority;
- a Gate is still pending/failing;
- workflow is `BLOCKED`;
- your prompt is stale.

Do not manually mark a later stage `READY` because you want to work on it.

## Stage is `WORKING` and autonomous dispatch will not start

The autonomous gateway has a narrow adoption/resume rule for pre-existing `WORKING` state. It is allowed only when trusted Commander recomputes `WAIT`, exactly one current Feature stage is in progress, that stage matches `workflow.current_stage`, and Runtime Router still resolves it to `gh-aw/autonomous`.

If those conditions do not hold, fix the lifecycle/routing cause rather than writing a duplicate START event or forcing dispatch.

See [Cross-repository autonomous execution](cross-repository-autonomous-execution.md).

## Gate is still `PENDING`

A stage author completing work does not automatically PASS the associated Gate.

For example:

```text
implementation DONE
```

does not imply:

```text
code-gate PASS
```

The appropriate independent review/QA/acceptance role must produce durable Evidence, and a valid Feature Event must reference that Evidence when changing the Gate.

If a Gate is `PENDING`:

1. confirm the associated review stage is current/eligible;
2. run the independent role;
3. persist its Evidence and Gate verdict;
4. Plan again.

Do not directly edit the Gate field in the Manifest.

## A stage is `DONE` but its Gate is not PASS/WAIVED

The Manifest semantic validator rejects a gated stage that is `DONE` while its Gate remains non-passing.

A valid review completion event should include the Evidence-supported Gate verdict and stage completion in the same legal transition (or have the passing Gate already persisted).

Do not commit an internally inconsistent Manifest.

## Autonomous Issue Comment is ignored or rejected

Check the exact command form:

```text
/ai-sdlc dispatch-gh-aw target_ref=<feature-branch> manifest=state/features/<feature-id>.yaml
```

Then check:

- the comment starts exactly with `/ai-sdlc `;
- command arguments match the supported form;
- commenter association is `OWNER`, `MEMBER`, or `COLLABORATOR`;
- `target_ref` is not the default branch;
- paths contain no parent traversal;
- `ai-sdlc-command.yml` is installed on the default branch;
- `AI_SDLC_CONTROL_DISPATCH_TOKEN` is available for trusted control dispatch.

The command intentionally cannot take provider/model/policy/worker selectors.

## `AI_SDLC_CONTROL_DISPATCH_TOKEN` is missing or unusable

This target-repository credential is needed when the command bridge must dispatch/read workflows in the trusted private control repository, including autonomous dispatch and public-target lifecycle transport.

Check that the credential:

- exists in the target repository secret store;
- is scoped to `DREAM-XIN/ai-sdlc` only where possible;
- can dispatch and read the required Actions workflows;
- has metadata access required by the chosen credential type;
- is not expected to provide target source-write access.

Do not broaden it into a classic all-repositories PAT just to make the error disappear.

## Runtime GitHub App is not installed on the target

Symptoms include failure to mint an exact-target installation token or a Cross-Repo Runtime Preflight failure.

In the trusted control repository, confirm:

```text
AI_SDLC_RUNTIME_APP_CLIENT_ID
AI_SDLC_RUNTIME_APP_PRIVATE_KEY
```

are configured, then confirm the Runtime GitHub App is installed on the exact intended target repository with the minimum required permissions.

Run:

```text
AI-SDLC gh-aw Cross-Repo Runtime Preflight
```

for that target. A `READY` result proves the exact-target read transport only; it does not prove provider readiness or implementation write/PR success.

## Cross-Repo Runtime Preflight passes but autonomous execution still fails

The Runtime App preflight and the execution-engine preflight test different things.

Run/check:

```text
AI-SDLC gh-aw Runtime Preflight
```

for engine/profile/provider readiness.

Possible causes after App transport succeeds include:

- engine lock/profile mismatch;
- provider credential missing;
- provider entitlement/quota/availability issue;
- compiled worker not allowed/materialized;
- runtime routing not selecting gh-aw for the current work unit.

Do not assume Runtime App `READY` means the AI provider is ready.

See [`runtimes/gh-aw/README.md`](../runtimes/gh-aw/README.md) and [gh-aw integration](integrations/gh-aw.md).

## AI provider / engine preflight fails

Treat this as an autonomous execution backend problem, not a reason to alter the Feature Manifest.

Check trusted control configuration for the selected engine/profile and provider credentials according to the runtime documentation. Provider/model selection is not supplied by the target Issue Comment.

You can continue the Feature through the manual/ChatGPT Web runtime if policy/routing allows; do not fake an autonomous success event.

## Draft PR exists but Feature Manifest did not update

A gh-aw Draft PR proves source output was published. It does not itself prove lifecycle persistence completed.

Expected flow:

```text
Draft PR
  -> Worker Result
  -> trusted collector
  -> Feature Event
  -> Persist
  -> Manifest revision advances
```

Check:

- worker run conclusion;
- Worker Result/receipt correlation;
- collector execution;
- generated event revision;
- Persist conclusion;
- whether the target branch advanced and caused a stale precondition.

Do **not** manually edit `state/features/<feature>.yaml` to match the Draft PR. Fix/retry the trusted result/persistence path, then re-run Plan.

## Autonomous worker changed `state/features/**` or `state/events/**`

That violates the worker boundary. The trusted worker path is expected to reject such a diff.

Do not merge the Draft PR. Review the scope/routing/worker result and remove lifecycle-state changes from the implementation contribution. Lifecycle state must come through the collector/Persist path.

See [Autonomous development](autonomous-development.md) and [Security model](security-model.md).

## Why can't the Developer PASS Code Gate?

Because implementation and approval are different authorities.

A Developer can provide implementation results and test evidence, but an independent Code Reviewer checks the real diff against approved requirements/design/plan and produces the Evidence used for `code-gate`.

This prevents a worker from turning “I finished” into “my work is approved.”

## Why is Verification still required after Code Review?

Code Review and Verification answer different questions.

- Code Review: is the change correct, maintainable, safe, scoped, and consistent with the approved design based on review of the implementation?
- Verification: does the delivered behavior actually pass the required deterministic/functional checks independently?

Passing Code Gate does not automatically PASS Verification Gate.

For `standard-feature`, Product Acceptance is separate again: it asks whether the verified change satisfies the intended user/business outcome and Release Gate criteria.

## Why can't I edit the Manifest directly if I know the correct state?

Because the Manifest is authoritative lifecycle state, and direct worker writes bypass:

- schema/semantic transition validation;
- event replay protection;
- `expected_revision` optimistic concurrency;
- Evidence requirements for Gates;
- Git branch freshness checks;
- durable audit history.

Workers propose facts/events. Trusted persistence decides whether those facts are valid lifecycle transitions.

## My prompt says revision 10, but GitHub says revision 12

GitHub wins.

The prompt is stale context. Re-read the Manifest and all artifacts/events added since revision 10. Re-run Plan before continuing.

Never tell a new role to “assume revision 10” when the repository can be queried directly.

## `standard-feature` prompt expects a stage that is not present

Check the actual selected profile in the Manifest.

`standard-feature` stages:

```text
requirement -> requirement-review -> design -> design-review -> plan -> implementation -> code-review -> verification -> acceptance
```

`small-change` stages:

```text
requirement -> implementation -> review -> verification
```

A `small-change` Task Package must not inherit design/plan/code-review/acceptance assumptions from `standard-feature`.

## Feature says `DONE` — can the autonomous worker merge now?

No. `DONE` is lifecycle state. Normal repository merge policy and branch protection still apply.

The final Feature-to-base-branch PR should be merged by the repository's authorized merge process. Autonomous Developer workers are not granted merge/release authority by `workflow.status: DONE`.

## Diagnostic order

When unsure, use this order:

1. Open the real Feature Issue.
2. Read the Feature branch's latest `state/features/<feature>.yaml`.
3. Check `revision`, `workflow.status`, `current_stage`, current stage status, and Gates.
4. Read the most recent Event Inbox files and `applied_events`.
5. Re-run Plan.
6. Inspect the relevant caller/control workflow run and receipt.
7. For source work, inspect the actual PR/diff and CI.
8. For autonomous work, separately check control-dispatch transport, Runtime App preflight, and engine/provider preflight.
9. Fix the failing boundary; do not bypass it with direct Manifest edits.

## Deep references

- [Getting Started](getting-started.md)
- [Set up a new project](new-project-setup.md)
- [Feature lifecycle guide](feature-lifecycle-guide.md)
- [Role guide](role-guide.md)
- [Autonomous development](autonomous-development.md)
- [Project Adapter](project-adapter.md)
- [Feature Bootstrap and Event Inbox](feature-bootstrap-event-inbox.md)
- [Optimistic concurrency](optimistic-concurrency.md)
- [Cross-repository installation](cross-repository-installation.md)
- [Cross-repository installation preflight](cross-repository-installation-preflight.md)
- [Public target lifecycle transport](public-target-lifecycle-transport.md)
- [Cross-repository autonomous execution](cross-repository-autonomous-execution.md)
- [Security model](security-model.md)
