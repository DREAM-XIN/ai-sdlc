# Requirement v2 — F-OPERATOR-OPERATION-STORE-0001

## 1. Purpose

Implement the trusted repository-backed Operation Store and dispatch-safety substrate required by the frozen AI-SDLC v0.3 Release Spec before the automated Developer → Reviewer → Remediation → Re-review → QA vertical loop.

This Requirement supersedes `requirement-v1` for review after Requirement Review MAJOR-1. It preserves the frozen `ai-sdlc.operator/v1` contract and does not introduce partial `operator.inbox` semantics.

## 2. Normative upstream

Normative inputs:

- `docs/v0.3-release-spec.md`;
- tracking issue #205 implementation order;
- `ai-sdlc.operator/v1` from `F-OPERATOR-CANONICAL-API-0001`;
- supported MCP read-only adapter from `F-OPERATOR-MCP-ADAPTER-0001`;
- existing Feature Event + trusted Persist authority.

The frozen Release Spec and approved canonical API take precedence over this Feature.

## 3. Product outcome

Trusted Operator runtime code SHALL be able to create, read, append, rebuild, claim, fence, cancel, and recover durable Operation state using repository-backed trusted storage that is independent of the target Feature branch.

The store SHALL be deterministic and side-effect safe under retries, concurrent writers, generation takeover, duplicate callbacks, ambiguous launch receipts, and lost acknowledgements.

This Feature SHALL NOT automate the role loop, Decision/Notification product behavior, broad recovery policy, or lifecycle Gate authority.

## 4. Trusted storage and append-only journal

### R-STORE-001 — Protected state ref

Operator state SHALL live on a dedicated trusted control-plane state ref selected by trusted installation/control configuration, conceptually `refs/heads/ai-sdlc-operator-state`.

Feature branches SHALL NOT select, redirect, or mutate that ref. Role workers SHALL NOT receive credentials capable of writing it.

### R-STORE-002 — Versioned logical layout

The store SHALL support the v1 logical namespace under `state/operator/v1/`, including:

- Operation Events under `operations/<operation-id>/events/`;
- external semantic-effect reservations;
- generation-specific dispatch claims;
- per-Feature active-operation claims;
- replaceable Operation projections;
- reserved Decision/Notification namespaces that remain unbacked in this Feature.

Operation Event schema version SHALL be `ai-sdlc.operation-event/v1`.

### R-STORE-003 — Immutability

Committed Operation Events, consumed reservations, and claims SHALL NOT be silently overwritten with different identity or semantics.

Equivalent idempotent replay MAY resolve as already applied. Conflicting immutable identity reuse SHALL fail closed.

### R-STORE-004 — Deterministic projection

Operation projection SHALL be deterministically rebuildable from append-only Operation history plus normative immutable reservation/claim state.

Deleting or replacing a projection cache SHALL not change the reconstructed canonical Operation state.

Projection correctness SHALL NOT depend on chat history or process-local memory.

## 5. CAS and concurrency

### R-CAS-001

Every trusted multi-record write SHALL:

1. read exact Operator state-ref SHA;
2. build the bounded update against that exact tree;
3. update the ref only if the expected SHA remains current;
4. on conflict, re-read durable state and semantically re-evaluate before retry.

Force-update / last-writer-wins is prohibited.

### R-CAS-002

Conflict retry SHALL re-evaluate generation ownership, cancellation/supersession, reservation/claim ownership, expected Feature revision/stage, and candidate binding when applicable. Replaying stale bytes is insufficient.

Deterministic tests SHALL inject CAS conflicts.

## 6. Operation identity and active generation

### R-OP-001

Each Operation SHALL have stable `operation_id`, generation, target repository, Feature id where applicable, trusted creation identity, and append-only history.

Generation identifies orchestration ownership, not semantic side-effect identity.

### R-OP-002

For one target repository + Feature, at most one nonterminal Operation generation may own automatic progression.

`operation.start` SHALL atomically claim that slot. Equivalent starts SHALL converge to the existing compatible active Operation. Unsafe conflicting starts SHALL fail with a structured bounded error.

### R-OP-003

Trusted takeover from generation `G` to `G+1` SHALL be atomic, record supersession, fence `G` from new decisions, and preserve unresolved semantic reservations.

This Feature supplies the durable takeover primitive only; policy deciding when to invoke takeover remains later work.

## 7. Semantic-effect reservation and dispatch identity

### R-DISPATCH-001

A potential external Worker launch SHALL have a generation-independent semantic-effect key binding at least:

- target repository;
- Feature id;
- expected Feature revision;
- current stage;
- task identity;
- role;
- candidate head SHA when candidate-bound.

Equivalent semantic work SHALL converge on the same reservation.

### R-DISPATCH-002

Each semantic-effect reservation SHALL derive or permanently bind exactly one stable `external_dispatch_key`.

Generation takeover SHALL NOT create a new key for the same unresolved semantic effect.

### R-DISPATCH-003

