# Design v2 — F-OPERATOR-OPERATION-STORE-0001

## 1. Status and supersession

This Design supersedes `design-v1` after independent Design Review identified two MAJOR findings:

1. production writes did not fail closed on unverifiable/unprotected Operator state-ref policy;
2. reservation/claim objects were modeled as replaceable JSON ledgers rather than immutable durable artifacts.

All other approved Requirement and reviewed design semantics are retained unless explicitly changed below.

## 2. Architectural layers

The implementation remains split into:

```text
scripts/
├── operator_store_model.py       # pure schemas, keys, reducers, domain outcomes
├── operator_store.py             # pure semantic commands -> StoreMutationPlan
├── operator_store_git.py         # trusted Git state-ref CAS adapter
├── operator_store_protection.py  # trusted protection verification/provisioning boundary
├── operator_store_backends.py    # canonical operation.start/status/cancel backing
└── validate_operator_store.py    # deterministic fault/concurrency validation

spec/operator/store/
├── operation-event.schema.json
├── operation-projection.schema.json
├── semantic-reservation.schema.json
├── dispatch-claim.schema.json
├── feature-claim.schema.json
└── protection-receipt.schema.json
```

No process-local state or external database is authoritative. Production persistence remains repository-backed on a trusted control-plane state ref.

## 3. Trusted state-ref selection

The state ref is selected only by trusted installation/control configuration, conceptually:

```text
refs/heads/ai-sdlc-operator-state
```

The following inputs can never select or override it:

- Feature Manifest or Feature branch files;
- canonical API request fields;
- MCP arguments;
- Worker Results;
- task payloads;
- callback payloads.

Role workers never receive credentials that can mutate the state ref.

## 4. State-ref protection boundary

### 4.1 Protection states

Introduce a trusted interface:

```text
StateRefProtectionVerifier.verify(repository, state_ref) ->
    PROTECTED | UNPROTECTED | UNKNOWN
```

`PROTECTED` means trusted control code has positively established that repository policy/rules prevent ordinary uncontrolled mutation of the Operator state ref and that the configured Operator writer is an allowed bounded writer.

`UNPROTECTED` means the ref is known to lack the required protection.

`UNKNOWN` means protection could not be proved because of API/platform/configuration ambiguity or transient inability to inspect policy.

Only `PROTECTED` permits production semantic Store writes.

### 4.2 No self-attestation

Protection status is obtained by trusted installation/control runtime. It is not accepted as a request boolean, environment value supplied by a Feature branch, Worker field, or unverified configuration file on the target branch.

The Git Store writer requires a `ProtectionReceipt` produced by the trusted verifier and bound to:

- repository identity;
- exact state ref;
- trusted installation/control identity;
- verified protection configuration digest/ruleset identity where available;
- verification time;
- status = `PROTECTED`.

A receipt for another repository/ref is rejected.

### 4.3 Fail closed

`GitStateRefBackend.commit(plan, protection_receipt)` refuses semantic writes when:

- receipt is absent;
- status is `UNPROTECTED`;
- status is `UNKNOWN`;
- repository/ref binding differs;
- receipt is structurally invalid or outside bounded trusted validity policy.

The result maps to a policy/configuration failure and never silently falls back to an unprotected push.

### 4.4 Provisioning responsibility

State-ref protection provisioning belongs to trusted installation/control authority, not Worker or Feature execution.

The reference flow is:

```text
trusted install/control
→ resolve configured state ref
→ provision/bootstrap ref if required
→ apply repository protection/rules
→ verify protection as PROTECTED
→ enable Operator Store semantic writes
```

Operator runtime does not attempt to grant itself repository-admin authority.

### 4.5 Safe first-ref initialization

Platforms differ on whether a ruleset can target a branch before the ref exists. The design supports two safe modes selected by trusted installation code:

**Mode A — pre-targetable protection**

1. install ruleset/policy matching the future state ref;
2. verify that policy will cover the target ref;
3. create the initial empty Store commit/ref through installation authority;
4. verify actual ref protection = PROTECTED;
5. enable semantic Store writes.

**Mode B — ref must exist before branch protection**

1. create an initialization-only ref/commit through privileged trusted installation authority;
2. the initialization commit contains only version marker/empty `state/operator/v1/` bootstrap metadata and no Operation, reservation, claim, launch, cancellation, or Persist semantic state;
3. immediately apply protection/rules;
4. verify PROTECTED;
5. only then mark the Store enabled for semantic writes.

There is no semantic-write window between bootstrap creation and verified protection. Runtime semantic writers remain disabled until step 5.

If protection cannot be applied/verified, bootstrap is incomplete and canonical Store-backed writes remain unavailable.

