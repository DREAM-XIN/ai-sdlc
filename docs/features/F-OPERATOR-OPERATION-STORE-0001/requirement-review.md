# Requirement Review — F-OPERATOR-OPERATION-STORE-0001

## Role

Independent Requirement Reviewer.

## Authoritative review state

- Feature: `F-OPERATOR-OPERATION-STORE-0001`
- Issue: #214
- review-start revision: `3`
- `requirement-review: WORKING`
- `requirement-gate: PENDING`
- reviewed artifact: `requirement-v1` (draft)
- normative upstream: frozen `docs/v0.3-release-spec.md` and canonical `ai-sdlc.operator/v1` contract on current `main`

## Verdict

**REWORK**

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

## MAJOR-1 — `operator.inbox` partial backing would be semantically dishonest under the frozen canonical schema

### Requirement text under review

`R-API-004` requires this Feature to make `operator.inbox` discover unfinished Operations while Decision and Notification portions remain unbacked and merely "honest about unavailable/unbacked state".

### Canonical contract reality

The frozen `operator.inbox` response schema requires one successful result to contain all three arrays:

- `operations`
- `decisions`
- `notifications`

It exposes no per-section availability marker, error object, or partial-result semantics.

The canonical dispatcher also treats capability availability at the whole-capability backend level.

### Why this is MAJOR

Within this Feature's approved implementation-order boundary, Decision and Notification durable backing belongs to a later workstream. Therefore making `operator.inbox` globally available now would force one of two invalid outcomes:

1. return empty `decisions` / `notifications`, which falsely means "there are no pending items" rather than "this data source is not implemented"; or
2. change the canonical response/availability contract inside this Feature, silently redefining an already approved upstream API.

Either outcome violates the frozen requirements for honest capability availability and stable canonical API semantics.

### Required remediation

The Requirement must separate the internal durable query primitive from canonical `operator.inbox` availability:

- this Feature MAY/SHALL provide a trusted internal store query capable of enumerating unfinished Operations for later inbox composition;
- canonical `operation.start`, `operation.status`, and `operation.cancel` may gain durable backing within this Feature;
- canonical `operator.inbox` as a whole MUST remain `CAPABILITY_UNAVAILABLE` until a later reviewed workstream can truthfully satisfy its complete operations + decisions + notifications semantics, unless a separately reviewed canonical API change introduces explicit partial-availability semantics;
- this Feature MUST NOT return empty Decision/Notification arrays as a substitute for missing backing.

`operation.resume`, Decision writes and Notification writes remain deferred as already stated.

## Other review findings

### Durable state authority — PASS

The Requirement correctly keeps Operator state off the Feature branch, selects the state ref from trusted configuration, and withholds state-ref write authority from role workers.

### Append-only journal / deterministic projection — PASS

The Requirement correctly distinguishes immutable Operation Events/reservations/claims from replaceable cached projections and requires deterministic rebuild.

### CAS concurrency semantics — PASS

The Requirement requires exact-ref CAS plus semantic re-read/re-evaluation rather than stale byte replay.

### Generation and side-effect identity — PASS

The Requirement correctly separates Operation generation from generation-independent semantic-effect identity and preserves one stable external dispatch key across takeover.

### Launch linearization — PASS

The cancellation/supersession ordering around `dispatch.launch.authorized` matches the frozen Release Spec, including the narrow allowance for an exact pre-authorized dispatch to complete after cancellation.

### External receipt recovery — PASS

`NOT_LAUNCHED`, `LAUNCHED`, and `UNKNOWN` semantics are correctly fail-closed; missing local acknowledgement is not treated as proof of non-launch.

### UNKNOWN inheritance — PASS

The Requirement correctly preserves unresolved reservations and the same external key across generation takeover.

### Persist linearization — PASS

Launch authorization and Feature Persist authorization remain separate durable boundaries. Lost acknowledgement recovery is correctly tied to exact Event/receipt correlation.

### Scope control — PASS

The Requirement does not improperly absorb the full role loop, Worker Result translators, Decision/Notification UX, project takeover, or final release authority.

## Gate recommendation

`requirement-gate`: **REWORK / remain PENDING**.

Product should perform the bounded remediation above, then a fresh independent Requirement Re-review must decide whether `requirement-v1` can be approved.
