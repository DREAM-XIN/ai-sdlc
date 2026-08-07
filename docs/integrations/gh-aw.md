# GitHub Agentic Workflows (`gh-aw`) integration

## Decision

AI-SDLC integrates with `github/gh-aw` as an **optional GitHub-native autonomous execution runtime**, not as the AI-SDLC workflow/state engine.

The manual ChatGPT Web path remains independent. Deterministic Feature lifecycle transitions, revision checks, Gate evaluation, risk policy and traceability remain owned by AI-SDLC.

## Reused upstream capabilities

| AI-SDLC need | `gh-aw` capability | Integration |
| --- | --- | --- |
| GitHub-hosted agent execution | compiled agentic workflows | direct runtime execution |
| Bounded GitHub writes | safe outputs | direct reuse |
| Agent engine choice | Copilot / Claude / Codex / Gemini | trusted engine profiles |
| Orchestrator/worker patterns | OrchestratorOps | future fan-out after revision merge semantics exist |
| Cross-repository patterns | CentralRepoOps | future multi-repo control plane |
| Workflow distribution | `gh aw add` / compiled workflows | installation mechanism |

AI-SDLC does **not** fork or reimplement gh-aw's compiler, agent engine integration, sandbox or safe-output handlers.

## Deliberately not delegated to gh-aw

AI-SDLC owns:

- protocol schemas/versioning;
- Feature lifecycle and revision model;
- task dependency semantics;
- artifact/evidence contracts;
- Gate definitions and deterministic evaluation;
- runtime routing;
- Project Adapter rules;
- Git persistence and optimistic concurrency;
- ChatGPT Web manual transport;
- cross-runtime traceability.

## Executable v0.1 boundary

The reference adapter is implemented under `runtimes/gh-aw/` and `scripts/gh_aw_adapter.py`.

```text
Commander Plan
    ↓ select runtime=gh-aw/autonomous
Dispatch Plan
    ↓
START Event: READY → WORKING, revision N → N+1
    ↓ durable push
trusted compiled gh-aw worker on default branch
    ↓
structured gh-aw-worker-result-v0.1
    ↓
Feature Event at expected_revision N+1
    ↓
COMPLETED → assigned work stage DONE
BLOCKED/FAILED → assigned stage BLOCKED
    ↓
Commander computes next independent stage / Gate
```

A worker may complete its assigned work stage, but it cannot PASS a Gate, approve its own review stage, rewrite the authoritative Feature Manifest or release/merge the Feature.

## GitHub transports

### `AI-SDLC gh-aw Profile Dispatch`

`.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` is the preferred v0.1 entrypoint for autonomous execution. It resolves a trusted engine profile from `runtimes/gh-aw/engine-profiles.yaml`, verifies the corresponding compiled lock exists on the trusted default branch, then dispatches the core runtime gateway.

The profile gateway intentionally exposes engine profile choices rather than an arbitrary worker filename.

### `AI-SDLC gh-aw Dispatch`

`.github/workflows/ai-sdlc-gh-aw-dispatch.yml` has two permission-separated jobs:

- `plan`: `contents: read` only; builds Commander and gh-aw Dispatch Plans.
- `execute`: `contents: write` + `actions: write`; persists the START reservation, verifies the live branch SHA, then invokes the compiled gh-aw worker through `workflow_dispatch`.

The direct `worker_workflow` input remains for v0.1 compatibility, but AI-SDLC's standard path is the trusted profile gateway above.

The execute path rejects default-branch targets. If workflow dispatch fails after START is pushed, the Feature remains `WORKING`; AI-SDLC does not invent a rollback or BLOCKED transition.

### `AI-SDLC gh-aw Result`

`.github/workflows/ai-sdlc-gh-aw-result.yml` accepts a structured worker result, validates it against `runtimes/gh-aw/worker-result.schema.json`, converts it to a Feature Event and runs the normal Event Inbox/revision/Git write-precondition path.

The result gateway has no `actions: write` permission.

## Vendor-neutral engine profiles

