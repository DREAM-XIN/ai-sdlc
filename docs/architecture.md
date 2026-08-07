# Architecture

## Layers

### Protocol layer

Vendor-neutral definitions for workflows, tasks, artifacts, gates, runtimes and evidence.

### Control plane

A future AI-SDLC orchestrator resolves feature state, builds task DAGs, evaluates gates and selects runtimes. GitHub is the initial system of record.

### Integration plane

The reference GitHub integration should reuse GitHub Agentic Workflows where suitable for event handling, safe writes, reusable workflows and cross-repository automation.

### Execution plane

Execution is pluggable:

- ChatGPT Web manual runtime
- Human workers
- Coding-agent orchestrators
- GitHub-hosted agent workflows
- Future API/CLI runtimes

## Core invariant

`worker output -> artifact/evidence -> gate -> next state`

A worker's conversational claim never directly advances durable workflow state.

## Reuse strategy

AI-SDLC should not rebuild mature infrastructure:

- Reuse GitHub-native workflow/security primitives instead of implementing a GitHub automation platform.
- Integrate existing coding-agent orchestrators for worktree, session, PR and CI feedback loops.
- Adapt established SDLC ideas such as bounded work units, design gates and adversarial review rather than inventing new ceremonies without evidence.
