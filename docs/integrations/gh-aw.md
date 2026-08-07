# GitHub Agentic Workflows (`gh-aw`) integration

## Decision

AI-SDLC integrates with `github/gh-aw` as an **optional GitHub-native agentic automation adapter**, not as the universal workflow engine.

The manual ChatGPT Web path must not require a hosted coding-agent invocation. Deterministic state transitions and schema/gate validation remain ordinary code/GitHub Actions. `gh-aw` is used only where an AI-powered GitHub workflow is valuable.

## Reused upstream capabilities

| AI-SDLC need | `gh-aw` capability | Integration |
| --- | --- | --- |
| GitHub event-driven AI work | Markdown agentic workflows | reference adapter |
| Bounded GitHub writes | safe outputs | direct reuse |
| Orchestrator/worker fan-out | OrchestratorOps | direct reuse for autonomous workers |
| Async worker dispatch | `dispatch-workflow` | runtime dispatch option |
| Synchronous reusable worker | `call-workflow` | runtime dispatch option |
| Issue-triggered automation | IssueOps | feature/task intake option |
| Central control repository | CentralRepoOps | multi-repository deployment option |
| Workflow distribution | `gh aw add` / reusable workflows | installation mechanism |

## Deliberately not delegated to gh-aw

AI-SDLC owns or keeps deterministic:

- protocol schemas and versioning;
- feature lifecycle/state model;
- task DAG and dependency semantics;
- artifact contracts;
- gate definitions and deterministic gate evaluation;
- risk policy;
- runtime selection;
- ChatGPT Web manual transport;
- cross-runtime traceability.

This prevents the protocol from being coupled to a specific agent engine or GitHub-only execution model.

## Runtime routing

A stage may select one of several execution paths:

```text
Task
  |
  +-- chatgpt-web  -> render portable package -> human dispatch
  +-- human        -> GitHub task/approval
  +-- gh-aw        -> invoke agentic workflow
  +-- orchestrator -> external coding-agent runtime
```

The runtime is an execution choice. It does not change the task's Definition of Done, artifact contract, evidence contract, or gate.

## Mapping to GitHub

Recommended reference mapping:

- Feature: GitHub Issue (optionally parent issue).
- Work Unit / Task: sub-issue or linked issue.
- Requirement/Design: versioned repository artifact and/or issue body with canonical repository path.
- Implementation: branch + pull request.
- Review: PR review plus normalized Review Artifact.
- Deterministic Evidence: GitHub Actions/check runs plus Evidence documents.
- Human approval: protected environment, review, or explicit approval record depending on repository policy.
- Correlation ID: AI-SDLC Feature/Task ID propagated into issue, PR, workflow payload and artifacts.

## OrchestratorOps adapter

For autonomous stages, an AI-SDLC orchestrator may expose only allowlisted worker workflows through `dispatch-workflow` or `call-workflow`.

Guidelines:

1. Pass the AI-SDLC `task_id` as a correlation identifier.
2. Workers receive bounded task packages, not unrestricted feature authority.
3. Use the smallest permission set for each worker.
4. Writes must use upstream safe-output controls where available.
5. Worker completion produces durable GitHub artifacts/evidence; it never directly advances the AI-SDLC state machine.
6. Gate evaluation remains external to worker self-report.

## Manual-first reference implementation

Milestone 1 intentionally uses this path:

```text
GitHub Issue
  -> deterministic AI-SDLC state/task rendering
  -> ChatGPT Web task package
  -> worker writes artifact/PR/evidence to GitHub
  -> deterministic gate evaluator
  -> next state
```

No `gh-aw` AI invocation is required for that path.

Milestone 2 can add selected `gh-aw` workers for tasks such as repository research, triage, autonomous review or cross-repository coordination when their AI-engine cost is justified.

## Safety model

Where `gh-aw` is used, prefer its read-only agent runtime plus safe-output separation rather than granting broad write credentials directly to an agent. File-changing automation should additionally enforce AI-SDLC allowed/forbidden scope and upstream file allowlists where practical.

## Upstream references

- https://github.github.com/gh-aw/
- https://github.github.com/gh-aw/patterns/orchestrator-ops/
- https://github.github.com/gh-aw/patterns/issue-ops/
- https://github.github.com/gh-aw/patterns/central-repo-ops/
- https://github.github.com/gh-aw/reference/safe-outputs/
- https://github.github.com/gh-aw/guides/reusing-workflows/
