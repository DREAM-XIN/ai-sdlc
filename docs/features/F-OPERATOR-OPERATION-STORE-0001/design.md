# Design — F-OPERATOR-OPERATION-STORE-0001

## 1. Design objective

Implement a repository-backed trusted Operation Store that satisfies approved `requirement-v2` and the frozen v0.3 Release Spec without introducing a second Feature truth or prematurely implementing the later vertical orchestration loop.

The design separates:

1. pure deterministic domain/state-machine logic;
2. logical store layout and record validation;
3. trusted Git state-ref CAS transport;
4. canonical Operator API backends for only the capabilities this Feature may honestly support;
5. deterministic fault-injection validation.

## 2. Primary architecture

### 2.1 Modules

Introduce the following bounded modules:

```text
scripts/
├── operator_store_model.py
├── operator_store.py
├── operator_store_git.py
├── operator_store_backends.py
└── validate_operator_store.py

spec/operator/store/
├── operation-event.schema.json
├── operation-projection.schema.json
├── semantic-reservation.schema.json
├── dispatch-claim-ledger.schema.json
├── feature-claim-ledger.schema.json
└── persist-receipt.schema.json
```

`operator_store_model.py` contains schema-safe datatypes, canonical serialization, semantic keys and deterministic reducers. It performs no Git or network I/O.

`operator_store.py` implements semantic commands against an abstract state snapshot and produces a bounded `StoreMutationPlan`. It performs no Git push and receives no worker-controlled credentials.

`operator_store_git.py` is the trusted adapter that reads one configured control-plane state ref, materializes a plan into a Git tree/commit and performs exact-ref compare-and-set.

`operator_store_backends.py` adapts the approved store commands to canonical `ai-sdlc.operator/v1` backends for `operation.start`, `operation.status`, and `operation.cancel` only.

`validate_operator_store.py` is a deterministic test harness using an in-memory CAS backend plus controlled receipt/callback fixtures.

### 2.2 No Operator database abstraction

This Feature does not introduce SQLite, Redis, hosted database, or process-local authoritative state. The normative implementation remains repository-backed and Git-ref-addressed.

An in-memory backend exists only for deterministic unit validation and shares the exact semantic planner/reducer with the Git adapter.

## 3. Trusted state ref

### 3.1 State-ref selection

Production trusted code receives the Operator state ref only from trusted control-plane configuration. The default reference implementation uses:

```text
refs/heads/ai-sdlc-operator-state
```

No canonical API request, MCP argument, Feature Manifest, Feature branch file, Worker Result, or task payload may provide or override this value.

The Git adapter constructor accepts a state-ref value only as trusted runtime wiring. Tests may inject a synthetic ref.

### 3.2 Separate from Feature branch

The Operator state ref is not the Feature branch and contains only the Operator state tree needed by this subsystem. Feature lifecycle artifacts remain on their normal branches and are accessed through existing trusted Feature reads/Persist paths.

Workers do not receive credentials or tokens capable of updating the Operator state ref.

## 4. Git CAS implementation

### 4.1 Snapshot

`GitStateRefBackend.read()` returns:

```text
StoreSnapshot
- ref_name
- ref_sha | null
- tree_sha | null
- files: path -> bytes/json
```

A missing state ref is represented explicitly rather than as an empty ref with an invented SHA.

### 4.2 Plan materialization

A semantic command first runs against `StoreSnapshot` and returns:

```text
StoreMutationPlan
- expected_ref_sha | null
- logical_result
- creates
- replacements_of_cache_or_append-ledger-files
- invariant_checks
```

The plan may only touch `state/operator/v1/**`.

It may not touch `.github/**`, `state/features/**`, Feature Events, source code, release files, or arbitrary paths.

### 4.3 Commit creation

The trusted Git adapter:

1. creates blobs for changed files;
2. creates a new tree based on the exact snapshot tree;
3. creates a commit whose parent is the exact expected state-ref commit when present;
4. attempts a lease-protected ref update.

Reference implementation may use Git plumbing plus:

```text
git push --force-with-lease=<state-branch>:<expected-sha> origin <new-commit>:<state-ref>
```

For first creation, the lease asserts that the remote ref does not yet exist.

