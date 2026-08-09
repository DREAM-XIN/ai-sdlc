# Set Up a New Project

Use this guide when you have a GitHub repository that does not yet use AI-SDLC.

For AI-SDLC v0.2.0, the published release baseline is:

```text
44e68d4ec6517135b0008ba4cf14fdb625f9481d
```

The examples below assume `DREAM-XIN/ai-sdlc` is the trusted control repository. Review the commit you intend to run and use its full 40-character SHA in installed Action references.

## Target repository layout

A fully installed target normally looks like this:

```text
my-project/
├── .ai-sdlc/
│   └── project.yaml
├── .github/
│   └── workflows/
│       ├── ai-sdlc-installation-check.yml
│       ├── ai-sdlc-bootstrap.yml
│       ├── ai-sdlc-plan.yml
│       ├── ai-sdlc-persist.yml
│       └── ai-sdlc-command.yml
├── state/
│   ├── bootstrap/
│   ├── features/
│   └── events/
├── AGENTS.md
├── README.md
└── project source
```

`ai-sdlc-command.yml` is optional at the protocol level, but install it if you want the Issue Comment commands used throughout the end-user guides. It is also the public-safe command surface when a public target must call a private control repository.

The target repository does **not** copy `scripts/`, `spec/`, `roles/`, the trusted default Dispatch Policy, compiled gh-aw workers, or the rest of the AI-SDLC control implementation.

## 1. Initialize the project first

Before installing AI-SDLC, create enough real project structure for your engineering contract to be meaningful:

- source directories;
- `README.md`;
- the project's normal build/test/lint configuration;
- normal CI where appropriate.

AI-SDLC coordinates your engineering process; it is not a replacement for the project's source layout or deterministic tests.

## 2. Create `AGENTS.md`

Create a repository-wide `AGENTS.md` containing the durable rules that every AI/human worker must follow.

Typical content includes:

- repository purpose;
- supported language/runtime versions;
- build/test/lint expectations;
- directory ownership or important boundaries;
- generated files that must not be edited;
- security or dependency rules;
- definition-of-done rules that are specific to the project.

The installed cross-repository contract requires repository-wide `AGENTS.md`, and the Project Adapter must list it under `context.rules`.

Do not put credentials in `AGENTS.md`.

## 3. Create `.ai-sdlc/project.yaml`

Start from `templates/project-adapter.yaml`. A minimal example is:

```yaml
version: 0.1.0
project:
  id: my-project
  name: My Project
  description: Short project description.
repository:
  provider: github
  full_name: owner/my-project
  default_branch: main
defaults:
  workflow_profile: standard-feature
  runtime_policy: default
  required_commands:
    - test
context:
  rules:
    - AGENTS.md
  read:
    - README.md
commands:
  - id: test
    purpose: test
    argv: [npm, test]
    cwd: .
ownership:
  - id: application
    role: developer
    roots:
      - src
```

Adapt the command and ownership values to your repository; do not copy an `npm test` example into a non-Node project.

### `repository`

Keep these values accurate:

```yaml
repository:
  full_name: owner/my-project
  default_branch: main
```

Cross-repository validation compares them with the live GitHub repository identity. A rename or default-branch change must be reflected here.

### `context.rules`

These are normative worker rules. In the current installation contract, include:

```yaml
context:
  rules:
    - AGENTS.md
```

Every listed path must exist as a regular non-symlink file inside the repository workspace.

### `context.read`

These are durable project documents workers should read for context, for example:

```yaml
context:
  read:
    - README.md
    - docs/architecture.md
```

Do not list a file that does not exist. `context.read` has no optional-path representation in the current protocol.

### `commands`

Commands are data, not shell snippets. Each command uses an argument vector:

```yaml
commands:
  - id: test
    purpose: test
    argv: [python, -m, pytest]
    cwd: .
```

The Project Adapter does not execute arbitrary shell itself. Runtimes decide how to execute approved deterministic commands under their sandbox/policy.

### `ownership`

Ownership roots limit which paths a role can own or edit. Avoid overlapping roots assigned to different owners unless the boundary is deliberately marked shared under the Project Adapter rules.

For the complete field semantics, see [Project Adapter](project-adapter.md).