A generation-specific dispatch claim SHALL reference the generation-independent reservation. Equivalent retries SHALL converge or return `ALREADY_CLAIMED`; they SHALL NOT create another external dispatch identity.

### R-DISPATCH-004

For one semantic-effect key, at most one external dispatch identity may ever be valid across retries and generation takeover.

## 8. Launch linearization and fencing

### R-LAUNCH-001

Before launch authorization, trusted code SHALL re-read store state and verify:

- current generation;
- not cancelled/superseded;
- dispatch claim ownership;
- expected Feature revision/stage when bound;
- exact candidate head when bound;
- trusted policy/credential preconditions supplied by trusted caller context.

Worker payload SHALL NOT self-assert these trusted bindings.

### R-LAUNCH-002

`dispatch.launch.authorized` SHALL be the durable launch linearization point and bind operation/generation, semantic-effect key, external dispatch key, dispatch id, Feature id, expected revision, stage, role, candidate head when applicable, and authorization time.

### R-LAUNCH-003

Cancellation/supersession durable before launch authorization SHALL prevent launch authorization and any new external launch.

Launch authorization durable first SHALL permit only that exact already-authorized side effect to complete/correlate after later cancellation; later unlinearized work remains fenced.

## 9. External receipt and callback correlation

### R-RECEIPT-001

The durable correlation state machine SHALL consume external lookup states:

- `NOT_LAUNCHED`;
- `LAUNCHED`;
- `UNKNOWN`.

The external runtime/gateway product integration may be later work, but this Feature SHALL provide the trusted durable contract needed to consume those outcomes safely.

### R-RECEIPT-002

For the same reservation/external key:

- `NOT_LAUNCHED` allows retry only after the current generation re-passes authorization/fences;
- `LAUNCHED` adopts/correlates the existing receipt and SHALL NOT relaunch;
- `UNKNOWN` SHALL block speculative relaunch.

Missing local `worker.dispatched` data is never proof of `NOT_LAUNCHED`.

### R-RECEIPT-003

Equivalent callback replay SHALL be idempotent. Conflicting callback identity/generation/semantic bindings SHALL fail closed and remain auditable.

### R-RECEIPT-004

An unresolved `UNKNOWN` reservation SHALL survive generation takeover with exactly the same semantic-effect reservation and `external_dispatch_key`.

Generation change alone SHALL never retire or replace it.

## 10. Cancellation

### R-CANCEL-001

`operation.cancel` SHALL durably stop automatic progression for the Operation generation and SHALL remain distinct from Feature cancellation.

State/result SHALL distinguish work already launch- or Persist-linearized from work not yet authorized.

### R-CANCEL-002

After cancellation:

- no new generation-local decision may linearize;
- no new un-authorized external launch may occur;
- exact pre-authorized dispatch correlation may complete;
- later Worker results may be retained for audit but SHALL NOT gain Feature Persist authority merely from launch authorization.

## 11. Persist linearization primitives

### R-PERSIST-001

Launch authorization and Feature Persist authorization SHALL remain separate boundaries.

The store SHALL support durable `persist.requested`, `persist.linearized` (or equivalent), and `persist.confirmed` records binding Operation/generation, exact result/Event identity, expected Feature revision, and candidate head where applicable.

### R-PERSIST-002

Before Persist linearization, trusted code SHALL re-read generation/cancel/supersession state and validate exact Feature/candidate bindings.

Cancellation/supersession durable first SHALL prevent the Feature write. Persist linearization durable first MAY allow only that exact write to complete after cancellation.

### R-PERSIST-003

If Feature Persist may have succeeded but acknowledgement is lost, recovery SHALL query/correlate the exact Event/receipt before retry. Missing `persist.confirmed` is not proof of Persist failure.

This Feature provides correlation/authorization primitives; Feature Event translation remains under existing/later trusted translator authority.

## 12. Canonical API backing

### R-API-001 — `operation.start`

`operation.start` SHALL gain durable backing sufficient to atomically create/converge on the active Operation claim and return stable Operation identity/status while preserving canonical API versioning, structured errors, idempotency, expected-revision, identity, and trusted authorization-context contracts.

### R-API-002 — `operation.status`

`operation.status` SHALL read the durable/rebuilt projection and SHALL not present Operation projection as Feature lifecycle truth.

### R-API-003 — `operation.cancel`

`operation.cancel` SHALL execute the durable cancellation semantics above and be duplicate-safe.

### R-API-004 — Trusted unfinished-Operation query primitive

The store SHALL expose a trusted internal query capable of enumerating unfinished / blocked / cancellation-relevant Operations so a later complete `operator.inbox` composer can consume them without prior-chat `operation_id` knowledge.

This primitive is not itself a new canonical capability.

### R-API-005 — `operator.inbox` remains unavailable in this Feature

Because the frozen canonical `operator.inbox` success schema requires `operations`, `decisions`, and `notifications` together and provides no per-section partial-availability semantics, this Feature SHALL NOT make canonical `operator.inbox` available while Decision/Notification backing is absent.