### 4.6 Deterministic protection tests

Validation fixtures must prove:

- PROTECTED permits bounded CAS writes;
- UNPROTECTED rejects writes before tree/commit mutation;
- UNKNOWN rejects writes;
- mismatched receipt rejects writes;
- first-ref bootstrap cannot append semantic Operation state before PROTECTED;
- Feature/client/Worker inputs cannot forge protection receipts.

## 5. Git CAS semantics

The Store uses exact state-ref compare-and-set:

1. read exact ref SHA/tree;
2. run semantic planner against that snapshot;
3. build changed blobs/tree/commit with expected commit as parent;
4. require a valid PROTECTED receipt;
5. update/push the state ref only with an exact expected-SHA/nonexistence lease;
6. on conflict, re-read the Store and rerun the semantic command from original trusted inputs.

A local implementation may use exact `--force-with-lease=<ref>:<expected-sha>` plumbing solely as a compare-and-set primitive. It may not intentionally create a non-fast-forward semantic history rewrite. The new commit must descend from the exact expected state-ref commit.

Persistent CAS conflict or transport failure is bounded; retries are finite and semantic state is re-evaluated on each retry.

## 6. Immutable durable layout

The v1 logical layout remains:

```text
state/operator/v1/
├── operations/<operation-id>/events/<sequence>-<event-id>.json
├── reservations/external/<semantic-effect-key>.json
├── claims/dispatch/<dispatch-claim-id>.json
├── claims/feature/<target-repo-hash>/<feature-id>/<claim-id>.json
└── projections/<operation-id>.json
```

Decision/Notification namespaces remain reserved/unimplemented by this Feature.

### 6.1 Operation Events

Each Operation Event is created once and never modified:

```text
operations/<operation-id>/events/<sequence>-<event-id>.json
```

Schema version is `ai-sdlc.operation-event/v1`.

Events carry immutable facts and transitions including, as applicable:

- `operation.started`;
- `operation.superseded`;
- `operation.generation.started`;
- `dispatch.claimed`;
- `dispatch.launch.authorized`;
- `dispatch.launch.lookup-recorded`;
- `worker.callback.recorded`;
- `operation.blocked`;
- `operation.cancelled`;
- `persist.requested`;
- `persist.linearized`;
- `persist.confirmed`;
- terminal/release observations where in scope.

Sequence is contiguous per Operation. Conflicting event-id/path reuse is rejected. Equivalent exact replay is idempotent.

### 6.2 Semantic reservation is immutable

Reservation path:

```text
reservations/external/<semantic-effect-key>.json
```

It is **create-once immutable** and permanently binds:

- semantic-effect key;
- normalized key inputs;
- one stable `external_dispatch_key`;
- original Operation id/generation for audit;
- target repository / Feature / expected revision / stage / task / role / candidate head when applicable;
- creation timestamp and trusted context digest.

No later launch state, callback state, UNKNOWN state, takeover state, or retirement state is written back into this file.

All evolving observations are immutable Operation Events referring to this reservation.

### 6.3 Dispatch claim is immutable

Path:

```text
claims/dispatch/<dispatch-claim-id>.json
```

A claim is create-once and binds:

- claim id;
- operation id/generation;
- semantic-effect key;
- stable external dispatch key;
- trusted claim context/time.

It is never modified to mark launched/released/terminal. Those facts are journal events.

Generation takeover creates a new generation-specific immutable claim only when trusted takeover rules permit ownership inheritance; it points to the same immutable reservation/external key.

### 6.4 Feature claim is immutable

Feature claims no longer use a replaceable ledger. Each claim gets an immutable artifact:

```text
claims/feature/<target-repo-hash>/<feature-id>/<claim-id>.json
```

It binds Feature identity, Operation identity, generation/claim epoch, expected Feature revision and trusted idempotency/equivalence information at claim time.

Claim terminal/superseded/released state is derived from Operation journal events and projection, never written back into the claim artifact.

The active Feature owner is derived deterministically from immutable claims plus journal events. New claims are permitted only when the reducer proves there is no incompatible nonterminal active owner.

### 6.5 Projection is the only replaceable Store cache

Projection path:

```text
projections/<operation-id>.json
```

It may be replaced because it is explicitly a cache. It contains `last_sequence` and journal/input digest sufficient to validate or rebuild it.

If projection disagrees with immutable history, immutable history wins and the projection is rebuilt.

### 6.6 Mutation planner path rules

`StoreMutationPlan` categorizes every mutation as either:

- `create_immutable` for Operation Event/reservation/claim artifacts; or
- `replace_projection` for projection cache only.

