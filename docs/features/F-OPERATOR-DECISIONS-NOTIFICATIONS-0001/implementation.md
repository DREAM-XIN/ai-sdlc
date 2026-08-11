# Implementation — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## Status

Implementation of the approved Requirement, Design, and Plan is complete for the bounded `F-OPERATOR-DECISIONS-NOTIFICATIONS-0001` scope.

Validated functional candidate: `f5348697a1dc53d674af8d36d7e481f5829062c8`.

PR: `#230`.

This is Developer implementation output only. It is not independent Code Review, QA, Product Acceptance, Issue #221 real-runtime fault-injection evidence, second-adapter evidence, #218 release-ledger completion, or v0.3 release-readiness approval. `code-gate` remains PENDING until an independent Reviewer evaluates the actual candidate.

## Durable Decision and Notification state

The accepted protected Operator Store is extended rather than replaced. The implementation adds immutable Decision and Notification records under the existing `state/operator/v1` authority plus append-only Operation journal facts for:

- `decision.requested`;
- `decision.responded`;
- `decision.expired`;
- `decision.superseded`;
- `decision.authorization-consumed`;
- `notification.created`;
- `notification.acknowledged`.

`operator_store_model.py` keeps these records create-once, extends the deterministic Operation projection with bounded Decision/Notification indexes, and preserves the existing protected-ref CAS, candidate, generation, Effect Lineage, launch, Persist, cancellation, and Feature lifecycle authority boundaries.

The Design Review expiry note is implemented explicitly: trusted-clock checks reject expired response/authorization use immediately, while `decision.expired` is an idempotent durable fact produced by trusted reconciliation. Reducer rebuild remains a pure function of durable Store history and does not depend on wall-clock time.

## Protected Decision policy and authorization

`operator_decision_policy.py` adds protected trusted policy verification and bounded effective-policy behavior. Decision creation/response/authorization consumption binds exact current trusted policy material and rejects authority-bearing drift.

Feature-side restrictions are tighten-only. Ordinary client or Worker input cannot select or expand protected policy origin, allowed Decision types/choices, privileged action mappings, responder authority, state ref, trusted clock, or Store write authority.

A resolved Decision is bounded evidence for exactly one verified choice/action. It is not a Worker-launch or Persist linearization point. Existing `dispatch.launch.authorized`, Effect Lineage gating, cancellation/supersession fences, and `persist.linearized` remain independently required.

## Canonical production backends and trusted identity

`operator_decision_backends.py` backs the existing `ai-sdlc.operator/v1` capabilities:

- `operator.inbox`;
- `decision.list`;
- `decision.respond`;
- `notification.list`;
- `notification.ack`.

Read/write scope and responder identity come from trusted runtime context. Canonical `client_identity` remains a client assertion and is never promoted into authorization merely because it validates against the request schema. Mismatched trusted/client identity fails closed.

The existing MCP surface remains read-only; adding canonical write-capable backends does not register write-capable MCP tools.

## Notification Outbox and inbox

The implementation supports the frozen v0.3 Notification types:

- `decision.requested`;
- `operation.blocked`;
- `operation.completed`;
- `authorization.expiring`.

Notification identity is deterministic from trusted semantic trigger material. Duplicate production converges on the existing immutable record; the first durable `created_at` remains authoritative even if a later reconcile tick observes the same semantic trigger at a different time. Conflicting reuse still fails closed.

`notification.ack` is exact-item, append-only, idempotent acknowledgement and cannot mutate Feature state or grant authorization.

`operator.inbox` is a pure trusted-scope projection over unfinished Operations, pending Decisions, and unread Notifications reconstructed from protected Store facts, so new sessions do not depend on chat history or mutable process memory.

## Vertical runtime composition

`operator_vertical_runtime.py` composes the Decision/Notification coordinator and canonical backends with the existing protected vertical runtime rather than introducing a second write path. Decision/Notification writes continue through the same trusted Operator Store `commit_replanned(...)` CAS boundary.

No Decision/Notification path can directly edit the authoritative Feature Manifest, PASS a Gate, synthesize independent Reviewer/QA/Product evidence, launch an external Worker without the existing launch fences, or Persist Feature state without the existing Persist linearization contract.

## Deterministic validation

`validate_operator_decisions_notifications.py` is wired into authoritative `scripts/validate.py` and exercises, among other cases:

- durable pending Decision reconstruction and new-session inbox discovery;
- exact-choice response and duplicate/conflicting response behavior;
- wrong identity, revision, candidate, generation, expiry, cancellation and policy-drift fail-closed behavior;
- Feature-policy expansion rejection;
- bounded authorization consumption with re-validation;
- trusted reconcile materialization of expiry;
- all required Notification types;
- semantic Notification deduplication across later reconcile timestamps;
- idempotent acknowledgement and unread projection;
- deterministic projection rebuild and CAS/re-plan behavior;
- canonical backend request/response and trusted-scope behavior;
- regression aggregation with existing Operator Store, Vertical Loop, Effect Lineage, lifecycle, security, cross-repository, MCP and Public Runtime suites.

Exact functional-head workflow evidence is recorded in `docs/features/F-OPERATOR-DECISIONS-NOTIFICATIONS-0001/evidence/implementation-verification.md`.

## Explicit non-scope

This implementation does not complete Issue #221 real-runtime fault injection, add the second materially independent supported write-capable adapter, complete #218 release evidence accounting, publish `VERSION`, create final `release/v0.3.0.yaml`, or claim overall v0.3 release readiness.

Next legal authority after trusted `IMPL-DONE` materialization is an independent Code Reviewer bound to the actual PR head.