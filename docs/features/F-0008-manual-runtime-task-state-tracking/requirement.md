# Requirement — Manual Runtime Task State Tracking

Feature: #8

## Problem
Manual runtimes such as ChatGPT Web can receive portable Task Packages, but AI-SDLC does not yet define durable execution lifecycle state. The orchestrator therefore cannot determine whether a manual task is merely ready, actively being worked, blocked, submitted for evaluation, completed, or failed without relying on conversation memory or informal comments.

## Goal
Define a portable, deterministic execution-state model for manual runtimes whose state can be persisted in GitHub/repository artifacts and validated without an LLM.

## Scope
- Canonical states: READY, STARTED, BLOCKED, SUBMITTED, COMPLETED, FAILED.
- Allowed transitions between states.
- State-specific required metadata.
- Correlation to feature, task and runtime identifiers.
- Deterministic transition validation.
- Reference persistence and operator guidance.

## Non-goals
- Browser automation of ChatGPT Web.
- Autonomous dispatch.
- Model-based state inference.
- Replacing GitHub as the system of record.

## Business rules
1. READY is the initial state for a dispatchable manual task.
2. STARTED means a worker has accepted/started the task.
3. BLOCKED requires a non-empty blocker reason and may resume to STARTED.
4. SUBMITTED means the worker has produced outputs for independent evaluation; it is not completion.
5. COMPLETED requires one or more durable completion evidence references.
6. FAILED requires failure detail and may be retried only through an explicit new transition.
7. State changes must preserve task identity and runtime identity.
8. Invalid transitions must be rejected deterministically.

## Acceptance criteria
- AC1: The protocol defines READY, STARTED, BLOCKED, SUBMITTED, COMPLETED and FAILED.
- AC2: Illegal transitions are rejected deterministically.
- AC3: BLOCKED requires a reason; FAILED requires failure detail; COMPLETED requires completion evidence references.
- AC4: State records correlate to feature/task/runtime identifiers.
- AC5: A reference CLI validates transitions without invoking an LLM.
- AC6: Requirement, design, review and verification artifacts are traceable for this feature.

## Edge cases
- Repeated STARTED -> STARTED is not considered a transition and is rejected.
- BLOCKED -> SUBMITTED is rejected; the task must resume to STARTED first.
- COMPLETED and FAILED are terminal for the same execution attempt.
- Evidence URI syntax is provider-neutral; validation requires non-empty references, not network reachability.

## Constraints
- JSON Schema 2020-12.
- Validator must be dependency-light and deterministic.
- Runtime-specific adapters may map these states to GitHub labels/comments/files, but canonical semantics remain vendor-neutral.

## Open questions
None for v0.1.
