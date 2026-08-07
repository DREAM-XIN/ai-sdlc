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

## Repository layout

- `spec/` — protocol JSON Schemas.
- `roles/` — vendor-neutral capability-role contracts.
- `profiles/` — reusable lifecycle profiles.
- `runtimes/` — runtime adapter contracts and reference runtimes.
- `docs/` — architecture and roadmap.

## Status

Protocol v0.1 bootstrap. The first milestone is a complete reference flow:

`requirement -> design -> plan -> implementation -> review -> verification -> acceptance`

## Design principle

> Agents are replaceable workers. Artifacts, evidence and workflow state are durable facts.
