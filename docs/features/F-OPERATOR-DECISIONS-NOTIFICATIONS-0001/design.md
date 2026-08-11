# Design — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## 1. Status and role

Role: Architect / Design Author.

Feature: `F-OPERATOR-DECISIONS-NOTIFICATIONS-0001` / Issue #229 / PR #230.

This Design implements the approved Requirement and carries forward Requirement Review `4902265577` (`PASS_WITH_NOTES — 0 BLOCKER / 0 MAJOR / 1 MINOR`). It is not a Design Review, implementation verdict, QA result, Product Acceptance decision, #221 dogfood result, or v0.3 release-readiness claim.

## 2. Design goals

The implementation shall add production-grade durable backing for the already-declared `ai-sdlc.operator/v1` capabilities:

- `operator.inbox`;
- `decision.list`;
- `decision.respond`;
- `notification.list`;
- `notification.ack`.

The design MUST preserve these accepted invariants:

1. Feature Manifest + trusted Feature Event/Persist remain the sole Feature lifecycle authority.
2. Decision/Notification state is Operator orchestration state only.
3. Existing Operation generation, candidate, cancellation, Effect Lineage, launch-linearization, and Persist-linearization fences remain mandatory.
4. A Decision response is bounded evidence for one exact current Decision; it is never ambient permission.
5. Protected Operator Store CAS remains the single durable write serialization boundary for this Feature.
6. Session/chat history is never required to reconstruct pending Decisions, unread Notifications, or unfinished Operations.

## 3. Architectural choice

### 3.1 Extend the accepted Operator Store; do not create a second store

Decision and Notification data live under the existing trusted protected `state/operator/v1` state ref and use the accepted `OperatorStoreRuntime.commit_replanned(...)` path.

The implementation MUST NOT add a mutable database, Feature-branch store, chat-local cache, or client-side authority source.

The existing protected state-ref verification and Git ref CAS contract remains unchanged:

`read exact state ref → verify protection → build semantic plan → CAS → on conflict re-read + re-plan`.

### 3.2 Immutable definitions + append-only Operation facts

The durable model is intentionally asymmetric:

- Decision request definitions are immutable create-once facts.
- Notification creation records are immutable create-once facts.
- Decision response / expiry / supersession / authorization-consumption facts are append-only Operation Events.
- Notification acknowledgement facts are append-only Operation Events.
- Current Decision / Notification / inbox state is a deterministic projection, never mutable authority.

This avoids last-writer-wins mutation of an authority-bearing Decision record and makes duplicate/replay/concurrent behavior auditable.

## 4. Durable state model

### 4.1 Store paths

The existing Store root remains:

```text
state/operator/v1/
```

This Feature adds immutable paths conceptually:

```text
state/operator/v1/
├── decisions/<decision-id>.json
└── notifications/<notification-id>.json
```

The implementation shall extend `operator_store_model.is_immutable_path(...)` so only these exact versioned record paths become create-once trusted artifacts. Feature branches and Workers cannot select alternate roots.

Optional derived indexes/caches MAY be added under an explicitly projection-only namespace, but they must be replaceable, non-authoritative, and exactly rebuildable from immutable records + Operation Events. The initial implementation SHOULD avoid a new cache unless deterministic test evidence demonstrates a need.

### 4.2 Decision request record

Decision request schema: `ai-sdlc.decision/v1`.

An immutable Decision request record contains at least:

```text
schema_version
decision_id
decision_type
target_repository
feature_id
operation_id
operation_generation
expected_revision
target_ref
candidate_pr_number              # optional, when candidate-bound
candidate_head_sha               # optional, when candidate-bound
allowed_choices                  # exact bounded choice ids
trusted_policy_ref
trusted_policy_epoch
trusted_policy_digest
feature_restriction_digest       # optional tighten-only overlay
requested_by
requested_at
expires_at
request_correlation_id
request_evidence_digest
trusted_context_digest
```

