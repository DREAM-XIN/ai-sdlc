# Cross-repository GitHub installation

AI-SDLC can be consumed by another private GitHub repository without copying the Python control plane, schemas, validators or default Dispatch Policy into that repository.

The target repository keeps only its project-specific contract, durable state and small caller workflows. The trusted control implementation is downloaded as a versioned GitHub Action from `DREAM-XIN/ai-sdlc`.

## Private repository prerequisite

While `DREAM-XIN/ai-sdlc` is private, GitHub must be configured to share its actions with other private repositories owned by the same account.

In `DREAM-XIN/ai-sdlc`:

1. Open **Settings**.
2. Open **Actions → General**.
3. Find the **Access** section.
4. Select **Accessible from repositories owned by 'DREAM-XIN' user**.
5. Save the setting.

GitHub uses a short-lived scoped installation token to let the runner download the private action. A separate PAT is not required when private-action sharing is configured correctly.

Private action sharing has an important visibility consequence: collaborators who can view workflow logs in an allowed caller repository may indirectly see information emitted by the private action. Do not print private control-repository secrets or sensitive source content into logs.

A public caller repository cannot consume a private AI-SDLC action. Keep target repositories private until the AI-SDLC distribution model changes or the control action is published publicly.

## Minimal target repository

```text
my-project/
├── .ai-sdlc/
│   └── project.yaml
├── .github/
│   └── workflows/
│       ├── ai-sdlc-plan.yml
│       ├── ai-sdlc-bootstrap.yml
│       └── ai-sdlc-persist.yml
├── state/
│   ├── features/
│   └── events/
├── AGENTS.md
└── <project source>
```

The target repository does **not** copy:

- `scripts/`;
- `spec/`;
- `roles/`;
- `dispatch/default.yaml`;
- AI-SDLC Python dependencies.

The default Dispatch Policy and control code come from the pinned AI-SDLC Action.

## Install the Project Adapter

Start from `templates/project-adapter.yaml` and save it in the target repository as:

```text
.ai-sdlc/project.yaml
```

See `docs/project-adapter.md` for the schema and semantic rules.

## Install caller workflows

Copy the three files from `templates/github/` into the target repository's `.github/workflows/` directory.

You may install only the workflows you need. For example, a repository that allows humans to persist state through normal PRs may install only the read-only Plan workflow.

### Pin the AI-SDLC version

The templates use this development reference:

```yaml
uses: DREAM-XIN/ai-sdlc/.github/actions/control@main
```

For production use, replace `main` with an AI-SDLC release tag or, for the strongest immutability, a full commit SHA.

The caller controls the exact Action version. A target repository cannot replace AI-SDLC runtime code by modifying its own Feature branch.

## Permission separation

The installation deliberately uses separate workflows.

### Plan

```yaml
permissions:
  contents: read
```

Plan checkout also sets `persist-credentials: false`.

It can read a Feature Manifest and Project Adapter, compute the next Commander state, and produce:

- `commander-plan.json`;
- `commander-summary.md`;
- `chatgpt-web-prompts.txt`.

It cannot push repository changes.

### Bootstrap

```yaml
permissions:
  contents: write
```

Bootstrap can generate a Feature Manifest. Persistence is disabled by default and must be explicitly enabled.

Direct writes to the caller default branch are rejected unless `allow_default_branch=true` is explicitly supplied.

### Persist

```yaml
permissions:
  contents: write
```

Persist validates a Feature Event through the Event Inbox and Transition Engine. It runs dry by default. A real commit/push happens only when `dry_run=false`.

The called AI-SDLC Action cannot elevate `GITHUB_TOKEN` permissions. The caller workflow owns the token and its permission envelope.

## Recommended operating flow

### 1. Create a Feature branch

Create a non-default branch for AI-SDLC state and implementation work.

### 2. Add a Feature Bootstrap input

For example:

```yaml
version: 0.1.0
feature:
  id: F-123
  title: Add customer export
  risk: medium
  issue: '#123'
profile: standard-feature
created_at: '2026-08-07T13:00:00Z'
```

Commit it somewhere in the target repository, for example `state/bootstrap/F-123.yaml`.

### 3. Run `AI-SDLC Bootstrap`

First run with `persist=false` and inspect the generated Manifest artifact. Then persist to the Feature branch when the output is correct.

Canonical state path:

```text
state/features/F-123.yaml
```

### 4. Run `AI-SDLC Plan`

The Plan workflow reads the Feature Manifest and automatically loads `.ai-sdlc/project.yaml` when present.

For a ChatGPT Web/manual route, download or copy `chatgpt-web-prompts.txt` and give each dispatched prompt to the intended independent conversation/window.

### 5. Worker writes durable outputs

The worker must not edit the Feature Manifest directly. It should write the required artifact/evidence and a Feature Event such as:

```text
state/events/F-123/EVT-F123-REQ-DONE.yaml
```

### 6. Run `AI-SDLC Persist Event`

Run once with `dry_run=true` to inspect the Persistence Plan. Then run with `dry_run=false` to commit the validated Manifest transition to the Feature branch.

### 7. Run Plan again

Commander computes the next runnable stage and produces the next runtime decision or ChatGPT Web prompt.

## Trust boundary

```text
private AI-SDLC Action @ pinned ref
  ├── schemas
  ├── Commander
  ├── transition engine
  ├── persistence validator
  └── default Dispatch Policy
             │
             │ reads / validated writes
             ▼
caller repository checkout
  ├── .ai-sdlc/project.yaml
  ├── source code
  ├── artifacts/evidence
  └── state/
```

The composite Action:

- installs Python dependencies from its own downloaded AI-SDLC repository;
- executes Commander/validators from its own action repository;
- confines repository input paths to the caller workspace;
- rejects symlinked required input files and parent traversal;
- validates Feature Manifest writes under `state/features/`;
- validates write refs as branch names;
- denies default-branch writes unless explicitly allowed;
- does **not** execute the Project Adapter's build/test/lint commands.

Project commands remain portable data until an execution Runtime with an explicit sandbox policy consumes them.

## No hidden privilege escalation

GitHub associates a called/shared automation with the caller repository context. The caller's `GITHUB_TOKEN` is scoped to the caller repository, and downstream reusable automation can keep or reduce those permissions but cannot elevate them.

This is why Plan, Bootstrap and Persist remain separate caller workflows instead of sharing one permanently write-capable token.

## Current limitations

- The templates currently point to `@main` for development; a release tag/SHA should be used after the first release.
- Private Actions Access must be enabled manually in the `ai-sdlc` repository settings.
- ChatGPT Web remains a manual transport: the workflow produces prompts but does not automate browser tabs.
- Autonomous coding-agent runtimes are separate adapters and are not executed by this transport.
- Multi-repository central scheduling is a later layer; this feature lets each target repository consume the same control-plane implementation first.