This is CAS, not unbounded force-push: the exact expected remote SHA/nonexistence is mandatory.

### 4.4 CAS conflict

A rejected lease produces `CasConflict`, never silent retry of the stale plan.

The command executor then:

1. re-reads state ref;
2. re-runs the semantic command from original trusted inputs against the new snapshot;
3. returns converged semantic outcome if another writer already performed equivalent work;
4. otherwise produces a new plan or a structured conflict/fence error.

Retry count is bounded. Persistent infrastructure failure maps to `TRANSIENT_FAILURE` or bounded internal failure, never blind infinite retry.

## 5. Logical layout and immutability

### 5.1 Operation Event journal

Operation Events are individual immutable files:

```text
state/operator/v1/operations/<operation-id>/events/<sequence>-<event-id>.json
```

Each event contains at minimum:

```text
schema_version: ai-sdlc.operation-event/v1
operation_id
operation_generation
sequence
event_id
event_type
occurred_at
trusted_context_digest
payload
```

Sequence starts at 1 and is contiguous within one Operation journal. Event identity/content reuse is idempotent only when canonical bytes/digest match exactly; conflicting reuse is rejected.

### 5.2 Projection

Cached projection path:

```text
state/operator/v1/projections/<operation-id>.json
```

Projection contains derived state plus `last_sequence` and `journal_digest`.

It is never trusted independently. Reads may validate cache against journal tail/digest; tests rebuild after cache deletion/corruption replacement.

### 5.3 Fixed-path claim/reservation ledgers

The frozen layout uses fixed logical paths for semantic reservations and feature/dispatch claims. To preserve history immutability while allowing state evolution, each such JSON file is an append-only **logical ledger**:

```text
{
  "schema_version": ".../v1",
  "key": "...",
  "records": [ immutable record 1, immutable record 2, ... ]
}
```

A CAS update may replace the JSON file bytes only by appending a new validated logical record. Existing record bytes/semantic digests must remain identical and in the same order.

The planner rejects truncation, mutation, reordering, or conflicting re-use of a record id.

This pattern is used for:

- feature active-operation claim history;
- semantic-effect reservation observations/state transitions;
- generation-specific dispatch claim history;
- persist authorization/confirmation correlation where a fixed correlation file is appropriate.

Consumed historical records are therefore immutable even though the containing ledger file grows.

## 6. Deterministic operation reducer

`rebuild_projection(events, ledgers)` is pure and deterministic.

Top-level states in this Feature:

- `RUNNING`;
- `WAITING_EXTERNAL` with bounded wait reason;
- `BLOCKED`;
- `DONE`;
- `CANCELLED`.

`NEEDS_USER` is reserved for later Decision/Authorization work unless a store command merely preserves an already supplied bounded state. This Feature does not synthesize user decisions.

Reducer rules reject impossible history such as:

- sequence gaps/duplicates;
- events for another operation id;
- lower generation becoming current after supersession;
- new launch authorization after cancellation/supersession;
- new semantic dispatch identity for an unresolved inherited reservation;
- persist linearization after cancellation when no pre-cancel authorization exists.

## 7. `operation.start`

### 7.1 Inputs

Canonical backend consumes validated canonical request plus trusted context. Expected Feature revision remains required by the canonical API.

Trusted code resolves target repository/Feature identity from request target and trusted authorization context; request cannot select Operator state ref.

### 7.2 Feature claim ledger

Feature claim key is a stable hash of normalized target repository + Feature id.

Feature claim ledger records immutable claim epochs:

```text
claim_id
operation_id
operation_generation
expected_feature_revision
idempotency_key_digest
status: CLAIMED | TERMINAL
recorded_at
```

Current active owner is the newest valid `CLAIMED` record without a corresponding terminal release/terminal Operation state.

### 7.3 Equivalent start convergence

The planner checks, in order:

1. existing idempotency/equivalent start binding;
2. current active feature claim;
3. current authoritative Feature binding supplied by trusted caller.

If an equivalent compatible active Operation exists, it returns that Operation without appending a duplicate semantic start.

If another incompatible nonterminal Operation owns the Feature, return bounded `ALREADY_CLAIMED`/policy-safe conflict.

Otherwise atomically append:

- feature claim record;
- initial `operation.started` journal event;
- projection cache.

All are one Git CAS commit.

## 8. Generation takeover primitive

`plan_takeover()` is trusted-internal and is not exposed as canonical `operation.resume` in this Feature.

It requires current generation and a trusted recovery reason/context.

One CAS commit appends:

- `operation.superseded` for generation G;
- `operation.generation.started` for G+1;
- generation claim record(s) that inherit unresolved semantic reservations.

No semantic-effect key or external dispatch key is recomputed merely because generation changed.

Old generation is fenced by reducer/planner checks for all later decisions.

## 9. Semantic-effect reservation

### 9.1 Key

Use canonical JSON normalization and SHA-256 over exactly:

```text
target_repository
feature_id
expected_revision
current_stage
task_identity
role
candidate_head_sha_or_null
```

Operation id and generation are intentionally excluded.

Normalization is explicit and deterministic; repository names and role/stage ids use canonical case rules defined by existing contracts rather than locale-dependent transforms.

### 9.2 Reservation ledger

Reservation ledger path:

```text
state/operator/v1/reservations/external/<semantic-effect-key>.json
```

First record permanently binds:

- semantic-effect key input digest;
- one `external_dispatch_key`;
- creation operation/generation;
- exact Feature/candidate bindings.

Later records may append observations such as claim transfer, launch authorization reference, receipt state, callback correlation, or trusted retirement. They may never replace the original external key.

## 10. Dispatch claim

Generation-specific dispatch claim id is deterministic from operation id + generation + semantic-effect key.

Claim ledger binds one current generation to the reservation.

Equivalent same-generation claim returns existing claim. Another generation may inherit ownership only as part of a trusted takeover/fencing plan after old-generation supersession is durable.

No inheritance creates a new semantic or external dispatch identity.

## 11. Launch authorization linearization

`plan_authorize_launch()` re-evaluates from the latest snapshot:

- current Operation generation;
- non-cancelled/non-superseded state;
- current dispatch claim ownership;
- semantic reservation identity;
- expected Feature revision/stage verification receipt supplied by trusted caller;
- candidate head verification receipt when candidate-bound;
- bounded trusted policy/credential precondition receipt.

It then atomically appends `dispatch.launch.authorized` and linked ledger observation.

The returned `LaunchAuthorization` is a capability-like receipt containing only the exact already-linearized dispatch identity. External launch code must present that receipt/external key; it cannot use the Store to authorize arbitrary launch data after the fact.

Cancellation durable before this CAS makes authorization fail. Cancellation racing after this CAS cannot revoke that exact authorization.

## 12. External receipt/callback correlation

### 12.1 Receipt interface

Define a trusted interface:

```text
LaunchReceiptLookup.lookup(external_dispatch_key) ->
    NOT_LAUNCHED | LAUNCHED(receipt) | UNKNOWN
```

This Feature ships fixture/in-memory implementations for deterministic tests and the correlation state machine, not the final Worker gateway integration.

### 12.2 Correlation commands

`record_launch_lookup()` and `record_callback()` append immutable observations.

Rules:

- `LAUNCHED`: persist exact trusted receipt identity, never relaunch;
- `NOT_LAUNCHED`: record observation; a future retry still requires current-generation launch fence/authorization logic with the same external key;
- `UNKNOWN`: append blocked observation and project Operation to `BLOCKED` for that semantic effect;
- duplicate same receipt/callback: `ALREADY_APPLIED` semantic success;
- conflicting receipt/callback binding: fail closed/auditable error.

### 12.3 UNKNOWN takeover

Takeover reducer carries unresolved reservation/UNKNOWN status into G+1. Any new dispatch claim for that semantic task references the inherited reservation and exact external key. Planner rejects new reservation creation for an unresolved equivalent semantic key.

## 13. Cancellation

`plan_cancel_operation()` is canonical-backend reachable.

One CAS commit appends `operation.cancelled` and updates projection.

Projection/result includes bounded lists/counts for:

- launch-authorized-but-not-yet-correlated dispatches;
- persist-linearized-but-not-yet-confirmed writes;
- unresolved UNKNOWN reservations.

After cancellation, planners reject all new unlinearized launch/persist decisions. Exact prior launch authorization and exact prior Persist authorization may still be correlated/completed.