The Git adapter rejects:

- update/delete of an existing immutable path;
- replace of reservation/claim/event bytes;
- delete/truncate/reorder historical immutable objects;
- write outside `state/operator/v1/**`.

This makes immutability enforceable at the trusted writer boundary rather than a convention in domain code.

## 7. Deterministic projection and current state

`rebuild_projection(immutable_events, reservations, claims)` is pure and deterministic.

Current status, current generation, active claim ownership, launch authorization, receipt observations, UNKNOWN blocking, cancellation, and Persist state are all derived from immutable objects/events.

Top-level Store states in scope:

- RUNNING;
- WAITING_EXTERNAL;
- BLOCKED;
- CANCELLED;
- DONE.

`NEEDS_USER` remains a later Decision/Authorization concern.

The reducer rejects impossible/forked history, including:

- event sequence gaps/duplicates;
- events for another Operation;
- old generation acting after supersession;
- launch authorization after prior cancellation;
- new external identity for inherited unresolved semantic effect;
- Persist linearization after prior cancellation/supersession;
- conflicting immutable claims for an already owned Feature without valid takeover/release history.

## 8. Operation start and active Feature ownership

`operation.start` remains one atomic semantic command.

The planner:

1. resolves idempotency/equivalence;
2. reconstructs active Feature ownership from immutable feature claims + Operation journals;
3. validates trusted expected Feature revision binding;
4. returns existing compatible active Operation when equivalent;
5. rejects incompatible active ownership;
6. otherwise creates one immutable feature claim + `operation.started` event + projection in one CAS commit.

Equivalent concurrent starts converge after CAS loser re-read/re-evaluation.

## 9. Generation takeover

Takeover is trusted-internal and not canonical `operation.resume` in this Feature.

One CAS commit creates immutable takeover events and, where needed, a new immutable generation-specific dispatch/feature claim referring to existing immutable reservations.

It never rewrites reservation, old claim, old events, or external dispatch key.

Generation G becomes fenced through journal semantics; G+1 inherits unresolved reservation identity.

## 10. Semantic effect key and external dispatch key

Semantic-effect key is canonical SHA-256 over normalized:

```text
target_repository
feature_id
expected_revision
current_stage
task_identity
role
candidate_head_sha_or_null
```

Operation id and generation are excluded.

The immutable reservation is the permanent durable source of the associated `external_dispatch_key`.

Any attempt to create the same semantic-effect key with different canonical inputs or another external key is rejected.

## 11. Launch authorization

`plan_authorize_launch()` reconstructs current state from immutable data and validates trusted binding receipts for:

- current generation;
- non-cancelled/non-superseded state;
- immutable dispatch claim ownership;
- immutable reservation identity;
- Feature revision/stage;
- candidate head when applicable;
- trusted policy/credential preconditions.

It creates a single immutable `dispatch.launch.authorized` event. That event is the launch linearization point.

Cancellation/supersession committed first makes authorization fail. Authorization committed first remains valid only for the exact reservation/dispatch identity already bound by that event.

## 12. Receipt and callback correlation

Trusted receipt interface remains:

```text
NOT_LAUNCHED
LAUNCHED(receipt)
UNKNOWN
```

Each observation becomes an immutable Operation Event; reservation/claim files are untouched.

Rules:

- NOT_LAUNCHED: same reservation/key may later be considered for exact retry after current fences re-pass;
- LAUNCHED: adopt existing receipt; no relaunch;
- UNKNOWN: append blocking observation and project BLOCKED; no speculative relaunch;
- duplicate same observation/callback: idempotent exact event/correlation outcome;
- conflicting identity/binding: fail closed.

UNKNOWN inheritance is computed from immutable reservation + journal history. Takeover does not create a new semantic reservation or external key.

## 13. Cancellation

`operation.cancel` appends one immutable `operation.cancelled` event and replaces projection cache in one CAS commit.

After durable cancellation:

- no new unlinearized decisions/launches/Persist authorization;
- exact pre-authorized launch may complete/correlate;
- exact pre-linearized Persist may complete/correlate;
- later Worker result alone does not gain lifecycle Persist authority.

Repeated cancel converges to the current cancelled result.

## 14. Persist linearization

Persist lifecycle is represented entirely by immutable Operation Events:

- `persist.requested`;
- `persist.linearized`;
- `persist.confirmed`.

Each binds exact Operation/generation/Event/Feature revision/ref/candidate head where applicable.

The Store does not modify Feature Manifest/Event files. Existing/later trusted Persist consumes the exact authorization receipt.

Cancellation/supersession before `persist.linearized` wins. `persist.linearized` first permits only the exact already-bound write to finish after cancellation.

