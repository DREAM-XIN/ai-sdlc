# Requirement — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## 1. Purpose

Complete the durable user-interaction and recovery surface required by the frozen v0.3 Operator contract: bounded Decisions and Authorization, durable Notification Outbox, and new-session `operator.inbox` discovery.

This Feature productizes already-declared `ai-sdlc.operator/v1` capabilities. It must not create a second lifecycle authority or weaken the accepted Operation Store, Vertical Loop, Effect Lineage, candidate, cancellation, launch, or Persist safety boundaries.

## 2. Normative upstream

The implementation shall conform to the current protected `main` versions of:

- `docs/v0.3-release-spec.md`;
- `release/v0.3.0-draft.yaml`;
- the canonical `ai-sdlc.operator/v1` capability registry and schemas;
- accepted Operation Store, Vertical Loop, and Effect Lineage semantics.

The Feature may refine implementation-reviewable record/schema details where the frozen Release Spec intentionally leaves them open, but it shall not silently reinterpret the frozen authorization or notification contract.

## 3. Product outcomes

After this Feature is accepted, a supported trusted Operator runtime shall be able to:

1. durably request a bounded user Decision when progression requires a choice or authorization;
2. discover that pending Decision from a new client session without relying on chat history;
3. accept only a response that is valid for the exact trusted Decision and current bound state;
4. resume or authorize only the exact bounded action permitted by trusted policy, while all existing lifecycle/effect/candidate/cancel fences remain authoritative;
5. durably publish required Notifications and expose unread items after restart/session loss;
6. acknowledge Notifications idempotently without mutating Feature lifecycle authority;
7. expose unfinished Operations, pending Decisions, and unread Notifications through canonical `operator.inbox`.

## 4. Decision model requirements

### 4.1 Durable identity and state

A Decision shall have a stable durable identity and a rebuildable state derived from protected Operator persistence.

A Decision shall bind, when applicable, at least:

- decision id;
- decision type;
- target repository and Feature id;
- Operation id;
- Operation generation;
- expected Feature revision;
- target ref;
- candidate PR/head SHA for candidate-bound work;
- bounded allowed choices;
- trusted authorization/policy ref or digest;
- expiry;
- responder identity on response;
- request/response evidence or correlation identity sufficient for audit and replay safety.

Decision state shall distinguish at minimum pending from resolved/expired/superseded-equivalent terminal states so stale responses cannot become current authority.

### 4.2 Bounded choices

The trusted runtime, not a Feature branch, Worker, callback, or ordinary AI client, shall determine the set of allowed choices.

A response outside the exact current allowed choices shall fail closed.

The existing canonical `decision.respond` request shape may be used compatibly, but a free-form string shall not be interpreted as unbounded natural-language authorization. The trusted backend must resolve the input to one exact allowed choice or reject it.

### 4.3 Fresh-state validation

Before a Decision response can authorize progression, trusted code shall revalidate the current relevant state. A response shall fail closed when any authority-bearing binding is stale or mismatched, including as applicable:

- Feature revision;
- target ref;
- candidate head;
- Operation id/generation;
- Decision state;
- trusted policy ref/digest;
- expiry;
- responder authorization;
- allowed choice.

A stale Decision must not be made valid by restart, takeover, new session, retry, candidate change, or revision change.

## 5. Authorization requirements

### 5.1 Trusted policy sources

Authorization policy may come only from trusted sources allowed by the frozen Release Spec:

- protected default-branch configuration;
- installation-level configuration;
- trusted control-repository policy.

A Feature branch may tighten policy but shall not expand authorization.

Workers, callbacks, target Feature branches, and ordinary AI clients shall not select a broader policy, credential scope, resolver identity, or privileged action.

### 5.2 Authorization is bounded, not generic approval

Generic natural-language approval is not sufficient authority.

A Decision response may authorize only the exact action represented by the current Decision and trusted policy. It shall not create ambient permission for later revisions, candidates, generations, effects, or lifecycle transitions.

### 5.3 Existing fences remain mandatory

Decision resolution does not itself bypass or replace any existing control. Authorized progression must still pass all applicable current checks, including:

- Feature revision/stage validation;
- candidate head validation;
- Operation generation fencing;
- cancellation/supersession rules;
- semantic reservation / Effect Lineage rules;
- launch authorization linearization;
- Persist authorization/linearization;
- independent Gate-role authority boundaries.

Launch authorization does not imply Persist authorization, and a Decision must not collapse those boundaries.

## 6. Canonical capability requirements

The Feature shall provide trusted production backends for the already-declared canonical capabilities in scope:

- `operator.inbox`;
- `decision.list`;
- `decision.respond`;
- `notification.list`;
- `notification.ack`.

All shall preserve `ai-sdlc.operator/v1` envelope, version, structured-error, identity, idempotency, and capability-discovery semantics.

Unavailable or policy-restricted cases shall fail honestly through the existing bounded capability/error model rather than returning fabricated empty success.

## 7. Notification Outbox requirements

### 7.1 Required durable notification types

The durable outbox shall support at least the frozen required types:

- `decision.requested`;
- `operation.blocked`;
- `operation.completed`;
- `authorization.expiring`.

Notifications shall have durable stable identities, creation ordering/correlation sufficient for deterministic rebuild, and unread/acknowledged state.

### 7.2 Acknowledgement semantics

`notification.ack` shall be idempotent for an equivalent acknowledgement and shall not:

- change Feature Manifest lifecycle state;
- grant authorization;
- acknowledge a different notification;
- silently acknowledge future notifications;
- depend on a prior chat session.

Wrong identity or unauthorized access shall fail closed.

### 7.3 Replay and duplicate suppression

Restart, retry, callback replay, projection rebuild, and concurrent resume shall not create duplicate durable notifications for the same semantic notification event beyond the explicitly defined idempotency model.