Repeated equivalent cancel is idempotent and returns current cancelled state.

## 14. Persist linearization

### 14.1 Records

Provide trusted-internal store commands:

- `record_persist_requested()`;
- `linearize_persist()`;
- `confirm_persist()`;
- `correlate_persist_receipt()`.

They are not arbitrary Feature writers.

### 14.2 Binding

Persist correlation key binds:

```text
operation_id
operation_generation
exact_feature_event_id
expected_feature_revision
target_repository
target_ref
candidate_head_sha_when_applicable
```

`persist.linearized` is committed only after latest store state and trusted Feature/candidate verification receipts pass.

### 14.3 Ordering

Cancellation/supersession durable first causes `linearize_persist()` to fail.

Persist linearization durable first creates an exact authorization receipt that permits only the already-bound Event/Persist operation to complete afterward.

The store never itself edits the Feature Manifest. Existing/later trusted Persist code consumes the exact authorization receipt.

### 14.4 Lost acknowledgement

Missing `persist.confirmed` is ambiguous. Recovery interface queries exact Event/receipt state before deciding whether confirmation can be appended or retry is safe.

No generic "retry Feature write" API is introduced here.

## 15. Internal unfinished-Operation query

Expose a trusted Python query:

```text
list_unfinished_operations(filters...) -> list[OperationProjection]
```

It scans/rebuilds only Operator state and has no canonical capability id.

It is intended for the later full `operator.inbox` composer.

Canonical `operator.inbox` remains backed by `UnavailableBackend`/no backend in this Feature. The design explicitly forbids installing an inbox backend that returns empty Decision/Notification arrays merely because the Operation subset is available.

## 16. Canonical API backends

`operator_store_backends.py` provides:

- `OperationStartBackend`;
- `OperationStatusBackend`;
- `OperationCancelBackend`.

Availability is true only when trusted Store runtime configuration is present and authorized.

`operation.resume`, `operator.inbox`, Decision and Notification backends remain absent/unavailable.

Backend exceptions are mapped intentionally to canonical structured errors rather than allowing all store failures to collapse to `INTERNAL_FAILURE`. This may require a bounded canonical backend exception/result adapter without changing the API schemas/version.

No MCP production tool is added for write capabilities in this Feature; MCP remains the read-only adapter delivered by the prior Feature. A later write-capable AI client adapter will consume canonical writes separately.

## 17. Error mapping

Store domain outcomes map to canonical errors:

```text
invalid semantic input        -> INVALID_REQUEST
stale Feature binding         -> STALE_REVISION
existing incompatible claim   -> ALREADY_CLAIMED
exact idempotent replay       -> ALREADY_APPLIED or successful prior result, per capability contract
old fenced generation         -> SUPERSEDED_GENERATION
cancelled new action          -> CANCELLED_OPERATION
receipt still external wait   -> EXTERNAL_WAIT
UNKNOWN / stable safety stop  -> BLOCKED
bounded CAS infra exhaustion  -> TRANSIENT_FAILURE
unexpected invariant failure  -> INTERNAL_FAILURE
```

Trusted policy failures remain `UNAUTHORIZED` / `POLICY_DENIED` from the trusted policy boundary.

Human-readable messages are diagnostic only.

## 18. Trusted binding receipts

The Store does not trust raw booleans such as `feature_revision_valid=true` supplied by workers/clients.

For candidate/Feature-sensitive commands, trusted caller code supplies typed verification receipts created by trusted runtime immediately before the Store command. Receipts bind:

- repository/Feature;
- expected revision/stage;
- target ref;
- candidate PR/head when applicable;
- verifier/runtime identity;
- verification timestamp/nonce or digest sufficient to prevent accidental cross-command reuse.

The Store validates receipt bindings against the semantic command. Worker payload cannot construct a trusted receipt through canonical schemas.

The exact runtime verifier integration may be completed by the vertical-loop Feature; this Feature defines and deterministically validates the binding contract and rejects mismatched receipts.

## 19. Schemas and canonical serialization

All store JSON files use:

- UTF-8;
- sorted deterministic object keys for digest input;
- explicit schema/version field;
- no timestamps in semantic key derivation;
- UTC RFC3339 timestamps for audit records;
- no secrets/tokens/authorization headers in durable state.

