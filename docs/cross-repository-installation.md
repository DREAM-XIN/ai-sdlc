# Cross-repository GitHub installation

AI-SDLC can be consumed by another private GitHub repository without copying the Python control plane, schemas, validators, trusted Dispatch Policy, or autonomous worker policy into that repository.

The target repository keeps only its project-specific contract, durable state, source code, and small caller workflows. Trusted lifecycle code is consumed from `DREAM-XIN/ai-sdlc`; autonomous Developer execution is handed back to the trusted `ai-sdlc` runtime gateway.

## Private repository prerequisite

While `DREAM-XIN/ai-sdlc` is private, GitHub must allow other private repositories owned by `DREAM-XIN` to consume its shared Actions.

In `DREAM-XIN/ai-sdlc`, open **Settings → Actions → General → Access**, allow repositories owned by `DREAM-XIN`, and save the setting.

GitHub uses a short-lived scoped token to download a private shared Action. The Bootstrap/Plan/Persist transport therefore does not require a PAT merely to consume the private control Action. Keep in mind that collaborators who can read caller workflow logs may see information intentionally emitted by the shared Action, so trusted code must never print control-plane secrets or sensitive source content.

## Minimal target repository

```text
my-project/
├── .ai-sdlc/
│   └── project.yaml
├── .github/
│   └── workflows/
│       ├── ai-sdlc-plan.yml
│       ├── ai-sdlc-bootstrap.yml
│       ├── ai-sdlc-persist.yml
│       └── ai-sdlc-command.yml          # optional command bridge
├── state/
│   ├── features/
│   └── events/
├── AGENTS.md
└── <project source>
```

The target repository does **not** copy `scripts/`, `spec/`, `roles/`, `dispatch/default.yaml`, `dispatch/gh-aw-developer.yaml`, compiled gh-aw workers, or AI-SDLC Python dependencies. Those remain trusted control-plane implementation.

## Install the Project Adapter

Start from `templates/project-adapter.yaml` and save it as `.ai-sdlc/project.yaml` in the target repository. Keep `repository.full_name` and `repository.default_branch` accurate. Cross-repository autonomous execution validates those fields against the target repository identity and the live default branch before dispatch.

See `docs/project-adapter.md` for the schema and semantic rules.

## Install caller workflows

Copy the required templates from `templates/github/` into the target repository's `.github/workflows/` directory. A repository may install only the operations it uses.

### Pin lifecycle Actions

The Bootstrap/Plan/Persist templates intentionally contain this invalid installation placeholder:

```yaml
uses: DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA # ai-sdlc-install-placeholder
```

Replace `REPLACE_WITH_AI_SDLC_FULL_SHA` with the reviewed 40-character `ai-sdlc` commit SHA you intend to run. Do not replace it with `main`, a release branch, or another moving ref. The target Feature branch cannot replace code behind that immutable pin.

Third-party Actions in the templates are also pinned to reviewed immutable SHAs. See `templates/github/README.md` and `docs/security-model.md`.

## Permission separation

### Plan

```yaml
permissions:
  contents: read
```

Plan reads the Feature Manifest and Project Adapter, computes Commander state, and emits the Commander plan and manual transport artifacts. Checkout uses `persist-credentials: false`; Plan cannot push source or state.

### Bootstrap

```yaml
permissions:
  contents: write
```

Bootstrap can create the initial Feature Manifest. Persistence is disabled by default and direct default-branch writes are denied unless explicitly enabled. Immediately before a real push, the trusted Action verifies the checked-out branch SHA still matches the remote branch.

### Persist

```yaml
permissions:
  contents: write
```

Persist validates Feature Events through the Event Inbox and Transition Engine. It is dry-run by default, enforces `expected_revision`, and verifies the remote branch precondition before a real push.

The shared lifecycle Action cannot elevate the caller `GITHUB_TOKEN`; the caller workflow owns that permission envelope.

