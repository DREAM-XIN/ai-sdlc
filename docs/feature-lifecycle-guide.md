# Feature Lifecycle Guide

This guide shows how to run a normal AI-SDLC Feature after the target repository is installed.

The end-to-end example is:

```text
Feature: F-DEMO-LOGIN-0001
Title:   Add user login
Branch:  feature/F-DEMO-LOGIN-0001
```

If your repository is not installed yet, start with [Set up a new project](new-project-setup.md).

## 1. Choose the workflow profile

AI-SDLC v0.2.0 includes multiple profiles. For everyday use, the first decision is usually between `standard-feature` and `small-change`.

### `standard-feature`

Use it for work that benefits from explicit requirement/design review and independent acceptance, such as:

- new business functionality;
- architectural changes;
- medium/high-impact behavior changes;
- security-sensitive changes;
- changes that need independent Requirement and Design review.

The current profile stages are exactly:

```text
requirement
  ↓
requirement-review        [requirement-gate]
  ↓
design
  ↓
design-review             [design-gate]
  ↓
plan
  ↓
implementation
  ↓
code-review               [code-gate]
  ↓
verification              [verification-gate]
  ↓
acceptance                [release-gate]
```

The roles are Product → Reviewer → Architect → Reviewer → Orchestrator → Developer → Reviewer → QA → Product.

### `small-change`

Use it only when the risk and scope genuinely justify the shorter low-risk profile, for example:

- a small low-risk bug fix;
- a narrow UI adjustment;
- a small configuration change;
- a genuinely small documentation correction;
- a low-risk CI fix.

The current profile stages are exactly:

```text
requirement
  ↓
implementation
  ↓
review                    [code-gate]
  ↓
verification              [verification-gate]
```

`small-change` does **not** have `design`, `design-review`, `plan`, `code-review`, or `acceptance` stages. Do not write prompts or events for stages that the selected profile does not contain.

For `F-DEMO-LOGIN-0001`, use `standard-feature`.

## 2. Create the Feature Issue

Create a normal GitHub Issue describing the user outcome and acceptance criteria.

Example title:

```text
[F-DEMO-LOGIN-0001] Add user login
```

Record the Issue reference in the Feature Bootstrap. GitHub is the durable system of record; do not keep the only copy of the requirement in a chat conversation.

## 3. Create the non-default Feature branch

Create a Feature integration branch from the repository's intended base:

```bash
git switch main
git pull --ff-only
git switch -c feature/F-DEMO-LOGIN-0001
git push -u origin feature/F-DEMO-LOGIN-0001
```

Do not Bootstrap or Persist a normal Feature directly onto the default branch.

The Feature branch is the durable integration branch for the entire Feature. It is **not** the same as an autonomous Developer worker branch. gh-aw workers create bounded branches and Draft PRs whose base is this Feature branch.

## 4. Add the Feature Bootstrap input

Create:

```text
state/bootstrap/F-DEMO-LOGIN-0001.yaml
```

Example:

```yaml
version: 0.1.0
feature:
  id: F-DEMO-LOGIN-0001
  title: Add user login
  risk: medium
  issue: '#123'
profile: standard-feature
created_at: '2026-08-09T08:00:00Z'
```

The bootstrap schema requires `version`, `feature`, `profile`, and `created_at`. `feature` requires `id`, `title`, and `risk`; `issue` is optional but strongly useful for the GitHub-native flow.

Commit the bootstrap input to the Feature branch.

## 5. Bootstrap the Feature Manifest

If `ai-sdlc-command.yml` is installed on the default branch, comment on the Feature Issue with this exact v0.2.0 command form:

```text
/ai-sdlc bootstrap target_ref=feature/F-DEMO-LOGIN-0001 bootstrap=state/bootstrap/F-DEMO-LOGIN-0001.yaml manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

The command bridge dispatches Bootstrap with persistence enabled and default-branch writes disabled.

After successful Bootstrap, the Feature branch contains:

```text
state/features/F-DEMO-LOGIN-0001.yaml
```

A new `standard-feature` Manifest starts at revision `0`, with:

```text
requirement: READY
later stages: TODO
all referenced Gates: PENDING
workflow.status: ACTIVE
```

Do not manually create a later-state Manifest to “save time”. Bootstrap derives the stage/Gate structure from the selected profile.

## 6. Plan before assigning a worker

Comment:

```text
/ai-sdlc plan target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

Plan is read-only. Commander reads the current Manifest, `.ai-sdlc/project.yaml`, durable project context, and trusted routing policy. It produces the current action and manual transport artifacts/prompts.

The important habit is:

> Plan from the real current Manifest; never assign a role from an old prompt that merely says what the revision “should” be.

At revision 0 the expected first action is Requirement / Product.

## 7. Start the current stage with a Feature Event

A worker should not directly rewrite the Feature Manifest from `READY` to `WORKING`.

Create an Event Inbox file:

```text
state/events/F-DEMO-LOGIN-0001/EVT-F-DEMO-LOGIN-0001-REQ-START.yaml
```