## 8. `operator.inbox` requirements

A new trusted client session shall be able to discover, without chat-memory dependence:

- unfinished/recoverable Operations relevant to the trusted scope;
- pending Decisions;
- unread Notifications.

The inbox shall be a read projection over authoritative durable state. It shall not itself mutate Operations, Decisions, Notifications, or Feature lifecycle state.

Inbox results must respect trusted repository/installation/identity scope; a caller shall not obtain cross-repository or broader-tenant items merely by supplying identifiers in request payloads.

## 9. Operation integration requirements

### 9.1 Stable stop states

When progression requires user input/authorization, the Operation projection shall expose an appropriate stable `NEEDS_USER` state/reason compatible with the frozen operation-state model rather than spinning, redispatching, or requiring repeated `continue` messages.

When a Decision is validly resolved, the Operation may become resumable only through trusted orchestration logic and existing fences.

### 9.2 Cancel separation

`operation.cancel` remains distinct from Feature cancellation. Decisions and Notifications shall not blur this distinction.

A late Decision response after relevant cancellation shall not restore cancelled launch/Persist authority.

## 10. Persistence, rebuild, and concurrency

Decision and Notification durable facts shall use trusted protected Operator persistence and support deterministic reconstruction after process/session loss.

Required behavior includes:

- append/create-once immutable facts where semantic history must be audited;
- deterministic projection rebuild;
- CAS/conflict detection for concurrent writers;
- re-read/re-plan after conflicts;
- duplicate request/response/ack handling;
- no lost pending Decision or unread Notification after takeover/restart;
- no reliance on mutable in-memory/chat-only state for release-required behavior.

The exact storage layout is a Design decision, but it must preserve the accepted Operation Store authority and protection model.

## 11. Security and authority invariants

The implementation shall demonstrate that:

- Feature Manifest + trusted Feature Event/Persist remain sole Feature lifecycle authority;
- Decision/Notification data are orchestration/user-interaction state only;
- Worker outputs cannot directly create authority-bearing Decisions, expand choices, PASS Gates, or mutate Feature state;
- client-supplied repository/ref/revision/candidate/generation/policy fields are not trusted merely because they match schema;
- responder identity is derived from trusted invocation context where authority depends on identity;
- policy expansion from Feature branch or target repository untrusted content is rejected;
- credentials/tokens are never returned in canonical Decision/Notification payloads.

## 12. Compatibility requirements

The Feature shall preserve the canonical API id `ai-sdlc.operator/v1` unless an independently reviewed protocol change proves a breaking version bump is required.

Existing read-only MCP behavior shall remain truthful: adding production backends to the canonical registry does not automatically register write-capable MCP tools.

Existing Operation Store, Vertical Loop, Effect Lineage, lifecycle, cross-repository, security, and public-runtime behavior shall remain regression-green.

## 13. Deterministic acceptance scenarios

Implementation and independent QA shall cover at least:

1. pending Decision survives process/session restart and appears in `decision.list` and `operator.inbox`;
2. valid exact allowed choice resolves once and an equivalent duplicate response is idempotent or returns the defined already-resolved semantic result without duplicate progression;
3. wrong Decision id/choice/responder fails closed;
4. stale Feature revision response rejected;
5. wrong target ref rejected;
6. wrong candidate head rejected for candidate-bound Decision;
7. wrong Operation generation rejected;
8. expired Decision rejected;
9. trusted policy digest/epoch change invalidates stale authorization where policy binding is authority-bearing;
10. Feature-branch attempt to expand allowed authorization is rejected;
11. generic natural-language approval cannot authorize an unbounded action;
12. cancellation before Decision response prevents restoration of forbidden launch/Persist authority;
13. `decision.requested` is durable and discoverable after restart;
14. `operation.blocked`, `operation.completed`, and `authorization.expiring` durable notification cases are produced under their defined triggers;
15. duplicate/replayed notification production does not create duplicate semantic outbox items;
16. `notification.ack` is idempotent and only affects the exact notification acknowledgement state;
17. unread Notifications survive restart and disappear from the unread projection only according to the ack contract;
18. `operator.inbox` returns unfinished Operations + pending Decisions + unread Notifications from durable state in trusted scope;
19. inbox read has no lifecycle side effect;
20. concurrent Decision response / resume or concurrent notification ack resolves through CAS/re-read semantics without double progression;
21. projection rebuild produces the same Decision/Notification/inbox state;
22. canonical capability discovery reports availability truthfully and canonical request/response schemas validate;
23. existing Operator Store / Vertical Loop / Effect Lineage / lifecycle / cross-repository / security / Public Runtime suites remain green.

## 14. Feature acceptance criteria

This Feature may be accepted only when independent evidence shows:

- bounded Decision and trusted authorization semantics are durable and fail closed;
- all five in-scope canonical capabilities are backed by trusted production semantics;
- required Notification Outbox types and acknowledgements are durable/rebuildable;
- a new session can discover unfinished Operations, pending Decisions, and unread Notifications;
- stale/expired/wrong-scope authorization attempts are rejected;
- no Decision or Notification path bypasses existing Gate, candidate, generation, Effect Lineage, cancellation, launch, or Persist authority;
- deterministic regression coverage is part of the authoritative validation path, not an optional standalone script.

## 15. Explicit non-scope / release boundary

Feature PASS does not prove:

- the second materially independent supported AI-client adapter;
- Issue #221 real-runtime effect-safety fault injection;
- external exactly-once execution;
- completion of #218 release evidence accounting;
- v0.3 overall release readiness;
- VERSION publication or final release manifest creation.

Those remain separate release workstreams. This Feature supplies durable capability evidence that later release governance and dogfood may cite at the appropriate scope.