AI-SDLC keeps one canonical bounded worker definition and deterministically renders provider-specific workers from it. The renderer is allowed to vary only the workflow name and `engine:` value; lifecycle rules, safe-output scope, permissions and result handoff remain identical.

| Profile | gh-aw engine | Compiled worker | Runtime credential |
| --- | --- | --- | --- |
| `copilot` | `copilot` | `ai-sdlc-gh-aw-worker.lock.yml` | `COPILOT_GITHUB_TOKEN` or supported Copilot permission |
| `codex` | `codex` | `ai-sdlc-gh-aw-worker-codex.lock.yml` | `OPENAI_API_KEY` / `CODEX_API_KEY` |
| `claude` | `claude` | `ai-sdlc-gh-aw-worker-claude.lock.yml` | `ANTHROPIC_API_KEY` or supported keyless auth |
| `gemini` | `gemini` | `ai-sdlc-gh-aw-worker-gemini.lock.yml` | `GEMINI_API_KEY` or supported keyless auth |

All four profiles are compiled with pinned `github/gh-aw` v0.83.4 and `--strict`. The generated lock files are committed through a dedicated `gh-aw/compile-*` materialization branch using `GH_AW_GITHUB_TOKEN`, because GitHub applies stronger authorization to changes under `.github/workflows/`.

Provider availability is therefore a deployment/runtime concern, not an AI-SDLC protocol dependency. A repository can switch providers by choosing another trusted profile without changing Feature lifecycle semantics.

## v0.1 serialization rule

The reference adapter permits exactly **one authoritative autonomous gh-aw dispatch per Feature revision**.

This is intentional: AI-SDLC currently has one global Feature revision counter, so accepting multiple autonomous worker results concurrently would require explicit merge/rebase semantics. Parallel engineering work is still allowed; only automatic authoritative result ingestion is serialized until that protocol exists.

## Worker contract

The trusted compiled gh-aw workflow must expose these dispatch inputs:

```text
feature_id
expected_revision
target_ref
stage
role
task_payload
```

`task_payload` is the portable AI-SDLC Task plus Project Adapter context. The worker returns `gh-aw-worker-result-v0.1` with its correlation ids, expected revision, status, artifacts and evidence.

See `runtimes/gh-aw/README.md` for the exact adapter contract and failure semantics.

## Safety model

The model/agent should remain read-only by default and use gh-aw safe outputs for allowed mutations. AI-SDLC's trusted transport separately protects lifecycle state through:

- trusted control-plane checkout from the default branch;
- target Feature branch treated as data/workspace;
- immutable third-party Action pins;
- no execution of target-controlled Python/control dependencies;
- START/result revision preconditions;
- remote branch SHA write precondition;
- default-branch denial for autonomous state writes;
- independent review/Gate stages after worker completion.

Official gh-aw strict compiler output is also treated differently from AI-SDLC-authored workflows for one narrow checkout-credential rule. A generated worker lock qualifies only when its filename is controlled and its `gh-aw-metadata` attests strict mode, schema v4 and the repository-pinned compiler version. All other AI-SDLC Action security rules still apply.

## Current limitation

The executable worker definitions, multi-engine strict compilation, trusted profile dispatch, lock materialization and result gateway are installed and validated. Milestone #6 remains open for one final reason: **a live bounded autonomous run still requires at least one provider with usable inference entitlement/API access**.

GitHub Copilot is not required. Any supported trusted profile can satisfy the dogfood requirement. Once one real worker performs a bounded task, creates durable PR/runtime evidence and its Worker Result is persisted into the Feature Event loop, the reference autonomous runtime Definition of Done is satisfied.

## Upstream references

- https://github.github.com/gh-aw/
- https://github.github.com/gh-aw/reference/engines/
- https://github.github.com/gh-aw/reference/safe-outputs/
- https://github.github.com/gh-aw/patterns/orchestrator-ops/
- https://github.github.com/gh-aw/patterns/central-repo-ops/
- https://github.github.com/gh-aw/guides/reusing-workflows/