`decision_id` is a stable deterministic identity derived from immutable semantic request material, not process/session identity. Equivalent retries converge to the same Decision. Incompatible reuse of the same id fails closed.

### 4.3 Full frozen Decision audit record — Requirement Review MINOR-1 closure

The logical reconstructed Decision audit view MUST expose the complete frozen Release Spec field set, including:

```text
decision_id
decision_type
feature_id
operation_id
operation_generation
expected_revision
target_ref
candidate_head_sha
allowed_choices
trusted_policy_ref_or_digest
requested_by
requested_at
expires_at
status
responded_by_user
responded_via_client
responded_at
selected_choice
```

Because request definitions are immutable, response-time fields are supplied by append-only `decision.responded` facts and merged by the deterministic reducer. The Design therefore preserves every frozen audit field without mutating the original Decision request record.

`responded_by_user`, `responded_via_client`, `responded_at`, and `selected_choice` are explicit persisted audit facts; they MUST NOT be reconstructed from chat text or omitted as an implementation shortcut.

### 4.4 Decision statuses

The deterministic Decision projection supports at least:

```text
PENDING
RESOLVED
EXPIRED
SUPERSEDED
```

Only `PENDING` can accept a first valid response.

Terminal status precedence is deterministic. A stale/cancelled/policy-invalid Decision never becomes current authority merely because a late response arrives.

### 4.5 Notification record

Notification schema: `ai-sdlc.notification/v1`.

An immutable Notification contains at least:

```text
schema_version
notification_id
notification_type
target_repository
feature_id
operation_id
operation_generation
decision_id                      # optional
semantic_notification_key
created_at
summary
correlation_id
trusted_context_digest
```

Minimum supported `notification_type` values are exactly the frozen required set for v0.3:

```text
decision.requested
operation.blocked
operation.completed
authorization.expiring
```

The schema may allow future versioned types only through an independently reviewed extension. v0.3 implementation logic must not silently synthesize arbitrary model-provided notification types.

### 4.6 Semantic notification identity

Duplicate notification production is prevented by a deterministic semantic key derived from trusted trigger material.

Conceptually:

```text
semantic_notification_key = hash(
  notification_type,
  target_repository,
  feature_id,
  operation_id,
  operation_generation,
  decision_id_when_applicable,
  trigger_identity
)
```

`notification_id` is deterministically derived from this key. Replay/restart/concurrent reconcile therefore converges on the same immutable Notification record.

## 5. Operation Journal integration

### 5.1 New append-only event types

The Operation reducer gains bounded typed events, conceptually:

```text
decision.requested
decision.responded
decision.expired
decision.superseded
decision.authorization-consumed
notification.created
notification.acknowledged
```

`notification.created` records correlation to the immutable Notification object. `decision.requested` records correlation to the immutable Decision object.

All event creation uses the existing deterministic `_append_event(...)` / sequence model and existing `ai-sdlc.operation-event/v1` envelope unless Design Review determines a schema bump is necessary. No arbitrary Worker-specified event type is accepted.

### 5.2 Operation projection additions

`rebuild_projection(...)` shall deterministically expose enough bounded state for orchestration, including conceptually:

```text
pending_decision_ids
resolved_decision_ids
unread_notification_ids
```

These fields are Operator projection data only. They do not represent Feature stage/gate state.

When current progression requires a Decision, the Operation remains/enters `NEEDS_USER` with one of the existing bounded reason codes, typically `NEEDS_AUTHORIZATION`, `NEEDS_CLARIFICATION`, or `NEEDS_ACCEPTANCE`.

A Decision response does not itself launch a Worker or Persist a Feature Event.

## 6. Trusted authorization policy

### 6.1 Protected policy verifier

Introduce a production verifier analogous to the accepted `ProtectedEffectResolutionPolicyVerifier`, conceptually:

```text
ProtectedDecisionPolicyVerifier.verify_current(...)
    -> VerifiedDecisionPolicy
```

Policy schema: `ai-sdlc.decision-authorization-policy/v1`.

The verifier is trusted runtime composition state and re-reads current policy on every authority-bearing Decision creation/response/use. Ordinary canonical requests never supply the verifier or current authority object.

Allowed policy origins are only:

```text
protected://...
default-branch://...
installation://...
```

or an equivalent trusted control-repository representation already accepted by repository policy.

A verified policy binds at least:

```text
policy_ref
policy_epoch
policy_digest
operation_profile
allowed_decision_types
allowed_choices_by_type
allowed_responder identities/roles
choice -> exact bounded action mapping
maximum TTL / expiry policy
authorization-expiring lead-time policy
```

### 6.2 Feature-branch tighten-only overlay

A Feature branch may provide an optional restriction overlay. Trusted composition computes an effective policy only if the overlay is a mathematical subset/tightening of the protected base policy.

Examples of permitted tightening:

- fewer allowed choices;
- fewer responders;
- shorter expiry;
- requiring an extra already-supported approval condition.

Expansion attempts fail closed with `POLICY_DENIED`; they are not silently ignored.

The overlay cannot change:

- policy source identity;
- credential scope;
- state ref;
- trusted resolver/verifier implementation;
- privileged action vocabulary;
- protected maximum allowed choice set.

The Decision record binds both current protected policy digest/epoch and the effective restriction digest when present.

### 6.3 Policy drift

At response/application time, current protected policy and current effective restriction are re-read.

A changed authority-bearing policy epoch/digest makes an old Decision stale for authorization. The old response cannot be rebound to the new policy. Recovery may supersede the Decision and issue a fresh bounded Decision if current orchestration still needs one.

## 7. Decision creation

### 7.1 Trusted planner

Introduce a pure semantic planner conceptually:

```text
plan_decision_request(snapshot, trusted_feature, verified_policy, ...)
```

The planner:

1. revalidates Operation id/generation and nonterminal state;
2. binds exact target repository / Feature / revision / ref;
3. binds candidate PR/head when the requested action is candidate-bound;
4. verifies the requested Decision type is allowed by protected policy;
5. derives exact allowed choices from trusted policy, then applies any tighten-only overlay;
6. derives deterministic Decision identity;
7. creates the immutable Decision definition if absent;
8. appends `decision.requested`;
9. creates the deterministic `decision.requested` Notification in the same Store mutation plan;
10. updates the rebuildable Operation projection to `NEEDS_USER` as appropriate.

All of the above commits under one protected state-ref CAS.

An equivalent duplicate request converges. A conflicting duplicate identity fails closed.

### 7.2 Worker boundary

Workers may return a typed indication such as “user input required”, but they cannot choose:

- Decision id;
- Decision type authority;
- allowed choices;
- responder authorization;
- policy ref/digest;
- expiry;
- Feature/candidate/generation bindings.

A trusted role/orchestration translator converts a valid bounded Worker outcome into a trusted Decision request proposal before the Store planner is invoked.

## 8. `decision.respond`

### 8.1 Preserve canonical request compatibility

The existing request remains:

```json
{"decision_id":"...","response":"..."}
```

`response` is treated as one exact allowed choice identifier. The backend performs no free-form model interpretation and no fuzzy “yes means approve” conversion.

A client may map natural language to a concrete pending Decision and explicit choice before calling the canonical API, but the trusted backend only accepts exact bounded choice identity.

### 8.2 Response verification sequence

`DecisionRespondBackend` resolves all authority from trusted state:

1. load immutable Decision by `decision_id`;
2. derive repository / Feature / Operation from that record rather than caller payload;
3. verify trusted invocation identity and client identity;
4. read current Operation projection and require the bound generation/current ownership;
5. re-read current trusted Feature revision/ref/stage;
6. re-read current candidate head when candidate-bound;
7. re-read current protected Decision policy + tighten-only restriction;
8. require exact bound policy epoch/digest/restriction digest;
9. verify expiry using trusted runtime clock;
10. require current Decision state `PENDING`;
11. require exact response membership in current allowed choices;
12. verify responder is authorized for that exact Decision type/choice;
13. append one immutable-equivalent `decision.responded` Operation fact with the full response audit fields;
14. CAS commit; on conflict re-read and re-evaluate.

Stale revision/ref/SHA/generation, expiry, cancellation, supersession, policy drift, wrong responder, or wrong choice fails closed.

### 8.3 Duplicate response semantics

An equivalent repeated response by the same authorized identity to the already-resolved exact Decision returns deterministic resolved/already-applied semantics without duplicate progression.

A conflicting second choice or incompatible responder attempt is rejected.

### 8.4 No ambient authorization

`decision.responded` is not itself a Worker launch or Feature Persist authorization.

For an authorization-bearing choice, later trusted orchestration obtains a bounded authorization view from the resolved Decision and MUST re-check current bindings before the exact action. If the action is single-use, `decision.authorization-consumed` is appended atomically with the trusted orchestration decision that consumes it, or before the external/Persist-specific existing linearization step as required by that action.

Existing `dispatch.launch.authorized` and `persist.linearized` remain the only launch/Persist linearization points.

## 9. Notification production

### 9.1 Trigger ownership

Notification triggers are deterministic trusted runtime rules, not model prose.

- `decision.requested`: emitted atomically with a new Decision request.
- `operation.blocked`: emitted when deterministic Operation projection first enters the corresponding blocked semantic condition.
- `operation.completed`: emitted when deterministic Operation projection reaches `DONE`.
- `authorization.expiring`: emitted when a pending authorization-bearing Decision enters the protected policy's expiry-warning window.

Each trigger derives a stable semantic notification identity so repeated reconciliation is idempotent.

### 9.2 Time-based expiry warning

`authorization.expiring` may require a trusted reconcile/tick. The clock is an injected trusted runtime dependency. A client-supplied timestamp cannot create or suppress the warning.

One semantic warning per configured trigger window is allowed unless a future reviewed policy explicitly versions multiple warning windows.

## 10. `notification.ack`

The existing canonical request identifies only `notification_id`. The backend derives all authority from the immutable Notification and trusted invocation context.

Verification:

1. load exact Notification;
2. verify trusted repository/identity scope;
3. identify owning Operation/generation from the record;
4. reject cross-scope access;
5. append `notification.acknowledged` with `acknowledged_by_user`, `acknowledged_via_client`, `acknowledged_at`, and stable acknowledgement identity;
6. CAS commit/re-plan on conflict.

Equivalent repeated acknowledgement is idempotent. Ack never changes Feature Manifest state, grants authorization, acknowledges a different notification, or acknowledges future notifications.

## 11. Read services and trusted scope

### 11.1 Trusted scope provider

Reads use trusted runtime scope, not caller-selected identifiers.

Introduce a bounded trusted scope object/provider conceptually containing:

```text
installation/control identity
allowed repositories
trusted user identity
trusted client identity
```

A missing/invalid scope makes the backend unavailable or returns `UNAUTHORIZED`; it does not fall back to global listing.

### 11.2 `decision.list`

Returns current Decision projections in trusted scope. Default v0.3 behavior SHOULD prioritize pending Decisions while still permitting the response schema to expose bounded resolved audit rows if the existing schema/Design Review accepts it.

No request payload expansion is required.

### 11.3 `notification.list`

Returns Notifications in trusted scope with deterministic acknowledgement state. The release-required new-session path MUST include unread Notifications.

### 11.4 `operator.inbox`

`operator.inbox` is a pure read projection combining:

- unfinished/recoverable Operations in trusted scope;
- pending Decisions in trusted scope;
- unread Notifications in trusted scope.

