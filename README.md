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

The v0.1 reference control plane currently includes:

- Feature bootstrap and durable Feature Manifests;
- deterministic workflow/DAG state calculation;
- Runtime Router and ChatGPT Web manual Task Packages;
- Gate/Evidence semantics and Feature Events;
- replay-safe Event Inbox and GitHub persistence planning;
- Reference Commander CLI;
- model-free GitHub-native Commander transport;
- trusted-runtime/workspace isolation for write-capable GitHub workflows;
- Project Adapter support for stack-independent target repositories.

The next major milestone is running the same protocol against real target repositories and adding autonomous runtime adapters without changing the control-plane contract.

## Design principle

> Agents are replaceable workers. Artifacts, evidence and workflow state are durable facts.
