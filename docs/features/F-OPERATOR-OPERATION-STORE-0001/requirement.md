# Requirement — F-OPERATOR-OPERATION-STORE-0001

## 1. Purpose

Implement the durable trusted Operation Store and dispatch-safety substrate required by the frozen AI-SDLC v0.3 Release Spec before the automated Developer → Reviewer → Remediation → Re-review → QA vertical loop is introduced.

This Feature turns canonical Operation contracts from schema-only placeholders into bounded durable behavior where the store itself is sufficient, while preserving the rule that Feature lifecycle truth remains authoritative outside the Operator Store.

## 2. Normative upstream

This Requirement consumes, without redefining:

- `docs/v0.3-release-spec.md`;
- tracking issue #205 implementation order;
- `ai-sdlc.operator/v1` from `F-OPERATOR-CANONICAL-API-0001`;
- the supported MCP read-only adapter from `F-OPERATOR-MCP-ADAPTER-0001`;
- existing Feature Event + trusted Persist authority and protected-main policies.

If this Requirement conflicts with the frozen Release Spec, the Release Spec wins.

## 3. Product outcome

After this Feature, trusted Operator runtime code SHALL be able to create, read, update by append-only events, rebuild, claim, fence, cancel, and recover durable Operation state using repository-backed trusted storage that is independent of the target Feature branch.

The store SHALL be safe under retries, concurrent writers, Operation generation takeover, duplicated callbacks, ambiguous external launch receipts, and lost acknowledgements.

The Feature SHALL NOT yet automate the role loop or synthesize lifecycle Gate authority.

## 4. Required durable state model

### R-STORE-001 — Protected Operator state location

Operator state SHALL be persisted on a dedicated trusted control-plane state ref selected by trusted installation/control configuration, conceptually `refs/heads/ai-sdlc-operator-state`.

The target Feature branch SHALL NOT select, redirect, or mutate the Operator state ref.

Role workers SHALL NOT require or receive credentials capable of writing this state ref.

The implementation MAY use an injectable repository/state-ref backend for deterministic tests, but production wiring SHALL preserve the same trust boundary.

### R-STORE-002 — Versioned append-only layout

The v1 store SHALL support the normative logical layout:

```text
state/operator/v1/
├── operations/<operation-id>/events/<sequence>-<event-id>.json
├── reservations/external/<semantic-effect-key>.json
├── claims/dispatch/<dispatch-claim-id>.json
├── claims/feature/<target-repo-hash>/<feature-id>.json
├── decisions/<decision-id>.json
├── notifications/<notification-id>.json
└── projections/<operation-id>.json
```

This Feature SHALL implement only the Operation/reservation/claim/projection records required by its scope. Decision and Notification paths may remain reserved/unbacked until the later workstream.

Operation Event schema version SHALL be `ai-sdlc.operation-event/v1`.

### R-STORE-003 — Append-only event immutability

An already committed Operation Event identity/content SHALL NOT be overwritten through the trusted store API.

Equivalent idempotent reapplication MAY resolve as already applied; conflicting reuse of an existing immutable event identity SHALL fail closed with a structured conflict/error result.

Consumed semantic reservations and claims SHALL not be silently rewritten into different identities or semantics.

### R-STORE-004 — Deterministic projection

Current Operation state SHALL be derivable deterministically from append-only Operation Events plus the normative immutable reservation/claim records.

Cached projections MAY be replaced for efficiency, but deleting a cached projection and rebuilding from durable history SHALL produce an equivalent canonical Operation state.

Projection correctness SHALL NOT depend on chat history, process-local memory, or an unjournaled mutable flag.

## 5. Repository CAS and concurrency

### R-CAS-001 — Compare-and-set state-ref writes

Every trusted multi-record state mutation SHALL use Git ref compare-and-set semantics:

1. read exact state-ref SHA;
2. construct the bounded update from that exact state tree;
3. attempt to update the state ref only if the expected SHA is still current;
4. on conflict, re-read authoritative store state and re-evaluate the semantic operation before retrying.

Blind force-push or last-writer-wins behavior is prohibited.

### R-CAS-002 — Semantic re-evaluation after conflict

A CAS conflict retry SHALL NOT merely replay stale bytes. It SHALL re-evaluate active generation, cancellation/supersession state, reservation ownership, expected Feature revision/stage bindings, and any candidate binding relevant to the requested mutation.

The deterministic test backend SHALL be able to inject CAS conflicts to prove this behavior.

## 6. Operation identity and active-generation ownership

### R-OP-001 — Durable Operation identity