### Issue Command Bridge

The optional `ai-sdlc-command.yml` accepts exact commands only from an OWNER, MEMBER, or COLLABORATOR:

```text
/ai-sdlc bootstrap target_ref=<branch> bootstrap=state/bootstrap/<file>.yaml manifest=state/features/<file>.yaml
/ai-sdlc plan target_ref=<branch> manifest=state/features/<file>.yaml
/ai-sdlc persist target_ref=<branch> manifest=state/features/<file>.yaml event=state/events/<feature>/<file>.yaml
/ai-sdlc dispatch-gh-aw target_ref=<feature-branch> manifest=state/features/<file>.yaml
```

The autonomous command is deliberately provider-neutral. It cannot provide repository, policy, provider, model, engine-profile, credential, or compiled-worker selectors. Target identity is bound from `GITHUB_REPOSITORY`; trusted policy and engine-profile resolution remain in `DREAM-XIN/ai-sdlc`.

The bridge itself remains `contents: read`; it cannot edit the target source tree or Feature Manifest.

## Autonomous Developer handoff

For an executable Developer work unit, the command bridge can hand the Feature to the existing trusted gh-aw runtime:

```text
target Feature Issue / Manifest
  -> target command bridge
  -> trusted ai-sdlc profile gateway
  -> Commander + Runtime Router
  -> trusted cross-repo handoff
  -> compiled gh-aw Developer worker
  -> bounded branch in target repository
  -> Draft PR to the Feature branch
  -> Worker Result
  -> trusted Feature Event collector / Persist
  -> existing Code Review / Verification lifecycle
```

A target Feature that is already `WORKING` can be adopted without a second START event only when Commander recomputes `WAIT`, exactly one current Feature stage is in progress, and Runtime Router still resolves that work unit to `gh-aw/autonomous`. This resume path does not mutate the Manifest before worker dispatch.

See `docs/cross-repository-autonomous-execution.md` for the detailed contract and worker boundaries.

## Autonomous credentials: separate trust boundaries

Cross-repository autonomous execution requires two credential boundaries; they must not be collapsed into a broad PAT.

### Target -> trusted control repository

Configure `AI_SDLC_CONTROL_DISPATCH_TOKEN` in the target repository for the Issue Command Bridge. It is used only to dispatch/read Actions in `DREAM-XIN/ai-sdlc` and resolve the downstream run receipt. Scope it to the control repository only and grant only the Actions/metadata access required for that transport.

Prefer a dedicated GitHub App installation credential or a fine-grained token restricted to the single control repository. It does **not** need target repository contents or pull-request write access.

### Trusted control -> exact target repository

Configure `AI_SDLC_RUNTIME_APP_CLIENT_ID` and `AI_SDLC_RUNTIME_APP_PRIVATE_KEY` in the trusted `ai-sdlc` repository and install that GitHub App only on target repositories that opt into autonomous execution.

The trusted gateway mints short-lived installation tokens scoped to exactly one target repository per run:

- planning / identity validation: `contents: read`;
- trusted lifecycle persistence when required: `contents: write`;
- gh-aw checkout / GitHub context / Safe Output: the minimum App installation permissions needed for source reads and Draft PR creation.

The autonomous agent workflow itself remains `permissions: read-all`. It does not receive direct lifecycle write authority. Source changes are published through gh-aw Safe Output, whose target repository and Draft PR base are fixed by trusted runtime configuration.

### Run the cross-repository runtime preflight

Before enabling the autonomous command on a private target repository, run the control-repository workflow **AI-SDLC gh-aw Cross-Repo Runtime Preflight** and provide only the target repository in `owner/repo` form.

The preflight is provider-neutral and non-mutating. It checks that:

- `AI_SDLC_RUNTIME_APP_CLIENT_ID` exists in the trusted control repository;
- `AI_SDLC_RUNTIME_APP_PRIVATE_KEY` exists without exposing its value;
- the Runtime GitHub App is installed where an exact-target installation token can be minted;
- that token can read the exact repository metadata using only `contents: read` and `metadata: read`.