Lost acknowledgement recovery queries exact Feature Event/Persist receipt identity before recording `persist.confirmed` or deciding a retry is safe.

## 15. Trusted verification receipts

Protection receipt and Feature/candidate verification receipts are trusted runtime objects unavailable in canonical/Worker schemas.

Feature/candidate receipt binds repository, Feature, expected revision/stage/ref, candidate PR/head when applicable, verifier identity and bounded freshness/digest.

Store commands reject mismatched receipts. Workers/clients cannot self-assert trusted bindings.

## 16. Canonical API scope

Backends added only for:

- `operation.start`;
- `operation.status`;
- `operation.cancel`.

They use durable Store semantics and canonical structured errors.

`operator.inbox`, `operation.resume`, Decision and Notification backends remain unavailable.

An internal trusted unfinished-Operation query exists for later complete inbox composition.

The existing MCP production adapter remains read-only; this Feature does not add MCP semantic write tools.

## 17. Structured error mapping

Domain outcomes map intentionally to canonical errors:

- malformed/binding-invalid semantic input → INVALID_REQUEST;
- stale Feature binding → STALE_REVISION;
- incompatible active/dispatch claim → ALREADY_CLAIMED;
- exact replay → ALREADY_APPLIED or prior successful result according to capability idempotency contract;
- old generation → SUPERSEDED_GENERATION;
- new action after cancel → CANCELLED_OPERATION;
- external non-user wait → EXTERNAL_WAIT;
- UNKNOWN/safety stop → BLOCKED;
- bounded CAS/transport exhaustion → TRANSIENT_FAILURE;
- invariant violation → INTERNAL_FAILURE.

Protection failure is unavailable/policy-denied according to trusted runtime context and must not be collapsed into a successful Store availability claim.

## 18. Deterministic validation

Memory/fake state-ref tests use explicit barriers/fault injection, not sleeps.

Mandatory scenarios include:

1. event immutable exact replay vs conflicting reuse;
2. reservation immutable create-once and conflicting recreate rejection;
3. dispatch claim immutable create-once;
4. feature claim immutable create-once/history preservation;
5. planner rejects update/delete of any immutable object;
6. projection cache delete/corrupt/rebuild equivalence;
7. CAS conflict causes full semantic re-read/re-evaluation;
8. equivalent concurrent starts converge;
9. incompatible Feature ownership is rejected;
10. semantic key/external key stable across takeover;
11. takeover creates new immutable claim/events without modifying old objects;
12. cancellation before launch authorization rejects launch;
13. launch authorization before cancel remains exact-only;
14. NOT_LAUNCHED/LAUNCHED/UNKNOWN recovery;
15. UNKNOWN survives takeover unchanged;
16. duplicate callback idempotency and conflicting callback rejection;
17. Persist ordering around cancellation;
18. lost Persist ack exact correlation;
19. stale Feature revision/candidate receipt rejection;
20. unfinished Operation internal query;
21. canonical start/status/cancel behavior;
22. inbox/resume/Decision/Notification remain unavailable;
23. PROTECTED protection receipt permits write;
24. UNPROTECTED/UNKNOWN/mismatched protection receipt rejects before semantic commit;
25. first-ref bootstrap cannot contain semantic state before protection is verified;
26. canonical/MCP/lifecycle/Persist/cross-repo/public-runtime regression suites.

Local temporary Git tests additionally prove exact ref lease/CAS behavior and preservation of prior Git history.

## 19. Backward compatibility and deferrals

No changes to:

- `VERSION`;
- canonical API version/schemas;
- v0.2 Feature lifecycle/Persist authority;
- MCP production tool list;
- final release manifest.

Still deferred:

- automated Developer→Reviewer→Remediation→Re-review→QA loop;
- Worker Result translators;
- final Worker gateway;
- broad recovery / `operation.resume`;
- Decision/Authorization;
- Notification Outbox;
- project takeover/install/upgrade product flow beyond the trusted state-ref provisioning boundary needed by this Store;
- unattended v0.3 dogfood;
- final release authority.

## 20. Design completion criteria

The implementation may proceed only if independent Design Re-review confirms:

- actual state-ref protection is positively verified and fail-closed before semantic writes;
- first-ref initialization cannot expose an unprotected semantic-write window;
- reservation and claim artifacts are immutable create-once objects;
- evolving state lives in immutable journal events;
- projection is the only replaceable cache;
- exact Git CAS and semantic re-evaluation remain intact;
- launch/Persist linearization, UNKNOWN inheritance, generation fencing and canonical API scope remain aligned with approved Requirement v2.
