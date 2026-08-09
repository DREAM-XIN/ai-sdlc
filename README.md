# AI-SDLC

Vendor-neutral, GitHub-native orchestration for agentic software development.

AI-SDLC defines a portable protocol for coordinating humans, ChatGPT Web, coding agents, CI/CD and repository automation across the software-development lifecycle.

## Getting started

New to AI-SDLC? You do not need to read the internal architecture first.

1. [Getting Started](docs/getting-started.md) — understand the operating loop from a new GitHub repository to a completed Feature.
2. [Set up a new project](docs/new-project-setup.md) — install `AGENTS.md`, the Project Adapter, caller workflows, immutable pins, credentials, and installation validation.
3. [Run your first Feature](docs/feature-lifecycle-guide.md) — follow `F-DEMO-LOGIN-0001` through Bootstrap, Plan, every `standard-feature` stage, Persist, Gates, and `DONE`.
4. [Choose AI-SDLC roles](docs/role-guide.md) — use independent Product, Reviewer, Architect, Orchestrator, Developer, QA, and Acceptance contexts safely.
5. [Enable autonomous development](docs/autonomous-development.md) — understand trusted gh-aw dispatch, bounded worker branches, Draft PRs, credentials, and preflights.
6. [Troubleshooting and FAQ](docs/troubleshooting.md) — diagnose installation, revision, Persist, Gate, public/private transport, and autonomous runtime failures.

The published v0.2.0 release baseline is `44e68d4ec6517135b0008ba4cf14fdb625f9481d`. Production caller workflows must pin reviewed AI-SDLC Actions to a full immutable commit SHA, never a moving ref such as `main`.

## Goals

- Keep project state outside model conversations.
- Treat GitHub as the system of record.
- Separate orchestration from execution runtimes.
- Make requirements, designs, tasks, gates and evidence auditable.
- Reuse existing infrastructure instead of rebuilding GitHub or coding-agent runtimes.
- Support manual, assisted and autonomous execution modes.

## Execution model

AI providers are optional execution backends, not control-plane dependencies.

AI-SDLC owns lifecycle state, orchestration, routing, Gates, Evidence, and persistence. Models, coding agents, web assistants, IDE agents, CLI tools, and human operators are replaceable workers connected through runtime contracts.

A deployment therefore does not need a particular model provider to use the AI-SDLC protocol:

- `manual` execution can hand a portable Task Package to a human or web AI;
- `assisted` execution can combine an interactive runtime with deterministic GitHub automation;
- `autonomous` execution can dispatch bounded work to API/CLI/CI-backed agents without human initiation of each task.

Provider diversity exists to make autonomous workers routable by capability, availability, cost, and policy. It must not move provider-specific inference calls or credentials into Commander, lifecycle state transitions, or Gate authority.

Conceptually:

```text
AI-SDLC control plane
        |
        +--> runtime: ChatGPT Web / human / IDE / CLI / gh-aw / external agent
                         |
                         +--> optional provider/model selection
```

Runtime, provider, and model are separate concerns. For example, `gh-aw` is a runtime, DeepSeek is a provider, and `deepseek-chat` is a model.

## Reference architecture

AI-SDLC is designed to integrate with:

- GitHub Agentic Workflows (`github/gh-aw`) for GitHub-native automation and safe writes.
- Existing coding-agent orchestrators for worktree/branch/PR execution.
- ChatGPT Web through a manual runtime contract that produces portable task packages.

Core control-plane loop:

```text
Feature Bootstrap
      ↓
Feature Manifest
      ↓
Commander / Orchestrator
      ↓
Runtime Router
      ↓
Task Package / runtime decision
      ↓
Worker + Evidence
      ↓
Feature Event Inbox
      ↓
Transition + Persistence
      ↓
Next Commander state
```

## Repository layout

- `spec/` — protocol JSON Schemas.
- `roles/` — vendor-neutral capability-role contracts.
- `profiles/` — reusable lifecycle profiles.
- `runtimes/` — runtime adapter contracts and reference runtimes.
- `dispatch/` — trusted default runtime-routing policy.
- `scripts/` — deterministic reference control-plane implementation and validators.
- `docs/` — architecture, integration and operating documentation.

## Project Adapter

Target repositories describe their local stack and engineering contract in:

```text
.ai-sdlc/project.yaml
```

The adapter can define project rules, durable context, deterministic build/test/lint commands and ownership roots without changing AI-SDLC lifecycle code.

See `docs/project-adapter.md` and `templates/project-adapter.yaml`.

## Status

`0.2.0` is the declared release baseline. It preserves the v0.1 durable lifecycle authority model while stabilizing production-oriented autonomous and cross-repository execution.

Implemented and dogfooded capabilities include:

- Feature bootstrap, authoritative Feature Manifests, Feature Events, optimistic revisions, and deterministic persistence;
- Runtime Router support for manual, assisted, and autonomous execution without granting providers lifecycle authority;
- `gh-aw` autonomous workers with bounded Safe Output Draft PR creation and structured Worker Result ingestion;
- Feature-specific execution context propagation, including linked Feature Issue context and exact required outputs;
- automated PR review/verification collection and durable review-remediation tasks;
- trusted Issue Comment commands for cross-repository Bootstrap, Plan, Persist, and autonomous dispatch;
- artifact registration and evidence-backed artifact status transitions through Feature Events;
- private cross-repository autonomous execution using exact-target, short-lived GitHub App credentials;
- deterministic cross-repository dispatch identity, duplicate suppression, and exact run/receipt correlation;
- separate target-installation contract and Runtime App access preflights, so Project Adapter/context/caller defects fail before the first Feature and autonomous access remains exact-target;
- workflow-profile-aware Commander task contracts, including `small-change` lifecycles that do not inherit nonexistent standard-feature design/plan context;
- compiled-worker allowlisting and strict materialization of provider workers, including the current Gemini CLI `0.52.0` worker/lock;
- a completed external Feature lifecycle in the public dogfood target, including requirement, design, implementation, code review, verification, acceptance, and release Gates;
- a completed private repeated-dogfood lifecycle through independent code review and verification;
- successful target-installation preflight dogfood across multiple materially different private repositories.

The v0.2 release matrix was explicitly narrowed after those proofs completed. A separate security-sensitive private Target B remains active post-v0.2 hardening work; it is not marked DONE and no remaining Gate is waived or bypassed for the release.

See `docs/v0.2-stabilization.md`, `docs/v0.2-release-readiness.md`, and `release/v0.2.0.yaml` for the release boundary, evidence, and policy.

## Public repository readiness

The repository includes a deterministic current-tree credential scan for Public-release preparation:

```bash
python scripts/validate_public_readiness.py
```

Changing repository visibility also exposes historical repository data, so the current-tree scan is not sufficient by itself. Follow `docs/public-release-readiness.md` for the required Git-history, Actions-log/artifact, repository-protection, and transport migration checks before changing visibility.

## License

Licensed under the Apache License, Version 2.0. See `LICENSE`.

## Design principle

> Agents are replaceable workers. Artifacts, evidence and workflow state are durable facts.