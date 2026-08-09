# AI-SDLC

Vendor-neutral, GitHub-native orchestration for agentic software development.

AI-SDLC defines a portable protocol for coordinating humans, ChatGPT Web, coding agents, CI/CD and repository automation across the software-development lifecycle.

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

`0.1.0` remains the declared release baseline. The current `main` branch has moved into v0.2 stabilization and now includes the original v0.1 control-plane surface plus production-oriented autonomous and cross-repository execution capabilities.

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
- successful target-installation preflight dogfood across multiple materially different private repositories.

The project is now stabilizing these capabilities for a formal v0.2 baseline. Remaining release work is intentionally limited to lifecycle completion rather than more control-plane expansion: the Feature-context dogfood still needs real green CI evidence, one private repeated-dogfood target is waiting at independent review, and another correctly failed closed on a security-sensitive compatibility issue that was split into a higher-risk Feature requiring independent review. The earlier stale Gemini materialization PR has already been reconciled through a current-main rematerialization and is no longer a blocker.

See `docs/v0.2-stabilization.md` and `release/v0.2.0-draft.yaml` for the current stabilization boundary and release criteria.

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