Each Operation SHALL have a stable `operation_id`, generation number, target repository, Feature id where applicable, creation identity, and durable event history.

Operation generation identifies the active orchestration owner and SHALL NOT be used as a substitute for semantic side-effect identity.

### R-OP-002 — One active generation per Feature

For one target repository + Feature, at most one nonterminal Operation generation may own automatic progression.

`operation.start` SHALL atomically claim the active-operation slot.

Equivalent starts for the same Feature and compatible intent SHALL converge to the existing active Operation rather than creating parallel active generations.

A conflicting start that cannot safely converge SHALL return a structured bounded error rather than create concurrent ownership.

### R-OP-003 — Generation takeover

A trusted takeover MAY supersede generation `G` with `G+1` only through one atomic state transition that records the supersession and fences `G` from new orchestration decisions.

Generation takeover SHALL preserve unresolved semantic-effect reservations as specified below.

This Feature implements the durable takeover primitive; policy deciding when automated recovery may invoke takeover remains a later recovery/orchestration concern.

## 7. Semantic effect reservation and dispatch claims

### R-DISPATCH-001 — Generation-independent semantic effect key

Potential external Worker side effects SHALL be represented by a durable semantic-effect reservation whose identity does not include Operation generation.

The semantic effect key SHALL bind at least:

- target repository;
- Feature id;
- expected Feature revision;
- current stage;
- task identity;
- role;
- candidate head SHA when candidate-bound.

Equivalent semantic tasks SHALL converge on the same semantic-effect reservation.

### R-DISPATCH-002 — Stable external dispatch key

Each semantic-effect reservation SHALL derive or permanently bind exactly one stable `external_dispatch_key`.

Generation takeover SHALL NOT create a new external dispatch key for the same unresolved semantic effect.

### R-DISPATCH-003 — Generation-specific claim

A generation-specific dispatch claim SHALL reference the generation-independent semantic-effect reservation.

Before launch processing, the current generation SHALL atomically claim the reservation.

Equivalent retries SHALL resolve to the existing claim or a structured `ALREADY_CLAIMED`-equivalent result and SHALL NOT generate a second external dispatch identity.

### R-DISPATCH-004 — One external identity invariant

For one semantic-effect key, at most one external dispatch identity may ever be valid across retries and generation takeover.

This invariant SHALL be deterministic and testable without depending on best-effort timing.

## 8. Launch authorization and fencing

### R-LAUNCH-001 — Preconditions before launch authorization

Before recording launch authorization, trusted code SHALL re-read durable Operation state and verify:

- generation is current;
- Operation is not cancelled/superseded;
- dispatch claim owns the semantic reservation;
- expected Feature revision/stage bindings are still valid when supplied;
- candidate head binding is still valid when candidate-bound;
- trusted policy/credential preconditions represented by the caller remain satisfied.

The store primitive SHALL accept trusted verification results/bindings; it SHALL NOT let Worker payload self-assert them.

### R-LAUNCH-002 — Launch linearization

`dispatch.launch.authorized` SHALL be the durable launch linearization point.

It SHALL bind at least:

- operation id/generation;
- semantic-effect key;
- external dispatch key;
- dispatch id;
- Feature id;
- expected revision;
- stage;
- role;
- candidate head SHA when applicable;
- authorization timestamp.

No external launch SHALL be considered authorized merely because an in-memory runner decided to launch.

### R-LAUNCH-003 — Cancellation/supersession ordering

If cancellation/supersession is durable before launch authorization, authorization SHALL fail and no new launch may occur.

If launch authorization is durable first, later cancellation/supersession SHALL not retroactively revoke that exact already-authorized external side effect, but SHALL prevent later unlinearized work.

A stale runner after cancellation MAY only complete/correlate the exact pre-authorized dispatch using its existing external key; it SHALL NOT authorize a different dispatch.

## 9. External launch receipts and callback correlation

### R-RECEIPT-001 — Receipt lookup model

The store-facing recovery contract SHALL model external launch lookup states:

```text
NOT_LAUNCHED
LAUNCHED
UNKNOWN
```

The external runtime/gateway implementation itself may be supplied by later orchestration work, but this Feature SHALL provide the durable correlation records and deterministic state machine necessary to consume these lookup results safely.

### R-RECEIPT-002 — Recovery behavior

For the existing semantic reservation/external key:

- `NOT_LAUNCHED` permits retry of the exact semantic launch only after the current generation re-passes required authorization/fences;
- `LAUNCHED` adopts/correlates the existing trusted launch receipt and SHALL NOT launch again;
- `UNKNOWN` SHALL block speculative relaunch.