It SHALL remain `CAPABILITY_UNAVAILABLE` / honestly unbacked until a later independently reviewed workstream can satisfy the complete inbox contract, unless a separately approved canonical API revision introduces explicit partial-availability semantics.

Returning empty Decision/Notification arrays as a substitute for missing backing is prohibited.

### R-API-006 — Other deferred capabilities

`operation.resume`, `decision.respond`, `notification.ack`, Decision backing, Notification backing, and corresponding inbox composition SHALL remain unavailable in this Feature.

`operation.resume` remains deferred until recovery policy/authorization semantics are independently implemented and reviewed.

## 13. Structured errors

Deterministic mappings SHALL exist for applicable cases among:

- `INVALID_REQUEST`;
- `UNAUTHORIZED` / `POLICY_DENIED` from trusted policy boundary;
- `STALE_REVISION`;
- `ALREADY_CLAIMED`;
- `ALREADY_APPLIED`;
- `SUPERSEDED_GENERATION`;
- `CANCELLED_OPERATION`;
- `EXTERNAL_WAIT`;
- `BLOCKED`;
- `TRANSIENT_FAILURE`;
- `INTERNAL_FAILURE`.

Human-readable text SHALL not be the machine contract.

## 14. Authority boundaries

- Operation Store is trusted orchestration infrastructure, not Feature lifecycle authority.
- It SHALL NOT directly edit authoritative Feature Manifests or PASS/Waive Gates.
- Worker payload SHALL NOT override trusted operation/generation, semantic-effect key, dispatch identity, expected revision/ref, candidate identity, role, task, or runtime receipt identity.
- Feature branches SHALL NOT expand authorization policy or choose the Operator state ref.
- No arbitrary Manifest/Event/shell/repository mutation endpoint may substitute for canonical operations.

## 15. Deterministic verification

Automated evidence SHALL prove at least:

1. append-only immutable event behavior and conflicting-event rejection;
2. projection rebuild equivalence;
3. injected CAS conflict followed by durable re-read + semantic re-evaluation;
4. equivalent `operation.start` convergence;
5. active-generation exclusivity;
6. semantic-effect-key stability across retries;
7. duplicate dispatch claim convergence / `ALREADY_CLAIMED`;
8. generation takeover preserving reservation + external key;
9. cancellation before launch authorization prevents authorization;
10. launch authorization before cancellation preserves only exact pre-authorized identity;
11. deterministic `NOT_LAUNCHED` / `LAUNCHED` / `UNKNOWN` behavior;
12. UNKNOWN inheritance across takeover;
13. callback replay idempotency and conflicting-callback rejection;
14. Persist linearization ordering around cancellation;
15. lost-Persist-ack exact Event/receipt correlation;
16. stale expected Feature revision rejection where bound;
17. candidate-head mismatch rejection where bound;
18. canonical `operation.start/status/cancel` durable backing and structured errors;
19. trusted unfinished-Operation query behavior;
20. canonical `operator.inbox` remaining unavailable while Decision/Notification backing is absent;
21. `operation.resume` and Decision/Notification writes remaining unavailable;
22. canonical API + MCP conformance regressions;
23. lifecycle/Persist/cross-repository/public-runtime regression suites.

Concurrency tests SHALL use controllable CAS/receipt synchronization rather than timing sleeps as primary proof.

## 16. Backward compatibility and non-goals

This Feature SHALL preserve v0.2 lifecycle/Event/Persist behavior, protected-main/cross-repository behavior, `ai-sdlc.operator/v1`, the supported MCP read-only tool surface, and existing release/public-runtime validation.

It SHALL NOT change `VERSION`, finalize `release/v0.3.0.yaml`, or claim completion of:

- Developer → Reviewer → Remediation → Re-review → QA orchestration;
- role-specific Worker Result translators;
- broad recovery / `operation.resume` policy;
- Decision/Authorization persistence/UX;
- Notification Outbox persistence/UX;
- human Acceptance automation;
- project takeover/install/upgrade;
- unattended v0.3 dogfood;
- final v0.3 release readiness/publication.

## 17. Acceptance criteria

The Feature is acceptable only when independent review and QA establish that:

- Operator state is repository-backed on a trusted non-Feature state ref;
- journal history is append-only and projection rebuild deterministic;
- concurrent writers use CAS plus semantic re-evaluation;
- one active Operation generation owns one target Feature;
- one semantic effect maps to at most one external dispatch identity across takeover;
- launch and Persist linearization ordering matches the frozen Release Spec;
- UNKNOWN fails closed and survives takeover;
- duplicate starts/claims/callbacks/requests are side-effect safe;
- `operation.start/status/cancel` backing is honest and bounded;
- unfinished-operation discovery exists internally without falsely enabling canonical `operator.inbox`;
- later-workstream capabilities remain unavailable;
- no lifecycle authority or release-readiness claim leaks into this Feature;
- all required deterministic and repository regression validations pass on the reviewed candidate.
