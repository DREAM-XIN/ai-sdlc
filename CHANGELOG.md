# Changelog

## 0.2.0 — release candidate

AI-SDLC v0.2.0 stabilizes autonomous and cross-repository execution around the durable lifecycle authority model introduced in v0.1.

### Autonomous execution and Feature context

- Added Feature-specific execution context propagation, including linked Feature Issue context and exact required outputs.
- Added automated PR review/verification lifecycle collection and durable remediation tasks without granting workers Gate authority.
- Added provider/runtime/model separation with auditable effective-model metadata.
- Added compiled-worker allowlisting and current-main provider materialization, including Gemini CLI `0.52.0`.

### Cross-repository execution

- Added trusted Issue Comment transport for Bootstrap, Plan, Persist, and autonomous `gh-aw` dispatch.
- Added deterministic semantic dispatch identities, duplicate suppression, and exact command/worker receipt correlation.
- Added exact-target, short-lived Runtime GitHub App credentials for private cross-repository autonomous execution.
- Added separate target-installation contract and Runtime App access preflights.
- Added workflow-profile-aware Commander contracts so `small-change` targets do not inherit nonexistent standard-feature context.

### Lifecycle and persistence hardening

- Preserved Feature Event + trusted Persist as the only lifecycle mutation path.
- Preserved optimistic `expected_revision` write preconditions and fail-closed stale-write behavior.
- Added automatic Feature Event push resolution and PR lifecycle event validation.
- Kept review, verification, acceptance, release, merge, and Gate authority outside autonomous workers.

### Public repository and release hardening

- Added Apache-2.0 licensing and deterministic current-tree/public-history credential auditing.
- Added retained Actions log/artifact auditing for publication readiness.
- Added guarded merged-branch cleanup and post-public repository protection snapshots.
- Added a stable `required-pr-gate` aggregator and immutable full-SHA Action policy.
- Made release-readiness validation version-aware so `VERSION` selects `release/v<version>.yaml` instead of being hard-wired to v0.1.

### Dogfood evidence

- Completed the Feature-context dogfood tracked by Issue #128 / PR #144 with green validation, independent review, verification, and workflow `DONE` at revision 10.
- Completed a private repeated-dogfood `small-change` lifecycle through independent code review and verification at revision 7.
- Preserved a separate public external full standard-feature lifecycle through acceptance and release Gates.
- Validated target-installation preflight across two materially different private repositories.
- Confirmed fail-closed behavior when a low-risk private target exposed a security-sensitive Argon2 compatibility defect.

### Release matrix decision

- The required v0.2 cross-repository lifecycle matrix is explicitly narrowed to the completed public external full lifecycle plus the completed private repeated-dogfood lifecycle.
- The security-sensitive private Target B Feature remains active post-v0.2 hardening work. It is not marked DONE and no remaining code, verification, acceptance, or release Gate is waived.

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
- Completed cross-repository installation dogfood against a materially different private target with immutable Action pinning.
- Completed a real bounded gh-aw autonomous dogfood through DeepSeek, producing a bounded PR and durable evidence; review, Gates and verification were persisted until the Feature workflow reached `DONE`.
- Removed feature-specific one-shot/recovery workflows after durable evidence was captured; reusable provider/runtime infrastructure remains.

### Known limitations / release status

- ChatGPT Web remains a manual transport; AI-SDLC generates durable Task Packages/prompts but does not automate browser tabs.
- Agent Orchestrator remains routing-only.
- The two previously declared v0.1 release blockers (cross-repository dogfood and autonomous-runtime dogfood) are complete and no longer block the release candidate.
- v0.1 remains a release candidate until the release-readiness cleanup is merged and full repository CI is green on `main`; publication should use the reviewed full commit SHA for production caller pins.