It performs no Store mutation, no Feature mutation, no auto-ack, and no auto-resume.

A process restart/new client session produces the same logical inbox from the protected Store.

## 12. Canonical API schema strategy

The API version remains `ai-sdlc.operator/v1`.

The existing request envelopes remain compatible:

- `decision.list` empty payload;
- `decision.respond` = `decision_id + response`;
- `notification.list` empty payload;
- `notification.ack` = `notification_id`;
- `operator.inbox` existing payload contract.

Current response arrays are weakly typed. This Feature SHOULD add explicit reusable Decision/Notification public item schemas and bind array `items` to them without changing the envelope field names. Public schemas expose minimum audit/status data but never credentials, raw policy documents, tokens, or full chat transcripts.

Capability discovery reports these backends `AVAILABLE` only when required trusted Store/protection/scope/policy dependencies are configured and valid.

The existing read-only MCP adapter remains read-only. Canonical write backend availability does not auto-register MCP write tools.

## 13. Production composition

Extend trusted runtime composition rather than construct backend objects in callers.

Conceptually:

```text
TrustedOperatorStoreConfig
  + StateRefProtectionVerifier
  + TrustedFeatureSnapshotProvider
  + TrustedCandidateHeadProvider
  + ProtectedDecisionPolicyVerifier
  + TrustedScopeProvider
  + trusted clock
        ↓
DecisionNotificationRuntime
        ↓
Store-backed canonical backends
```

`store_backends(...)` or a narrowly factored composition helper registers:

```text
operator.inbox
decision.list
decision.respond
notification.list
notification.ack
```

only when production dependencies exist.

No canonical request argument selects state ref, trusted policy source, verifier implementation, clock, or privileged credential.

## 14. Interaction with Vertical Loop

The accepted vertical loop gains only bounded integration points:

- request a Decision when an approved orchestration rule reaches `NEEDS_USER`;
- surface pending Decision id/reason in Operation projection;
- after a valid Decision response, `operation.resume` may re-evaluate from durable state;
- notification producers observe deterministic Operation transitions.

The loop does not treat Decision resolution as a Gate verdict. Reviewer/QA/Product independence remains unchanged.

For `NEEDS_ACCEPTANCE`, an AI-generated recommendation can create/surface a Decision, but `decision.respond` alone MUST NOT synthesize Acceptance Evidence or PASS `release-gate`; the trusted Product/Acceptance path must still create the proper lifecycle evidence/event under existing policy.

## 15. Interaction with Effect Lineage and external effects

A resolved Decision cannot manufacture a new semantic reservation, new `external_dispatch_key`, or bypass an unresolved Effect Lineage predecessor.

Any subsequent external action still flows through:

```text
current Feature/candidate/generation check
→ current Decision authorization check when applicable
→ Effect Lineage gate
→ exact reservation
→ claim
→ dispatch.launch.authorized
→ gateway
```

UNKNOWN and launch-authorized predecessor semantics remain unchanged.

## 16. Interaction with cancellation and Persist

A late Decision response after Operation cancellation/supersession cannot restore automatic progression.

If a Decision was resolved before cancellation but its bounded action had not crossed its existing action-specific linearization point, cancellation wins according to the existing Store rules.

Persist remains:

```text
persist.requested
→ fresh revalidation
→ persist.linearized
→ exact Feature write
→ persist.confirmed
```

Decision resolution never substitutes for `persist.linearized`.

## 17. Concurrency and CAS cases

All writes use one semantic planner under current Store CAS. Required conflict behavior:

- two equivalent Decision requests converge;
- two different responses race: one wins; loser re-reads terminal Decision and fails/converges according to semantic equivalence;
- Decision response racing cancellation: whichever CAS wins is followed by fresh re-evaluation; cancellation still fences later automatic action;
- response racing policy/candidate/Feature movement: re-plan detects stale binding and rejects;
- duplicate Notification triggers converge to one immutable notification id;
- two ack writers converge for equivalent ack semantics;
- ack racing new unrelated notification affects only the exact id.

