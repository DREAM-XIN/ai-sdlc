# Getting Started with AI-SDLC v0.2.0

This is the starting point if you have a GitHub repository and want to use AI-SDLC without first reading the control-plane architecture.

The published v0.2.0 baseline is:

```text
44e68d4ec6517135b0008ba4cf14fdb625f9481d
```

Use a **reviewed full 40-character commit SHA** for every installed AI-SDLC Action reference. Never replace the pin with `main`, a release branch, or a moving tag.

## The shortest complete path

```text
Create GitHub repository
        ↓
Initialize source / README / CI
        ↓
Create AGENTS.md
        ↓
Create .ai-sdlc/project.yaml
        ↓
Install AI-SDLC caller workflows
        ↓
Pin AI-SDLC to a reviewed full commit SHA
        ↓
Configure credentials required by your transport
        ↓
Installation Check
        ↓
Create Feature Issue
        ↓
Create non-default Feature branch
        ↓
Create Feature Bootstrap input
        ↓
Bootstrap
        ↓
Feature Manifest
        ↓
Plan
        ↓
Run the role for the current stage
        ↓
Artifact / Evidence / Feature Event
        ↓
Persist
        ↓
Plan again
        ↓
Next stage
        ↓
...
        ↓
workflow.status: DONE
        ↓
Merge the Feature branch
```

If you are setting up a repository now, go to [Set up a new project](new-project-setup.md). If installation is already complete, go directly to [Run your first Feature](feature-lifecycle-guide.md).

## 1. Know which repository owns what

AI-SDLC separates a **trusted control plane** from your **target repository**.

### Trusted control plane

For this project the control repository is:

```text
DREAM-XIN/ai-sdlc
```

It owns the trusted lifecycle implementation: schemas, Commander, transition validation, persistence rules, default routing policy, and autonomous runtime policy.

### Target repository

Your project repository keeps only project and Feature-specific durable facts, for example:

```text
my-project/
├── .ai-sdlc/project.yaml
├── .github/workflows/ai-sdlc-*.yml
├── state/bootstrap/
├── state/features/
├── state/events/
├── AGENTS.md
├── README.md
└── project source
```

Do **not** copy the whole AI-SDLC control repository into each project. Keeping the control plane separate means a Feature branch cannot silently replace the schemas, transition engine, Gate rules, or trusted runtime that validate its own work.

See [Cross-repository installation](cross-repository-installation.md) for the detailed trust boundary.

## 2. Treat GitHub as the durable system of record

A model conversation is temporary context. GitHub is where AI-SDLC keeps facts that must survive different workers, browser windows, retries, and runtimes.

The important durable objects are:

- **Feature Issue** — human-readable intent and discussion.
- **Feature branch** — durable integration branch for the Feature.
- **Feature Bootstrap** — input used to create the initial lifecycle state.
- **Feature Manifest** — authoritative current lifecycle state.
- **Artifacts** — requirements, designs, plans, implementation records, reviews, and similar outputs.
- **Evidence** — durable facts used to support reviews and Gates, such as review records or CI results.
- **Feature Events** — proposed lifecycle changes prepared against a specific Manifest revision.
- **Pull requests and CI** — source changes and independent engineering evidence.

A new worker should read these facts from GitHub instead of trusting an old prompt that says, for example, “revision should be 8”.

## 3. Understand the Feature Manifest

After Bootstrap, the authoritative state lives at:

```text
state/features/<feature-id>.yaml
```

A Manifest records, among other things:

- `revision` — monotonically increasing lifecycle revision;
- `workflow.profile` — for example `standard-feature`;
- `workflow.current_stage`;
- stage statuses such as `READY`, `WORKING`, and `DONE`;
- Gate statuses such as `PENDING` and `PASS`;
- registered Artifacts and Evidence;
- already-applied Feature Event ids.

Ordinary workers do **not** directly edit this file to claim progress. They produce work and submit a Feature Event; trusted persistence validates the change and updates the Manifest.

See [Feature Bootstrap and Event Inbox](feature-bootstrap-event-inbox.md).

## 4. Understand Feature Events and Persist

A Feature Event is the durable request to change lifecycle state. A repository Event Inbox event looks like this:

```yaml
version: 0.1.0
id: EVT-F-DEMO-LOGIN-0001-REQ-START
feature_id: F-DEMO-LOGIN-0001
expected_revision: 0
occurred_at: '2026-08-09T08:00:00Z'
changes:
  - kind: stage
    id: requirement
    status: WORKING
```

The important field is:

```yaml
expected_revision: 0
```

Persist validates the event against the latest Manifest and applies it only if the revision and transition are valid. A successful event increments the revision exactly once. A stale event is rejected; do not “fix” it by changing only the revision number. Re-read the current state and confirm that the event is still valid.

See [Optimistic concurrency](optimistic-concurrency.md).

## 5. Understand Gates

Stages describe work. Gates describe independent authority to accept or reject evidence at important boundaries.

For the default `standard-feature` profile, the review stages carry these Gates:

```text
requirement-review -> requirement-gate
design-review      -> design-gate
code-review        -> code-gate
verification       -> verification-gate
acceptance         -> release-gate
```

A Gate verdict requires Evidence. A Developer saying “my implementation is good” is not permission to set `code-gate: PASS`.

The same worker should not complete implementation and then automatically:

- approve its own implementation;
- PASS or waive its own Gate;
- skip Verification;
- directly rewrite the authoritative Manifest;
- merge the Feature;
- release it.

Use independent role contexts as described in [Role guide](role-guide.md).

## 6. Install the target contract

Before the first Feature, install:

- `AGENTS.md`;
- `.ai-sdlc/project.yaml`;
- the required caller workflows from `templates/github/`;
- an immutable AI-SDLC full-SHA pin;
- the credentials required by your public/private and manual/autonomous mode.

Then run the installation preflight appropriate to your repository transport.

The exact steps are in [Set up a new project](new-project-setup.md).

## 7. Create a Feature, not an ad-hoc branch of lifecycle state

For a normal Feature:

1. Create the GitHub Feature Issue.
2. Choose the workflow profile.
3. Create a **non-default Feature branch**, for example:

   ```text
   feature/F-DEMO-LOGIN-0001
   ```

4. Add:

   ```text
   state/bootstrap/F-DEMO-LOGIN-0001.yaml
   ```

5. Bootstrap the Manifest.
6. Run Plan.
7. Execute only the role and stage returned by current state.

The Feature branch is the long-lived Feature integration branch. An autonomous Developer may additionally use a bounded implementation branch such as `gh-aw/...`; that worker branch is **not** the Feature branch.

## 8. Bootstrap and Plan from the Feature Issue

If you installed `ai-sdlc-command.yml`, the v0.2.0 Issue Comment forms are:

```text
/ai-sdlc bootstrap target_ref=feature/F-DEMO-LOGIN-0001 bootstrap=state/bootstrap/F-DEMO-LOGIN-0001.yaml manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

Then:

```text
/ai-sdlc plan target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

Plan is deliberately read-only. It reads the current Manifest and Project Adapter, computes the next action, and produces Commander output / manual prompts. It does not let a worker invent a later stage.

See [Feature lifecycle guide](feature-lifecycle-guide.md) for the complete `F-DEMO-LOGIN-0001` walkthrough.

## 9. Run one stage, persist facts, then plan again

The normal operating loop is:

```text
Plan
  ↓
Read current revision + Task Package
  ↓
Perform only the assigned role
  ↓
Produce Artifact / Evidence
  ↓
Prepare Feature Event with expected_revision=N
  ↓
Persist
  ↓
Manifest becomes N+1
  ↓
Plan again
```

With the Issue Command Bridge, an explicit Persist command is:

```text
/ai-sdlc persist target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml event=state/events/F-DEMO-LOGIN-0001/<event-id>.yaml
```

The installed Persist workflow can also resolve pushed Event Inbox files. Either way, the trusted persistence path owns authoritative Manifest mutation.

## 10. Choose manual or autonomous execution

### Manual / ChatGPT Web

Commander can produce a portable Task Package / prompt. A human or ChatGPT Web performs the work and returns durable artifacts, evidence, and a proposed Feature Event. Lifecycle authority remains in the trusted persistence path.

### Autonomous gh-aw

An eligible Developer work unit can be dispatched with:

```text
/ai-sdlc dispatch-gh-aw target_ref=feature/F-DEMO-LOGIN-0001 manifest=state/features/F-DEMO-LOGIN-0001.yaml
```

The trusted runtime creates bounded implementation work and a Draft PR back to the Feature branch. The Developer still cannot PASS Gates, merge, release, or directly change authoritative lifecycle state.

See [Autonomous development](autonomous-development.md).

## 11. Finish the lifecycle before merging

For `standard-feature`, keep iterating until the authoritative Manifest says:

```yaml
workflow:
  status: DONE
```

`DONE` means all lifecycle stages are complete and all required Gates are passing or explicitly waived under policy. It does **not** mean a Developer simply finished coding.

After the Feature is `DONE`, repository branch protection and normal merge policy still apply. Merge the Feature branch only when its PR and required checks are ready.

## Where to go next

- [Set up a new project](new-project-setup.md) — install AI-SDLC in a target repository.
- [Run your first Feature](feature-lifecycle-guide.md) — complete `F-DEMO-LOGIN-0001` end to end.
- [Role guide](role-guide.md) — choose one AI/human role per worker context.
- [Autonomous development](autonomous-development.md) — enable the trusted gh-aw Developer path.
- [Troubleshooting](troubleshooting.md) — diagnose installation, revision, Persist, Gate, and runtime failures.

For implementation details, use the existing deep references instead of treating this guide as a protocol specification: [Project Adapter](project-adapter.md), [Security model](security-model.md), [Optimistic concurrency](optimistic-concurrency.md), and [Cross-repository installation](cross-repository-installation.md).