with:

```yaml
version: 0.1.0
id: EVT-F-DEMO-LOGIN-0001-REQ-START
feature_id: F-DEMO-LOGIN-0001
expected_revision: 0
occurred_at: '2026-08-09T08:05:00Z'
changes:
  - kind: stage
    id: requirement
    status: WORKING
```

For repository Event Inbox persistence:

- directory Feature id must equal `feature_id`;
- filename stem must equal `id`;
- include an explicit stable `id`;
- include `expected_revision` from the latest Manifest.

Persist that event. If it succeeds, revision becomes 1.

## 8. Complete Requirement as Product

The Product role writes the actual requirement artifact, for example:

```text
docs/features/F-DEMO-LOGIN-0001/requirement.md
```

Then prepare an event against the latest revision. A representative Requirement completion event can:

- register `requirement-v1` as a `draft` Artifact;
- move `requirement` from `WORKING` to `DONE`;
- make `requirement-review` `READY`.

Example:

```yaml
version: 0.1.0
id: EVT-F-DEMO-LOGIN-0001-REQ-DONE
feature_id: F-DEMO-LOGIN-0001
expected_revision: 1
occurred_at: '2026-08-09T08:20:00Z'
changes:
  - kind: artifact-record
    record:
      id: requirement-v1
      type: requirement
      uri: docs/features/F-DEMO-LOGIN-0001/requirement.md
      status: draft
  - kind: stage
    id: requirement
    status: DONE
  - kind: stage
    id: requirement-review
    status: READY
```

A newly registered Artifact is `draft`. It cannot be registered and approved in the same Feature Event; independent review gets a later revision.

Persist, then Plan again.

## 9. Requirement Review is independent

Open a new worker context whose only role is Requirement Reviewer. It must re-read:

- the current Manifest and revision;
- the Feature Issue;
- the requirement artifact;
- `AGENTS.md` and `.ai-sdlc/project.yaml`;
- any required project context.

The reviewer does not rewrite the requirement as Product. It produces review Evidence.

A passing review event can, in the same validated transition:

- register review Evidence;
- approve `requirement-v1`;
- set `requirement-gate: PASS` referencing that Evidence;
- complete `requirement-review`;
- make `design` READY.

A Gate verdict cannot be justified only by the author saying the artifact is correct.

Persist, then Plan again.

## 10. Design as Architect

The Architect reads the approved requirement and creates the design artifact, for example:

```text
docs/features/F-DEMO-LOGIN-0001/design.md
```

Follow the same lifecycle pattern:

```text
design READY
  -> START event
  -> design WORKING
  -> design artifact + DONE event
  -> design-review READY
```

Do not PASS `design-gate` from the Architect context.

## 11. Design Review is independent

Use a new Design Reviewer context. Review the design against the approved requirement and repository constraints.

A passing review supplies Evidence, approves the design Artifact, PASSes `design-gate`, completes `design-review`, and makes `plan` READY through the trusted event transition.

Persist and Plan again.

## 12. Plan as Orchestrator

The Orchestrator translates the approved requirement/design into a concrete implementation plan: work units, scope boundaries, deterministic checks, and Definition of Done.

The Plan role coordinates work. It does not become Developer, Reviewer, QA, or release authority simply because it knows the full workflow.

After a valid plan completion event, `implementation` becomes READY. Persist and Plan again.

## 13. Implement manually or autonomously

### Manual / ChatGPT Web

Use the Task Package/prompt produced by Plan. The Developer:

- reads required context;
- changes only allowed source/docs/tests;
- runs applicable deterministic commands;
- produces implementation evidence/artifacts;
- returns a proposed Feature Event/result for trusted persistence.

The Developer does not directly change authoritative lifecycle state.

### Autonomous gh-aw

For an eligible Developer work unit, comment:

```text
/ai-sdlc dispatch-gh-aw target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

Do not add provider/model/policy/worker flags; the command intentionally does not accept them.

The trusted flow is:

```text
Feature branch
  -> trusted Commander / Runtime Router
  -> gh-aw Developer
  -> bounded gh-aw/... implementation branch
  -> Draft PR
  -> base: feature/F-DEMO-LOGIN-0001
  -> Worker Result
  -> trusted Feature Event collector / Persist