No stale mutation bytes are replayed after CAS loss.

## 18. Recovery and rebuild

A clean process with only the protected Store can reconstruct:

- every Decision request and status;
- full frozen Decision audit response fields;
- every Notification and ack state;
- pending Decisions;
- unread Notifications;
- unfinished Operations;
- combined operator inbox.

Projection caches, if any, may be deleted and rebuilt with identical logical results.

No recovery algorithm may infer authorization from missing data. Missing/ambiguous authority-bearing state fails closed.

## 19. Validation strategy

### 19.1 New authoritative validators

Add deterministic validation scripts, names implementation-reviewable, covering:

- Decision/Notification record schemas and path binding;
- Decision projection rebuild;
- full frozen audit field preservation, explicitly including `requested_by`, `requested_at`, `responded_by_user`, `responded_via_client`, `responded_at`, `selected_choice`;
- protected policy source/digest/epoch verification;
- Feature-branch tighten-only enforcement and expansion rejection;
- exact response choice behavior / no fuzzy natural-language authorization;
- stale revision/ref/candidate/generation/policy/expiry/responder rejection;
- equivalent duplicate response convergence and conflicting response rejection;
- cancellation race safety;
- authorization consumption does not bypass Effect Lineage / launch / Persist fences;
- all four notification trigger types;
- semantic notification deduplication;
- exact idempotent Notification ack;
- trusted-scope isolation;
- new-session `operator.inbox` rebuild;
- CAS conflict re-read/re-plan;
- canonical API response schemas and availability honesty.

These validators MUST be invoked by authoritative `scripts/validate.py`.

### 19.2 Regression suites

At minimum keep green:

- canonical Operator API validation;
- Operation Store validation/runtime validation;
- Vertical Loop validation/reconciliation;
- Effect Lineage / Effect Resolution / migration validation;
- lifecycle and cross-repository validation;
- security validation;
- Public Runtime Distribution validation.

### 19.3 Release boundary

This deterministic Feature validation does not replace Issue #221 real-runtime fault injection/dogfood.

## 20. Implementation decomposition

Recommended implementation slices:

1. Decision/Notification schemas + Store path/model/reducers.
2. Protected Decision Policy verifier + tighten-only policy composition.
3. Pure Decision request/respond/expiry/supersession planners.
4. Notification production/ack planners and semantic dedupe.
5. Trusted scoped read projections + `operator.inbox`.
6. Canonical backends and response schema tightening.
7. Vertical Loop integration and notification triggers.
8. Deterministic validators wired into `scripts/validate.py`.
9. Regression/public-runtime evidence.

## 21. Security review points

Independent Design Review should specifically challenge:

- whether any client/Worker can select policy, scope, allowed choices, clock, or privileged state ref;
- whether a free-form `response` can accidentally become generic approval;
- whether policy drift is checked at response and action-consumption time;
- whether the full Release Spec audit field set is persisted/rebuildable;
- whether Feature-branch overlays are mathematically tighten-only;
- whether Decision response bypasses Effect Lineage, cancellation, launch, or Persist fences;
- whether inbox/list calls can leak cross-repository data;
- whether replay/CAS can create duplicate progression or duplicate notifications;
- whether `NEEDS_ACCEPTANCE` can incorrectly synthesize Product Acceptance authority.

## 22. Explicit non-scope

This Design does not implement or claim:

- a second materially independent AI client adapter;
- MCP write tools;
- Issue #221 real-runtime fault injection completion;
- alternative Store SPI/backend work from #220;
- #218 release evidence ledger synchronization;
- external exactly-once execution;
- generic post-launch revocation;
- final v0.3 publication, VERSION change, or overall release readiness.