## 4. Install the caller workflows

Copy the current templates from `templates/github/` into the target repository using these exact target names:

```text
.github/workflows/ai-sdlc-installation-check.yml
.github/workflows/ai-sdlc-bootstrap.yml
.github/workflows/ai-sdlc-plan.yml
.github/workflows/ai-sdlc-persist.yml
.github/workflows/ai-sdlc-command.yml
```

The first four templates contain AI-SDLC Action installation placeholders. `ai-sdlc-command.yml` is a small Issue Comment bridge that dispatches the installed or trusted control workflows; it does not embed the private lifecycle Action itself.

See `templates/github/README.md` for the current template contract.

## 5. Replace every AI-SDLC installation placeholder

The lifecycle templates intentionally contain an invalid marker like:

```yaml
uses: DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA # ai-sdlc-install-placeholder
```

Replace every `REPLACE_WITH_AI_SDLC_FULL_SHA` with the **same reviewed 40-character commit SHA**.

For the published v0.2.0 baseline that would be:

```yaml
uses: DREAM-XIN/ai-sdlc/.github/actions/control@44e68d4ec6517135b0008ba4cf14fdb625f9481d # ai-sdlc-install-placeholder
```

Use the full commit identity only after you have decided that this is the reviewed baseline you want to execute.

Do not use:

```text
@main
@master
@v0.2.0
@release/v0.2
@latest
```

A moving ref lets different code execute later without a target-repository workflow change. Immutable pins are part of the AI-SDLC security boundary.

## 6. Choose the transport: Private target or Public target

This distinction matters while `DREAM-XIN/ai-sdlc` is private.

### Private target repository

A private target owned by an allowed owner can consume the shared private AI-SDLC Action when the control repository permits that private Action access.

In the control repository, the relevant GitHub setting is **Settings → Actions → General → Access**. Allow the intended repositories owned by the approved owner.

The installed lifecycle callers then use the pinned control Action directly:

```text
private target
  -> installed caller workflow
  -> private AI-SDLC Action @ full SHA
  -> target Feature branch
```

Plan uses read-only contents permission; Bootstrap and Persist have separate write-capable envelopes for their allowed operations.

### Public target repository while control repository is private

GitHub does not allow a public caller to download an Action/reusable workflow from a private repository. Do not try to make this work with `@main` or another ref.

The implemented public lifecycle path is:

```text
public target
  -> ai-sdlc-command.yml
  -> trusted private control workflow
  -> exact-target Runtime GitHub App token
  -> public target Feature branch
```

For Bootstrap, Plan, and Persist, the command bridge detects a public repository and dispatches the trusted `ai-sdlc-cross-repo-lifecycle.yml` workflow in `DREAM-XIN/ai-sdlc`.

That path requires the target-to-control credential described below plus the trusted Runtime GitHub App in the control repository.

Read [Public target lifecycle transport](public-target-lifecycle-transport.md) before enabling a public target.

## 7. Configure credentials only when your transport requires them

### Manual private target using installed lifecycle callers

Consuming the shared private Action uses GitHub's caller token and private Action access policy. You do not need a broad PAT merely to download the private shared Action.

### Target -> trusted control repository

Public lifecycle commands and autonomous dispatch require this target repository secret:

```text
AI_SDLC_CONTROL_DISPATCH_TOKEN
```

It is a transport credential used to dispatch/read Actions in `DREAM-XIN/ai-sdlc`. Scope it to the single control repository and the minimum Actions/metadata access required. It does not need target source write access.

Prefer a dedicated GitHub App installation credential or fine-grained token; do not substitute a broad classic PAT.

### Trusted control -> exact target repository

The trusted control repository stores:

```text
AI_SDLC_RUNTIME_APP_CLIENT_ID
AI_SDLC_RUNTIME_APP_PRIVATE_KEY
```

The Runtime GitHub App must be installed only on target repositories that opt into the relevant cross-repository/autonomous flows. The trusted gateway mints short-lived installation tokens restricted to one target repository per run.

Do not copy these Runtime App secrets into the target repository.

See [Cross-repository installation](cross-repository-installation.md) and [Security model](security-model.md).