```

The autonomous Developer cannot edit `state/features/**` or `state/events/**`, PASS a Gate, merge, or release.

See [Autonomous development](autonomous-development.md).

## 14. Finish Implementation without self-approving it

When implementation work is complete, the trusted lifecycle records implementation completion and makes `code-review` READY.

This does **not** mean:

```text
implementation DONE == code-gate PASS
```

It means the implementation is ready for an independent Code Reviewer.

If autonomous work produced a Draft PR to the Feature branch, review that actual PR/diff. If manual work was committed directly on the Feature branch, review the actual Feature changes.

## 15. Code Review is a separate stage

Open a new Code Reviewer context. It should inspect the real diff, approved requirement/design/plan, relevant tests and CI, and repository rules.

If review fails, record durable feedback/remediation rather than silently letting the Developer declare the Gate passed.

If review passes, the review Evidence can support:

```text
code-gate: PASS
code-review: DONE
verification: READY
```

Persist and Plan again.

## 16. Verification is QA-owned

QA independently verifies the built change against the requirement and repository validation commands.

Typical evidence can include:

- CI run/check results;
- test reports;
- build/lint/typecheck results;
- functional/manual verification records where deterministic automation is insufficient.

A Developer's claim that tests passed is not a substitute for the evidence expected by policy.

A passing Verification event supports:

```text
verification-gate: PASS
verification: DONE
acceptance: READY
```

Persist and Plan again.

## 17. Acceptance is Product-owned

Acceptance Product Owner verifies the user/business outcome, not just whether the code compiled.

For `Add user login`, acceptance should confirm that the delivered behavior satisfies the approved requirement and that the Feature is appropriate to release/merge under project policy.

A passing Acceptance event supplies Evidence and can set:

```text
release-gate: PASS
acceptance: DONE
```

When every stage is complete and all Gates are passing or validly waived, the transition engine recomputes:

```yaml
workflow:
  status: DONE
```

Do not manually set `workflow.status: DONE`.

## 18. Persist explicitly when needed

The exact Issue Comment form is:

```text
/ai-sdlc persist target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml event=state/events/F-DEMO-LOGIN-0001/EVT-F-DEMO-LOGIN-0001-REQ-DONE.yaml
```

The target `ai-sdlc-persist.yml` also listens for pushes that change `state/events/**/*.yaml` or `.yml` and resolves eligible Event Inbox updates automatically.

Important differences:

- the standalone manual Persist workflow has `dry_run: true` by default;
- the Issue Comment bridge dispatches explicit Persist with `dry_run=false`;
- pushed Event Inbox handling requests real persistence when the event is eligible;
- every real write still checks lifecycle revision and Git branch freshness.

If Persist rejects an event, fix the actual cause; do not edit the authoritative Manifest around the validator.

## 19. Re-plan after every durable transition

The most important daily operating rule is:

```text
Persist -> read new Manifest -> Plan again
```

Why:

- revision has changed;
- the next stage may differ from your expectation;
- a Gate may still be `PENDING`;
- remediation work may be active;
- another worker may have advanced state;
- Runtime Router may choose a different execution path for the next work unit.

Never keep running a chain of prompts from a stale revision.

## 20. Handle revision conflicts correctly

Suppose two workers both read revision 12:

```text
worker A event expected_revision=12 -> applied -> revision 13
worker B event expected_revision=12 -> stale
```

Worker B must re-read revision 13 and decide whether its result is still valid. If yes, create a new event based on revision 13. Do not change only `expected_revision` without re-validating the assumptions.

See [Optimistic concurrency](optimistic-concurrency.md).

## 21. Merge only after the Feature is complete and repository checks allow it

At the end, verify the Feature branch Manifest says `workflow.status: DONE` and the Feature PR satisfies normal branch protection / required checks.

Then merge the **Feature branch PR** into the target base branch according to repository policy.

An autonomous worker Draft PR is normally an implementation contribution into the Feature branch; it is not automatically the final Feature-to-main PR.

## Daily command reference

These are the exact v0.2.0 Issue Comment command shapes:

```text
/ai-sdlc bootstrap target_ref=<branch> bootstrap=state/bootstrap/<file>.yaml manifest=state/features/<file>.yaml
/ai-sdlc plan target_ref=<branch> manifest=state/features/<file>.yaml
/ai-sdlc persist target_ref=<branch> manifest=state/features/<file>.yaml event=state/events/<feature-id>/<file>.yaml
/ai-sdlc dispatch-gh-aw target_ref=<branch> manifest=state/features/<file>.yaml
```

The command bridge accepts trusted comments from an `OWNER`, `MEMBER`, or `COLLABORATOR`. It rejects the default branch for Bootstrap, Persist, and gh-aw dispatch and rejects parent-traversal paths.

## First Feature checklist

- [ ] Feature Issue exists.
- [ ] Correct profile chosen.
- [ ] Non-default Feature branch exists.
- [ ] Bootstrap input committed.
- [ ] Bootstrap created revision 0 Manifest.
- [ ] Plan read the real current state.
- [ ] Every active stage moved through legal events rather than direct Manifest edits.
- [ ] Requirement and Design were independently reviewed for `standard-feature`.
- [ ] Implementation did not self-PASS Code Gate.
- [ ] Verification was independent.
- [ ] Acceptance was independent for `standard-feature`.
- [ ] Gate verdicts reference durable Evidence.
- [ ] Stale events were regenerated only after re-reading state.
- [ ] Final Manifest says `workflow.status: DONE`.
- [ ] Feature PR and required repository checks are merge-ready.

Next: [Role guide](role-guide.md) for copyable worker prompts, or [Troubleshooting](troubleshooting.md) if the lifecycle does not advance as expected.
