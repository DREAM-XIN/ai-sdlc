# Changelog

## 0.1.0 — release candidate

AI-SDLC v0.1.0 is the first externally consumable baseline of the vendor-neutral, GitHub-native control-plane protocol and reference implementation.

### Protocol and durable state

- Added JSON Schema 2020-12 contracts for workflows, tasks, artifacts, gates, runtimes, evidence, task packages, task execution, Feature Manifests, Feature Events, bootstrap inputs, dispatch policy, Commander Plans, GitHub Persistence Plans and Project Adapters.
- Added semantic validation for cross-object/state-machine invariants intentionally kept outside JSON Schema.
- Added protocol versioning/compatibility policy.
- Made GitHub/repository artifacts the durable source of truth instead of model conversation state.

### Workflow and orchestration

- Added reusable `small-change`, `standard-feature` and `high-risk` workflow profiles.
- Added deterministic Orchestrator outcomes: `DISPATCH`, `WAIT`, `BLOCKED`, `COMPLETE`, `INVALID`.
- Added DAG/dependency-aware runnable-stage calculation and safe parallel work planning.
- Added Gate/Evidence semantics and independent review rubrics.

### Runtime routing and execution

- Added configuration-driven Runtime Router.
- Added ChatGPT Web/manual Runtime Task Packages and copy-ready prompts.
- Added Reference Commander `bootstrap`, `plan` and `ingest` interface.
- Added a reference `gh-aw/autonomous` adapter with deterministic dispatch-plan and worker-result contracts, READY → WORKING reservation, revision-aware result ingestion and trusted GitHub workflow boundaries.
- Autonomous worker completion closes only the assigned work stage and never self-approves a Gate, review, acceptance, release or merge.
- Agent Orchestrator remains a routing/integration target until a concrete adapter is implemented.

### Feature lifecycle and persistence

- Added Feature Bootstrap and authoritative `state/features/` manifests.
- Added append-oriented `state/events/<feature>/<event>.yaml` Event Inbox.
- Added stable event identity/replay protection.
- Added deterministic Feature transition and GitHub Persistence Plan generation.
- Added model-free GitHub-native Commander/Persistence workflows.

### Project portability

- Added `.ai-sdlc/project.yaml` Project Adapter for project identity, durable context, argv-form verification commands and ownership roots.
- Added generic and Java/Spring + Vue examples.
- Added shared cross-repository Composite Action and least-privilege caller workflow templates so target repositories do not copy the AI-SDLC control implementation.

### Concurrency

- Added monotonic Feature Manifest revisions and `expected_revision` Event preconditions.
- Repository Event Inbox rejects stale Feature Events.
- Persistence Plans expose source/result revision and hashes.
- Write-capable GitHub transports compare local checkout SHA to the live remote target branch before push and fail closed on stale workspaces.
- The v0.1 gh-aw reference adapter serializes authoritative autonomous result ingestion to one worker per Feature revision until parallel merge semantics are explicitly defined.

### Security

- Split trusted control-plane runtime from untrusted target-repository workspace in privileged workflows.
- Restricted Manifest persistence to validated `state/features/` paths and Event Inbox inputs to canonical paths.
- Added path traversal/symlink/input hardening.
- Added explicit read/write GitHub token permission separation.
- Added formal threat model and Runtime Adapter security checklist.
- Pinned external GitHub Actions to reviewed immutable full commit SHAs.
- Added CI enforcement against mutable Action refs, `pull_request_target`, `workflow_run`, `write-all`, inherited secrets and PR-triggered contents writes.
- Added gh-aw dispatch/result workflow security validation so trusted worker definitions and lifecycle write authority remain separated from target workspace data.

### Dogfooding lessons adopted

- Rejected the early “one Gate/Artifact = one GitHub Issue” bookkeeping model in favor of Feature Manifest + durable repository artifacts.
- Kept reviewer independence separate from implementation self-report.
- Separated structural JSON Schema validation from semantic/state-machine validation.
- Kept GitHub, gh-aw and future Agent Orchestrator integrations behind adapter boundaries rather than moving lifecycle rules into vendor-specific runtimes.

### Known limitations / release blockers

- ChatGPT Web remains a manual transport; AI-SDLC generates durable Task Packages/prompts but does not automate browser tabs.
- The gh-aw adapter and transport are executable reference boundaries, but autonomous-runtime support is not declared complete until a real compiled gh-aw worker executes one bounded work unit and returns durable PR/evidence state (Issue #6).
- Agent Orchestrator remains routing-only.
- Cross-repository private Action dogfood in `DREAM-XIN/StarringV6Test` is implemented but currently blocked by the one-time GitHub private Actions Access repository setting recorded in Issue #52.
- A public GitHub Release/tag should not be published until the declared release blockers are cleared.