Missing local `worker.dispatched` or callback records SHALL NOT be interpreted as `NOT_LAUNCHED`.

### R-RECEIPT-003 — Callback replay safety

Repeated equivalent callback/receipt correlation SHALL be idempotent and shall not create duplicate semantic effects, duplicate dispatch identities, or duplicate projection transitions.

Conflicting callback identity/generation/semantic-key bindings SHALL fail closed and remain auditable.

### R-RECEIPT-004 — UNKNOWN inheritance

An unresolved `UNKNOWN` semantic reservation SHALL survive Operation generation takeover unchanged.

Generation `G+1` SHALL inherit the exact reservation and `external_dispatch_key`; it SHALL NOT create a new key merely because ownership changed.

If authoritative Feature state makes the task obsolete, the reservation SHALL remain correlated/auditable until a trusted bounded retirement/adoption rule proves that no duplicate external side effect can result. This Feature need not implement broad adoption policy.

## 10. Cancellation

### R-CANCEL-001 — Durable Operation cancellation

`operation.cancel` SHALL durably stop automatic progression for the Operation generation. It SHALL NOT mean Feature cancellation.

Cancellation response/state SHALL expose enough information to distinguish work that had already crossed launch or Persist linearization from work that was not yet authorized.

### R-CANCEL-002 — Post-cancellation behavior

After cancellation is durable:

- no new generation-local orchestration decision may be linearized;
- no un-authorized external launch may occur;
- pre-authorized exact dispatch correlation may complete;
- later Worker result records may be retained for audit but SHALL NOT gain Feature Persist authority from the launch alone;
- Feature cancellation remains the existing separate lifecycle operation.

## 11. Persist linearization primitives

### R-PERSIST-001 — Independent Persist boundary

Feature Persist authorization SHALL remain distinct from launch authorization.

The store SHALL support durable journal records for at least:

- `persist.requested`;
- `persist.linearized` (or semantically equivalent authorization receipt);
- `persist.confirmed`.

These records SHALL bind Operation/generation, result/Event identity, expected Feature revision, and candidate head where applicable.

### R-PERSIST-002 — Persist ordering

Before `persist.linearized`, trusted code SHALL re-read current generation/cancel/supersession state and validate the exact Feature/candidate bindings supplied by the trusted caller.

Cancellation/supersession durable before Persist linearization SHALL prevent the Feature write.

Persist linearization durable first MAY allow that exact Event/Persist write to finish after cancellation, but SHALL not authorize subsequent automatic progression.

### R-PERSIST-003 — Lost acknowledgement recovery

If Feature Persist may have succeeded but acknowledgement is lost, recovery SHALL correlate/query the exact Event/receipt before retrying.

Missing local `persist.confirmed` SHALL NOT be proof that Feature Persist failed.

The Feature Store SHALL provide durable correlation state; actual Feature Event translation remains governed by existing/later trusted translator paths.

## 12. Canonical API backing

### R-API-001 — operation.start

The canonical `operation.start` capability SHALL gain real durable backing sufficient to create or converge on an active Operation/Feature claim and return stable Operation identity/status.

It SHALL remain subject to canonical API version, structured error, identity, authorization-context, and idempotency contracts.

### R-API-002 — operation.status

The canonical `operation.status` capability SHALL read the durable/rebuilt Operation projection and expose bounded user-visible state without treating it as Feature lifecycle truth.

### R-API-003 — operation.cancel

The canonical `operation.cancel` capability SHALL perform the durable cancellation semantics in this Requirement and be duplicate-safe.

### R-API-004 — operator.inbox Operations

`operator.inbox` SHALL be able to discover unfinished/blocked/cancel-relevant Operations backed by this store without requiring a prior-chat `operation_id`.

Decision and Notification portions of inbox SHALL remain honest about unavailable/unbacked state until their later workstream.

### R-API-005 — Deferred canonical writes

`operation.resume`, `decision.respond`, and `notification.ack` SHALL NOT be falsely advertised as available solely because this store exists.

`operation.resume` remains deferred until recovery policy/authorization semantics are implemented and independently reviewed.

## 13. Structured errors

Store and canonical boundaries SHALL preserve machine-readable failures. At minimum the implementation SHALL have deterministic mappings for relevant cases among:

- `INVALID_REQUEST`;
- `UNAUTHORIZED` / `POLICY_DENIED` when provided by trusted policy boundary;
- `STALE_REVISION`;
- `ALREADY_CLAIMED`;
- `ALREADY_APPLIED`;
- `SUPERSEDED_GENERATION`;
- `CANCELLED_OPERATION`;
- `EXTERNAL_WAIT`;
- `BLOCKED`;
- `TRANSIENT_FAILURE` for retriable CAS/backend failures;
- `INTERNAL_FAILURE` for bounded unexpected failures.

