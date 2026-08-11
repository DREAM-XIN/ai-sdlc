# Plan — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## 1. Role and inputs

Role: Plan Orchestrator / Plan Author.

Inputs:

- approved Requirement v1;
- Requirement Review `4902265577`;
- approved Design v1;
- Design Review `4902329907` (`PASS_WITH_NOTES — 0 BLOCKER / 0 MAJOR / 1 MINOR`);
- current protected v0.3 Release Spec;
- accepted Operation Store, Vertical Loop and Effect Lineage implementation.

This Plan does not approve implementation or any later Gate.

## 2. Implementation objective

Deliver durable trusted production semantics for:

- `operator.inbox`;
- `decision.list`;
- `decision.respond`;
- `notification.list`;
- `notification.ack`;

while preserving Feature Event/Persist lifecycle authority, exact Operation/candidate/generation bindings, cancellation, Effect Lineage, launch and Persist linearization.

## 3. Ordered implementation slices

### P1 — Durable Decision/Notification model

Add versioned Decision/Notification record schemas and a bounded Store model module. Extend the accepted Store immutable-path allowlist only for exact `state/operator/v1/decisions/*.json` and `state/operator/v1/notifications/*.json` records.

Implement deterministic helpers for:

- stable Decision identity;
- stable semantic Notification identity;
- Decision record/path validation;
- Notification record/path validation;
- Decision state rebuild from immutable definition + append-only Operation Events;
- Notification ack state rebuild;
- scoped enumeration.

Acceptance: same durable history always rebuilds the same logical state.

### P2 — Protected Decision policy and tighten-only overlay

Implement `ProtectedDecisionPolicyVerifier` over trusted protected/default-branch/installation policy input. Bind policy ref/epoch/digest, allowed Decision types/choices/responders, exact bounded action mapping, TTL and expiry-warning policy.

Implement a verified Feature restriction overlay that can only reduce choices/responders/TTL and cannot change policy source, state ref, privileged action vocabulary or verifier authority.

Acceptance: expansion and policy/source/digest mismatch fail closed.

### P3 — Decision planners

Implement pure planners for:

- request/create-or-converge;
- exact response;
- expiry materialization;
- supersession;
- optional single-use authorization consumption.

Decision request must atomically append `decision.requested`, create the matching `decision.requested` Notification, and leave the Operation at `NEEDS_USER` when applicable.

`decision.respond` must re-read/revalidate current Feature/ref/candidate/generation/policy/identity/expiry and accept only one exact current allowed choice.

Duplicate equivalent response converges; conflicting response fails closed.

### P4 — Design Review MINOR: deterministic expiry ownership

Make expiry semantics explicit in code:

- response/action safety checks `expires_at` against trusted clock immediately;
- a trusted reconcile/tick appends `decision.expired` exactly once when due;
- reducer output itself does not inspect wall-clock time;
- `authorization.expiring` is emitted by the same trusted time reconcile with a stable semantic trigger identity.

Acceptance: rebuild from unchanged Store history is time-independent; later reconcile appends the next durable fact.

### P5 — Notification planners and triggers

Implement deterministic create/dedupe and exact idempotent ack. Support the four frozen v0.3 types:

- `decision.requested`;
- `operation.blocked`;
- `operation.completed`;
- `authorization.expiring`.

Add bounded reconcile helpers for Operation transition-derived and time-derived Notifications. No model-supplied arbitrary notification type is accepted.

### P6 — Trusted scoped reads and inbox

Implement trusted scope provider/receipt integration and pure reads for:

- pending/current Decision list;
- Notifications with unread state;
- inbox = unfinished Operations + pending Decisions + unread Notifications.

Caller payload cannot broaden repository/installation/user scope. Reads have no Store or Feature side effects.

### P7 — Canonical backends and schemas

Add production backends for the five in-scope canonical capabilities and compose them only when Store protection, policy, Feature/candidate providers and trusted scope dependencies exist.

Preserve `ai-sdlc.operator/v1` request envelopes. Tighten response `items` schemas compatibly and keep MCP adapter read-only.

### P8 — Vertical integration

Add bounded integration helpers so deterministic orchestration can request Decisions, surface `NEEDS_USER`, resume only after current trusted re-evaluation, and produce transition Notifications.

A resolved Decision never directly PASSes a Gate, launches a Worker, creates a successor Effect Lineage key, or replaces `persist.linearized`.

### P9 — Authoritative deterministic validation

Add `validate_operator_decisions_notifications.py` (and narrowly factored policy/runtime validator if useful) covering the Requirement acceptance scenarios and Design Review expiry note. Wire it into `scripts/validate.py`.

Mandatory adversarial cases include:

- exact choice vs generic/fuzzy response rejection;
- stale revision/ref/SHA/generation/policy/expiry/responder;
- Feature overlay expansion rejection;
- duplicate/conflicting responses;
- cancel/response race;
- deterministic expiry materialization;
- all four Notification types and dedupe;
- exact idempotent ack;
- cross-scope denial;
- restart/rebuild/new-session inbox;
- CAS conflict re-plan;
- no bypass of Effect Lineage/launch/Persist;
- canonical capability availability/schema honesty.

### P10 — Regression and implementation evidence

Run authoritative protocol validation and Public Runtime distribution validation. Record exact functional candidate and CI/workflow evidence in `implementation.md` and `evidence/implementation-verification.md`.

Implementation author must stop before Code Gate; fresh independent Code Review is required.

## 4. Expected code surface

Implementation may refine names, but should concentrate changes in:

- `spec/operator/store/` or a narrow Decision/Notification schema namespace;
- `scripts/operator_store_model.py` only for bounded path/reducer integration;
- new Decision/Notification model/planner module(s);
- new protected Decision policy verifier module;
- new canonical backend/runtime composition module or narrow extension of `operator_store_backends.py`;
- bounded Vertical Loop integration points;
- canonical public item/response schemas;
- `scripts/validate_operator_decisions_notifications.py`;
- `scripts/validate.py`.

Avoid unrelated refactors.

## 5. Security invariants to test before IMPL-DONE

- no caller/Worker controls trusted policy, clock, scope, state ref or allowed choices;
- Decision response is exact and bounded, not natural-language ambient authorization;
- policy/Feature/candidate/generation drift invalidates stale authority;
- expired Decisions cannot authorize even before expiry-reconcile materialization;
- Feature branch can only tighten;
- ack cannot grant authorization or mutate Feature lifecycle;
- inbox/list cannot leak outside trusted scope;
- CAS/replay cannot double-resolve or double-notify;
- Decision resolution cannot bypass cancellation, Effect Lineage, `dispatch.launch.authorized`, `persist.linearized`, Reviewer/QA independence or Product Acceptance.

## 6. Completion boundary

Plan completion advances only to `implementation: READY`. Implementation completion must produce a fresh exact candidate and durable evidence, then hand off to an independent Code Reviewer. Feature PASS still does not prove #221, the second materially independent adapter, #218, or v0.3 release readiness.