## 8. Run Installation Check before the first Feature

### Private target that can consume the private Action

Run the installed workflow:

```text
AI-SDLC Installation Check
```

The check is read-only. It validates, among other things:

1. `.ai-sdlc/project.yaml` schema and semantic rules;
2. `repository.full_name` and `repository.default_branch` against live caller metadata;
3. every `context.rules` and `context.read` path;
4. required repository-wide `AGENTS.md` and its inclusion in `context.rules`;
5. configured command working directories;
6. all installed `DREAM-XIN/ai-sdlc` Action references for unresolved placeholders or moving refs.

A valid installation reports `READY`.

### Public target while the control repository is private

The current `ai-sdlc-installation-check.yml` itself consumes the private shared Action, so a public target cannot execute that caller directly while the control repository remains private. Do not claim that the public lifecycle bridge makes this particular caller executable; the v0.2.0 trusted cross-repository lifecycle workflow accepts only `plan`, `bootstrap`, and `persist`.

Before the first public-target Feature, run the implemented deterministic validator from a reviewed checkout of the control baseline against a checkout of the target workspace:

```bash
python scripts/validate_target_installation.py \
  --workspace /path/to/target \
  --project .ai-sdlc/project.yaml \
  --repository owner/my-project \
  --default-branch main
```

Then configure the public command bridge transport before Bootstrap/Plan/Persist. This is an explicit v0.2.0 transport limitation, not permission to skip installation validation.

For the detailed preflight contract, see [Cross-repository installation preflight](cross-repository-installation-preflight.md).

## 9. For autonomous private execution, run the separate Runtime App preflight

Installation Check validates the target repository contract. It does not prove that the Runtime GitHub App is installed on the target or that an AI execution engine is ready.

Before the first autonomous dispatch to a private target, run the control-repository workflow:

```text
AI-SDLC gh-aw Cross-Repo Runtime Preflight
```

Provide only the target repository in `owner/repo` form. A `READY` result proves exact-target Runtime App installation/read transport; it does not prove provider quota or model credentials.

The separate:

```text
AI-SDLC gh-aw Runtime Preflight
```

checks the gh-aw engine/profile/provider readiness described by the runtime integration.

See [Autonomous development](autonomous-development.md).

## 10. Commit installation on a normal branch and PR

Install AI-SDLC like any other repository change:

1. create a setup branch;
2. add `AGENTS.md`, `.ai-sdlc/project.yaml`, and the caller workflows;
3. replace all immutable-pin placeholders;
4. run the applicable installation validation;
5. review the workflow permissions and credentials;
6. merge the installation PR under normal repository protection.

Only after installation is ready should you create the first non-default Feature branch and Bootstrap the first Feature.

## 11. Create the durable state directories

Before or with the first Feature, ensure the repository can hold:

```text
state/bootstrap/
state/features/
state/events/
```

`state/bootstrap/` contains the Feature creation input. `state/features/` contains authoritative Feature Manifests. `state/events/` is the append-oriented lifecycle event/audit surface.

Workers should not be granted blanket permission to rewrite `state/features/**`.

## Installation checklist

Before your first Feature, verify:

- [ ] `AGENTS.md` exists.
- [ ] `.ai-sdlc/project.yaml` validates.
- [ ] `repository.full_name` matches the actual GitHub repository.
- [ ] `repository.default_branch` matches GitHub.
- [ ] every `context.rules` and `context.read` file exists.
- [ ] `AGENTS.md` is listed in `context.rules`.
- [ ] every configured command `cwd` exists.
- [ ] required caller workflows are installed under their exact names.
- [ ] no `REPLACE_WITH_AI_SDLC_FULL_SHA` placeholder remains.
- [ ] no AI-SDLC Action uses a moving ref such as `@main`.
- [ ] private Action access is configured if a private target consumes the private control Action.
- [ ] `AI_SDLC_CONTROL_DISPATCH_TOKEN` is configured if the target must dispatch the trusted private control repository.
- [ ] Runtime GitHub App credentials/installations are ready if using cross-repository public/autonomous paths.
- [ ] the applicable Installation Check/validator reports success.

Next: [Run your first Feature](feature-lifecycle-guide.md).