Error text SHALL not be the machine contract.

## 14. Trust and authority boundaries

### R-AUTH-001

Operation Store code is trusted orchestration infrastructure, not lifecycle authority.

It SHALL NOT directly edit authoritative Feature Manifests or PASS/Waive Gates.

### R-AUTH-002

Worker-supplied payload SHALL NOT be able to override trusted operation id/generation, semantic-effect key, dispatch identity, expected Feature revision, target ref, candidate identity, role, task, or runtime receipt identity used for store mutations.

### R-AUTH-003

Feature branches SHALL NOT expand authorization policy or choose the Operator state ref.

### R-AUTH-004

The Feature SHALL NOT introduce arbitrary Event/Manifest/shell/repository mutation endpoints as substitutes for canonical operations.

## 15. Deterministic verification requirements

Implementation SHALL provide deterministic automated tests/evidence for all of the following:

1. immutable append-only event behavior and conflicting-event rejection;
2. projection rebuild equivalence after deleting/replacing cache;
3. injected CAS conflict followed by state re-read and semantic re-evaluation;
4. equivalent `operation.start` convergence;
5. active-generation exclusivity for one target Feature;
6. semantic-effect-key stability across retries;
7. duplicate dispatch claim convergence / `ALREADY_CLAIMED` behavior;
8. generation takeover preserving semantic reservation and external dispatch key;
9. cancellation before launch authorization preventing launch authorization;
10. launch authorization before cancellation preserving only the exact pre-authorized dispatch identity;
11. deterministic `NOT_LAUNCHED`, `LAUNCHED`, and `UNKNOWN` recovery behavior;
12. `UNKNOWN` inheritance across generation takeover;
13. callback replay idempotency and conflicting-callback rejection;
14. Persist linearization before/after cancellation ordering;
15. lost-Persist-ack correlation using exact Event/receipt identity;
16. stale expected Feature revision rejection where the store mutation is revision-bound;
17. candidate-head mismatch rejection where candidate binding is supplied;
18. canonical `operation.start/status/cancel` backing and structured errors;
19. `operator.inbox` unfinished Operation discovery;
20. `operation.resume` and later Decision/Notification writes remaining honestly unavailable;
21. existing canonical API/MCP conformance regressions;
22. existing Feature lifecycle/Persist/cross-repository/public-runtime validation regressions.

Timing sleeps or flaky race assumptions SHALL NOT be the primary proof of concurrency semantics; tests SHALL use controllable CAS/receipt backends or equivalent deterministic synchronization.

## 16. Backward compatibility

This Feature SHALL preserve:

- existing v0.2 lifecycle/Event/Persist semantics;
- protected-main and cross-repository control behavior;
- canonical API version `ai-sdlc.operator/v1`;
- supported MCP read-only tool surface unless independently approved scope requires only availability changes underneath existing reads;
- existing release/public-runtime validation.

It SHALL NOT change `VERSION` or create/finalize `release/v0.3.0.yaml`.

## 17. Non-goals

This Feature does not claim completion of:

- Developer → Reviewer → Remediation → Re-review → QA orchestration;
- role-specific trusted Worker Result translators;
- actual external worker launcher/gateway product integration beyond the bounded receipt/correlation interface;
- broad recovery policy or `operation.resume` authorization;
- Decision/Authorization persistence/UX;
- Notification Outbox persistence/UX;
- human Acceptance automation;
- project takeover/install/upgrade;
- unattended end-to-end v0.3 dogfood;
- final v0.3 release readiness/publication.

## 18. Acceptance criteria

The Feature is acceptable only when independent review and QA establish that:

- durable state is repository-backed on a trusted non-Feature state ref;
- journal history is append-only and projections rebuild deterministically;
- concurrent writers are fenced with CAS and semantic re-evaluation;
- active Operation generation ownership is exclusive;
- one semantic effect maps to at most one external dispatch identity across takeover;
- launch and Persist linearization ordering is implemented exactly as frozen;
- UNKNOWN state fails closed against speculative relaunch and survives takeover;
- duplicate starts/claims/callbacks/requests are side-effect safe;
- canonical Operation backing is honest and bounded;
- no lifecycle authority, arbitrary mutation, later-workstream behavior, or release-readiness claim leaks into this Feature;
- all required deterministic and repository regression validations pass on the reviewed candidate.