A `READY` result proves only the control-to-target installation/read transport. It does not probe an AI provider, does not prove provider quota or entitlement, does not request target write permissions, and does not modify Feature state. The separate **AI-SDLC gh-aw Runtime Preflight** continues to validate engine lock/credential readiness.

The real cross-repository gateway repeats the Runtime App credential-presence check before token mint. It also rejects any `worker_workflow` that is not one of the compiled workflows registered in `runtimes/gh-aw/engine-profiles.yaml`, even if some other workflow file with that name exists in the control repository.

## Worker security boundary

The trusted worker must:

- checkout the exact target repository and the non-default Feature branch;
- construct a bounded `gh-aw/<feature>-<run>-v<revision>` implementation branch from `origin/<target_ref>`;
- read `AGENTS.md`, `.ai-sdlc/project.yaml`, the Feature Manifest, linked Feature Issue, approved requirement/design artifacts, and plan;
- restrict implementation edits to the assigned work unit and Project Adapter ownership roots;
- reject any `state/features/**` or `state/events/**` diff;
- create at most one Draft PR whose base is the target Feature branch;
- never PASS/waive a Gate, merge, release, or directly edit authoritative lifecycle state.

Worker Result handling stays in the trusted collector. The collector converts the structured result to a Feature Event and applies the existing optimistic-concurrency and Event Inbox rules before updating the target Feature branch.

## Recommended lifecycle flow

1. Create a non-default Feature branch.
2. Add a Feature Bootstrap input under `state/bootstrap/`.
3. Run Bootstrap dry first, then persist the generated Manifest to `state/features/<feature>.yaml`.
4. Run Plan. Commander reads `.ai-sdlc/project.yaml` when present.
5. Before the first autonomous dispatch for a private repository, run the cross-repository runtime preflight from the trusted control repository.
6. For manual work, use the generated task prompt and return a durable Feature Event. For an eligible Developer work unit, use the provider-neutral `dispatch-gh-aw` command instead.
7. Persist lifecycle transitions only through the trusted Event Inbox / collector path.
8. Re-run Plan after each durable transition; Code Review, remediation, Verification, Acceptance, and Gates remain independent lifecycle stages.

See `docs/optimistic-concurrency.md` for the state concurrency model.

## Trust boundary

```text
reviewed AI-SDLC lifecycle Action @ full commit SHA
  ├── schemas / Commander / transition engine / persistence validation
  └── caller-repository lifecycle state

trusted DREAM-XIN/ai-sdlc autonomous runtime on protected control branch
  ├── Runtime Router / developer policy / engine profile / compiled worker
  ├── exact-target GitHub App installation token
  └── gh-aw Safe Output
             │
             ▼
private target repository Feature branch
  ├── .ai-sdlc/project.yaml
  ├── source and tests
  ├── approved artifacts
  └── state/  (collector-owned lifecycle writes only)
```

The target Feature branch supplies project/Feature context but cannot replace Commander, Runtime Router, worker policy, compiled worker, or Gate authority. The autonomous control workflow is intentionally executed from the trusted control repository rather than from code supplied by the target Feature branch.

## Current limitations

- Caller lifecycle workflow templates require `REPLACE_WITH_AI_SDLC_FULL_SHA` substitution during installation.
- Private Actions Access must be enabled in the `ai-sdlc` repository settings.
- Autonomous execution additionally requires the least-privilege control-dispatch credential plus installation of the trusted runtime GitHub App on each opted-in private target repository.
- The read-only cross-repository preflight proves Runtime App installation/read access but intentionally does not prove target write/PR permissions; those remain exercised only by the trusted execution path.
- ChatGPT Web remains a manual transport.
- Multi-repository central scheduling is a later layer; this installation supports generic target-initiated autonomous handoff today.