Sensitive runtime failures use existing redaction principles and never persist raw credentials.

## 20. Deterministic validation strategy

`validate_operator_store.py` uses a `MemoryStateRefBackend` with explicit barriers/fault injection rather than sleeps.

Required scenarios:

1. immutable Operation Event replay vs conflicting reuse;
2. projection delete/rebuild equality;
3. cache corruption ignored/rebuilt from journal;
4. two concurrent equivalent starts — one CAS wins, loser re-reads and converges to same Operation;
5. incompatible active Feature claim — no second owner;
6. injected CAS conflict changes cancellation/generation state before retry — stale command re-evaluates and is fenced;
7. semantic key identical across G/G+1;
8. stable external dispatch key across takeover;
9. same-generation duplicate dispatch claim convergence;
10. cancellation before launch authorization rejects launch;
11. launch authorization CAS first, cancellation second — exact authorization remains correlatable and no other dispatch can authorize;
12. receipt `NOT_LAUNCHED` safe same-key retry path;
13. receipt `LAUNCHED` adoption with no relaunch;
14. receipt `UNKNOWN` -> BLOCKED/no relaunch;
15. UNKNOWN reservation inherited by G+1 unchanged;
16. duplicate callback idempotent;
17. conflicting callback fails closed;
18. Persist cancellation-before-linearization rejection;
19. Persist linearization-before-cancel exact-write completion semantics;
20. lost Persist ack exact Event correlation;
21. stale revision receipt rejected;
22. candidate-head mismatch receipt rejected;
23. unfinished Operation internal query;
24. canonical start/status/cancel structured success/error behavior;
25. canonical `operator.inbox` still unavailable;
26. `operation.resume`/Decision/Notification writes still unavailable;
27. canonical conformance/MCP read-only regressions;
28. lifecycle/Persist/cross-repo/public runtime regressions.

## 21. Git adapter validation

In addition to the memory backend, deterministic local temporary Git repositories validate:

- first state-ref creation with nonexistence lease;
- exact-SHA lease success;
- stale lease rejection;
- changed remote ref causes command re-evaluation path;
- no writes outside `state/operator/v1/**`;
- prior commit/history remains reachable;
- no Feature branch ref is modified by Store adapter tests.

No network GitHub dependency is required for the deterministic suite.

## 22. Security boundaries

The design preserves:

- no Manifest/Gate mutation from Store;
- no arbitrary shell endpoint;
- no caller-selected state ref;
- no worker-provided state-ref credentials;
- no Feature-branch authorization expansion;
- no Worker self-asserted trusted dispatch/candidate identity;
- no speculative relaunch from missing local receipt;
- no generic post-cancel authorization;
- no false `operator.inbox` availability.

## 23. Migration and backward compatibility

The state ref is new for v0.3 and has no v0.2 migration requirement. Empty/missing ref means no Operator Operations exist yet; first trusted write initializes v1 layout atomically.

Existing Feature state remains untouched.

No change to:

- `VERSION`;
- `ai-sdlc.operator/v1` schemas;
- v0.2 Feature lifecycle semantics;
- existing MCP production tool list;
- release manifest finalization.

## 24. Explicit deferrals

This Design does not implement:

- role dispatch loop;
- Developer/Reviewer/QA Worker Result translators;
- final external Worker gateway;
- `operation.resume` policy;
- Decision/Authorization persistence/UX;
- Notification Outbox persistence/UX;
- human Acceptance automation;
- project takeover/install/upgrade;
- unattended v0.3 dogfood;
- v0.3 publication/release authority.

## 25. Design completion criteria

Implementation is conformant only if independent review can prove:

- Git state-ref CAS is exact and fail-closed;
- logical history is append-only;
- projection rebuild is deterministic;
- generation ownership and semantic-effect identity are separated correctly;
- one semantic effect retains one external dispatch key across takeover;
- launch and Persist linearization ordering matches frozen semantics;
- UNKNOWN is inherited/fail-closed;
- canonical start/status/cancel are honest;
- canonical inbox remains unavailable until complete semantics exist;
- no later-workstream authority leaks into this substrate.
