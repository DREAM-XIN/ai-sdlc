# Design — F-OPERATOR-EFFECT-LINEAGE-0001

## 1. Objective and authority

Implement the approved Requirement for durable cross-revision/candidate Effect Lineage without changing the frozen v0.3 protocol.

Feature Manifest + trusted Feature Event/Persist remain the only Feature lifecycle authority. Effect Lineage is trusted orchestration safety state stored beside the existing Operator Store on the protected control-plane state ref.

This Design consumes the frozen `docs/v0.3-release-spec.md` and does not introduce any new post-`dispatch.launch.authorized` revocation primitive.

Requirement Review MINOR-1 is therefore resolved architecturally as follows:

- this Feature remains on frozen same-existing-key recovery / `BLOCKED` semantics after durable launch authorization;
- Design Review for this Feature is not authority to invent a new launch-revocation or gateway-fencing protocol;
- if implementation discovers that such a primitive is necessary, implementation stops fail-closed and requires a separate protocol amendment/review/freeze before this Feature may consume it.

## 2. Existing substrate

The implementation extends the accepted Operation Store and vertical-loop architecture rather than adding a second orchestration system.

Existing trusted Store properties remain unchanged:

- one protected trusted Operator state ref;
- Git exact-ref CAS with semantic re-plan on conflict;
- immutable Operation events;
- immutable exact semantic reservations;
- immutable generation-specific dispatch claims;
- replaceable/rebuildable projections only;
- `dispatch.launch.authorized` as launch linearization;
- receipt lookup states `NOT_LAUNCHED`, `LAUNCHED`, `UNKNOWN`;
- candidate/revision exact binding before launch and Persist;
- Feature Event/Persist as lifecycle authority.

Current exact reservation identity remains:

```text
target_repository
feature_id
expected_revision
current_stage
task_identity
role
candidate_head_sha_or_null
```

Operation id/generation remain excluded from `semantic_effect_key`.

## 3. Architectural components

Add focused modules rather than overloading the existing reservation reducer:

```text
scripts/
├── operator_effect_lineage_model.py      # lineage schemas, ids, reducers, invariants
├── operator_effect_lineage.py            # pure lineage semantic commands
├── operator_effect_lineage_integration.py# atomic lineage-gated reservation composition
├── operator_effect_resolution.py         # trusted bounded resolution planner
├── operator_effect_migration.py          # legacy attachment/fail-closed migration
├── operator_store.py                     # existing exact reservation/launch primitives, additive hooks only
├── operator_vertical.py                  # vertical planner calls lineage-gated reservation path
└── validate_operator_effect_lineage.py   # deterministic adversarial validation

spec/operator/effect-lineage/
├── lineage-anchor.schema.json
├── lineage-member.schema.json
├── lineage-proposal.schema.json
├── lineage-event.schema.json
├── lineage-projection.schema.json
└── effect-resolution-record.schema.json
```

Exact filenames may vary during implementation only if semantics remain equivalent and Design Review evidence maps them explicitly.

## 4. Protected durable layout

All authoritative lineage facts live under the same trusted protected Operator state ref as existing Store state:

```text
state/operator/v1/
├── reservations/external/<semantic-effect-key>.json
├── ... existing Store data ...
└── effect-lineages/
    ├── anchors/<effect-lineage-id>.json
    ├── members/<effect-lineage-id>/<semantic-effect-key>.json
    ├── proposals/<effect-lineage-id>/<proposal-id>.json
    ├── events/<effect-lineage-id>/<sequence>-<event-id>.json
    ├── resolutions/<effect-lineage-id>/<resolution-id>.json
    └── projections/<effect-lineage-id>.json
```

Rules:

- anchor/member/proposal/event/resolution records are create-once immutable;
- only lineage projection is replaceable cache;
- existing exact reservation files remain immutable and are not rewritten to inject lineage metadata;
- lineage member records bind an existing/new exact reservation to its lineage without mutating the reservation;
- the trusted Git mutation adapter rejects update/delete of lineage immutable paths exactly as it rejects reservation/event/claim rewrites.

## 5. Trusted lineage identity

### 5.1 Canonical causal material

`effect_lineage_id` is a canonical SHA-256 over a versioned trusted material document:

```text
schema = ai-sdlc.effect-lineage-key/v1
target_repository
feature_id
operation_profile
effect_kind
role
causal_work_id
external_effect_scope
```

The normalized serialization is canonical JSON using the same stable digest discipline as current Store identities.

### 5.2 `causal_work_id`

`causal_work_id` is derived only from trusted lifecycle/profile semantics.

For the currently supported vertical profile, the mapping is deterministic by logical work slot rather than Feature revision or candidate SHA. Conceptually:

```text
implementation:<feature-id>:<approved implementation work slot>
code-review:<feature-id>:<review slot / remediation lineage>
verification:<feature-id>:<verification slot / remediation lineage>
```

The exact mapping is centralized in a trusted `CausalWorkResolver` owned by the approved operation profile. It may consume durable Feature task/remediation identity, stage/role, and trusted profile semantics. It must not consume Worker-provided random ids, Operation generation, process/session ids, or candidate SHA as a discriminator that creates a fresh lineage.

Where current Feature state does not provide enough trusted facts to prove whether work is same or distinct, the resolver returns `AMBIGUOUS_LINEAGE` and planning stops `BLOCKED`.

### 5.3 External effect scope

`external_effect_scope` is a trusted profile-defined namespace for genuinely independent real-world effects. It is not a caller-provided escape hatch.

For current role dispatches it defaults to the reviewed profile's single logical dispatch scope for that causal work slot. A future profile may define multiple scopes only through reviewed trusted profile semantics.

## 6. Immutable lineage anchor

Anchor path:

```text
effect-lineages/anchors/<effect-lineage-id>.json
```

Fields:

```text
schema_version
effect_lineage_id
target_repository
feature_id
operation_profile
effect_kind
role
causal_work_id
external_effect_scope
created_at
trusted_context_digest
```

The anchor is create-once. Re-deriving the same id with non-equivalent canonical material is an invariant failure.

No mutable status such as blocked/retired/current leaf is stored in the anchor.

## 7. Exact lineage members

Member path:

```text
effect-lineages/members/<effect-lineage-id>/<semantic-effect-key>.json
```

A member binds one exact semantic reservation to one lineage:

```text
schema_version
effect_lineage_id
semantic_effect_key
external_dispatch_key
operation_id_at_activation
operation_generation_at_activation
expected_revision
stage
task_identity
role
candidate_head_sha
predecessor_semantic_effect_key|null
activated_from_proposal_id|null
activated_at
trusted_context_digest
```

The member is immutable.

Invariants:

- one exact semantic key may belong to at most one lineage;
- external key must equal the immutable exact reservation's external key;
- the active member chain is ordered by predecessor relation;
- there may be at most one launch-eligible descendant of any predecessor;
- changing revision/candidate/generation never rewrites a member.

## 8. Immutable successor proposals

A proposal is created when current exact work differs from the current lineage member but predecessor safety has not yet been cleared.

Path:

```text
effect-lineages/proposals/<effect-lineage-id>/<proposal-id>.json
```

Canonical proposal identity binds:

```text
effect_lineage_id
predecessor_semantic_effect_key
proposed_exact_semantic_material
current_feature_revision
current_stage
current_target_ref
current_candidate_head_sha
operation_id
operation_generation
trusted_profile_digest
```

A proposal deliberately contains **no** `external_dispatch_key`, dispatch claim, reservation receipt, or launch authorization.

Equivalent planning retries reuse the exact immutable proposal. Conflicting use of the same proposal identity fails closed.

A changed candidate/revision therefore becomes a new exact proposal while remaining in the same lineage when causal work is the same.

## 9. Lineage events and projection

Append-only lineage events record evolving safety facts without rewriting members/proposals:

```text
lineage.root-activated
lineage.successor-proposed
lineage.predecessor-blocked
lineage.predecessor-correlated
lineage.predecessor-retired
lineage.successor-activated
lineage.member-superseded
lineage.member-adopted
lineage.legacy-attached
lineage.legacy-unresolved
lineage.resolution-applied
```

Event payloads are bounded schemas; unknown event types fail closed.

Projection derives:

```text
effect_lineage_id
current_leaf_semantic_effect_key|null
current_leaf_external_dispatch_key|null
current_proposal_id|null
blocks_on_semantic_effect_key|null
predecessor_state
relations[]
last_sequence
history_digest
```

Projection deletion/corruption is recoverable from immutable anchor/member/proposal/event/resolution state. Immutable history wins over projection.

## 10. Pre-reservation lineage gate

### 10.1 New trusted API

Vertical planning must not call existing `plan_semantic_reservation()` directly for launch-eligible role work.

It calls one composition boundary:

```python
plan_lineage_gated_reservation(
    snapshot,
    *,
    operation_id,
    generation,
    trusted_feature_snapshot,
    operation_profile,
    effect_kind,
    role,
    causal_work_id,
    external_effect_scope,
    exact_semantic_material,
    occurred_at,
    trusted_context_digest,
) -> StoreMutationPlan
```

### 10.2 Single-snapshot atomicity

This command performs, against one Store snapshot:

1. derive/validate trusted lineage material;
2. rebuild lineage projection;
3. determine whether exact work is existing-member recovery, a root, a blocked successor, or provably distinct work;
4. if blocked, create/reuse successor proposal and blocking event only;
5. if safely eligible, create exact reservation + lineage member + activation events in **one StoreMutationPlan**;
6. the Git adapter commits the complete plan under one state-ref CAS.

There is no successful intermediate state in which lineage clearance is committed but a concurrent writer can create an incompatible reservation before member activation.

On CAS conflict, the whole command re-runs from original trusted inputs against the new snapshot.

### 10.3 Direct reservation fencing

Existing lower-level `plan_semantic_reservation()` remains available for backward-compatible legacy/test/internal paths, but production vertical composition after this Feature is accepted must not use it for lineage-required effects.

A trusted capability/configuration flag such as `effect_lineage_required=true` is installation/default-branch controlled. When active for the supported profile, the production vertical dispatcher refuses launch planning unless the reservation has a valid lineage member.

This supplies mixed-writer fencing: a non-lineage-aware production writer cannot silently continue to create launch-eligible work after rollout.

## 11. Existing exact-member recovery

If exact proposed semantic key already matches the current lineage member:

- no new proposal/member/reservation is created;
- existing reservation and external key are returned;
- existing launch/callback/recovery fences continue unchanged;
- generation takeover may create a new generation-specific dispatch claim only under existing Store rules and always points to the same reservation/key.

Lineage therefore does not interfere with same-key idempotent recovery.

## 12. Predecessor effective state

The lineage reducer derives predecessor state from **both** immutable lineage facts and existing Operation Store history.

At minimum:

```text
NEVER_AUTHORIZED
AUTHORIZED_UNCONFIRMED
AUTHORIZED_NOT_LAUNCHED_OBSERVED
LAUNCHED_CORRELATED
UNKNOWN
RETIRED_NO_DUPLICATE_PROVEN
LEGACY_UNRESOLVED
```

`NOT_LAUNCHED` is never interpreted without durable launch-authorization history.

A current lookup observation cannot erase a prior `dispatch.launch.authorized` event.

## 13. Bounded effect resolution

### 13.1 Authority object

Trusted composition supplies:

```text
EffectResolutionAuthority
  authority_id
  allowed_choices
  resolver_identity_policy
  trusted_policy_ref
  trusted_policy_digest
  evidence_verifier
  validity_window / freshness policy
```

This object is constructed only from protected/default-branch, installation, or trusted-control configuration. It is absent from canonical/Worker schemas.

### 13.2 Resolution request

Trusted resolution planner accepts only the four frozen choices:

```text
CORRELATE_EXISTING_RECEIPT
PROVE_NOT_LAUNCHED
RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN
REMAIN_BLOCKED
```

The request binds exact current state:

```text
resolution_id
target_repository
feature_id
effect_lineage_id
predecessor_semantic_effect_key
predecessor_external_dispatch_key
current_operation_id
current_operation_generation
current_feature_revision
current_target_ref
current_candidate_head_sha
successor_proposal_id?
successor_proposed_semantic_effect_key?
choice
trusted_policy_ref/digest
resolver_identity
evidence_refs/digests
```

### 13.3 Evidence verifier outcomes

`TrustedEffectEvidenceVerifier` returns typed evidence facts rather than arbitrary booleans:

```text
EXTERNAL_LAUNCH_RECEIPT(receipt_id, key)
EXTERNAL_NOT_LAUNCHED(key, observed_at, source_digest)
EXTERNAL_KEY_INVALIDATED(key, fence_receipt)
NON_OVERLAPPING_SCOPE(proof_digest)
INSUFFICIENT
```

Worker/model assertions are not evidence.

### 13.4 Application under CAS

Resolution application re-reads Store lineage, Operation history, Feature/candidate truth, policy digest and evidence binding before writing.

It appends immutable resolution record + lineage event in one CAS plan. If successor becomes eligible, **activation and creation of the new exact reservation/member occur in the same CAS plan** as predecessor retirement/adoption where required.

A stale resolution cannot be replayed against a changed proposal, candidate, generation, policy, predecessor leaf or Feature revision.

Resolution record alone never dispatches; dispatch claim and launch authorization remain later existing Store steps.

## 14. Resolution semantics

### 14.1 `CORRELATE_EXISTING_RECEIPT`

Requires receipt evidence for the exact predecessor external key.

Effects:

- append resolution and correlation/adoption facts;
- predecessor remains the executed external effect;
- no relaunch of predecessor;
- successor external activation is allowed only if current trusted lifecycle semantics still require genuinely subsequent non-duplicate work; otherwise the current effect is adopted and the successor proposal becomes obsolete/superseded without a launch.

Current Worker result applicability still passes existing exact candidate/revision/Persist fences.

### 14.2 `PROVE_NOT_LAUNCHED`

Requires:

1. trusted `EXTERNAL_NOT_LAUNCHED` evidence for exact predecessor key; and
2. durable history proving the predecessor never crossed `dispatch.launch.authorized` or otherwise has no still-executable launch authorization under the frozen protocol.

For current v0.3, condition 2 means **no durable launch authorization exists** for the predecessor. There is no generic revocation state that can make an existing authorization disappear.

If authorization exists, this choice is rejected with `AUTHORIZED_EFFECT_STILL_EXECUTABLE` and lineage stays blocked.

### 14.3 `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`

Permitted only with typed stronger evidence such as:

- authoritative `EXTERNAL_KEY_INVALIDATED` from an external gateway/control plane guaranteed to fence every possible launcher; or
- `NON_OVERLAPPING_SCOPE` proving the successor cannot duplicate/overlap the predecessor.

However, because the frozen v0.3 implementation currently defines no generic post-authorization invalidation protocol, this Feature does **not** implement a new generic gateway fence merely to satisfy this branch.

If the runtime has no already-reviewed trusted evidence source that meets the frozen rule, this outcome returns `INSUFFICIENT` / `BLOCKED`.

Any future new invalidation primitive requires a separate protocol amendment as stated in §1.

### 14.4 `REMAIN_BLOCKED`

Always valid when no safe proof exists. It may append/audit a resolution observation but creates no successor reservation or external identity.

## 15. Candidate A → B handling

Vertical task selection continues to create exact candidate-bound task identity for current candidate B.

When A and B map to the same trusted causal work lineage:

1. stale A Reviewer/QA callback/result is rejected by existing exact binding;
2. B exact semantic material is computed freshly;
3. lineage gate sees A as predecessor;
4. B becomes/reuses immutable successor proposal;
5. B receives no reservation/external key while A unresolved;
6. after safe predecessor resolution, B proposal may atomically activate into its own exact reservation/member;
7. normal claim/launch/Persist fences run afterward.

Thus candidate freshness and external effect safety remain separate mechanisms.

## 16. Cancellation, supersession and generation takeover

Cancellation/supersession append existing Operation events only; they do not retire lineage members.

A pre-authorized predecessor remains executable according to existing launch linearization even after cancellation/supersession.

Generation takeover:

- rebuilds current lineage from protected durable state;
- may inherit same exact member/reservation/key recovery responsibility;
- cannot create a successor reservation merely because generation changed;
- blocked proposal remains blocked until trusted resolution.

Operation id replacement under a reviewed takeover/recovery profile may point to the same lineage only when `CausalWorkResolver` proves the same durable causal slot; otherwise ambiguity blocks.

## 17. Stale-runner race ordering

The required race is enforced structurally:

```text
T1: K0 reservation/member exists
T2: dispatch.launch.authorized(K0,E0) commits
T3: stale runner pauses
T4: lookup(E0)=NOT_LAUNCHED is recorded
T5: current truth produces K1 exact material in same lineage
T6: lineage-gated planner creates K1 proposal only
T7: PROVE_NOT_LAUNCHED examines immutable Operation history and finds T2
T8: resolution rejected; no K1 reservation/member/external key exists
T9: stale runner may resume only exact E0
```

Because K1 external reservation is not created before lineage resolution and because T2 is immutable, the `NOT_LAUNCHED` observation cannot enable both E0 and E1.

## 18. Legacy migration

### 18.1 Read compatibility

Existing exact reservations remain readable and unchanged.

When lineage-required planning encounters a legacy reservation without member metadata, `LegacyLineageResolver` tries to reconstruct causal material only from trusted durable Store/Feature/profile facts.

### 18.2 Safe attachment

If exactly one lineage identity is proven and no conflict exists, one CAS plan creates:

- lineage anchor if absent;
- member binding to the existing reservation/key;
- `lineage.legacy-attached` event.

The reservation itself is not modified.

### 18.3 Fail closed

If the reservation is unresolved (`UNKNOWN`, authorized-unconfirmed, authorized+NOT_LAUNCHED observation, or otherwise potentially executable) and lineage cannot be proven uniquely:

- append/derive `LEGACY_UNRESOLVED_LINEAGE` where auditable;
- overlapping new launch remains blocked;
- no fresh lineage/key is invented.

For conclusively terminal/non-overlapping legacy data, Design permits read-only compatibility and bounded migration according to trusted evidence.

## 19. Mixed-writer rollout fence

Lineage enforcement is activated through trusted installation/runtime configuration bound to the protected state ref and vertical profile.

Rollout order:

1. deploy lineage-aware readers/reducers/migration code;
2. verify state-ref protection and schema support;
3. quiesce/fence old production vertical writers;
4. enable `effect_lineage_required` for the supported profile;
5. only then allow lineage-aware production reservation creation;
6. verify no writer without lineage membership can authorize launch.

The production launch path checks that the exact reservation is lineage-bound when enforcement is active. This is defense in depth against an accidentally retained direct reservation caller.

There is no compatibility mode in which old and new production writers both create launch-eligible reservations for the same profile.

## 20. Vertical-loop integration

`TrustedVerticalExecutor` / vertical planner receives a lineage-aware dispatch dependency.

Before dispatch claim creation it calls `plan_lineage_gated_reservation()` instead of the raw reservation planner.

Outcome mapping:

```text
EXISTING_MEMBER_RECOVERY -> existing claim/recovery flow
ROOT_ACTIVATED           -> normal claim/authorize/dispatch flow
SUCCESSOR_ACTIVATED      -> normal claim/authorize/dispatch flow
SUCCESSOR_BLOCKED        -> Operation BLOCKED with lineage reason
AMBIGUOUS_LINEAGE        -> Operation BLOCKED
LEGACY_UNRESOLVED_LINEAGE-> Operation BLOCKED
```

Worker callback translators remain unchanged in authority. Lineage cannot PASS Feature gates or synthesize lifecycle Events.

## 21. Error model

Add bounded domain codes:

```text
EFFECT_LINEAGE_BLOCKED
AMBIGUOUS_LINEAGE
LEGACY_UNRESOLVED_LINEAGE
STALE_EFFECT_RESOLUTION
RESOLUTION_NOT_AUTHORIZED
INSUFFICIENT_EFFECT_EVIDENCE
AUTHORIZED_EFFECT_STILL_EXECUTABLE
LINEAGE_INVARIANT_VIOLATION
MIXED_WRITER_FENCED
```

Canonical/public mapping remains conservative: safety/state stops map to `BLOCKED`; stale Feature binding remains `STALE_REVISION`; policy failures remain unavailable/denied according to existing canonical rules.

Internal detail remains auditable without exposing trusted policy/evidence internals to untrusted clients.

## 22. Machine-readable release-contract validator

Add a deterministic validator, integrated into authoritative `scripts/validate.py`, that asserts the frozen amended contract remains coherent.

It rejects reintroduction of:

```text
worker_result_contract.head_change_requires_new_semantic_dispatch: true
new-head-requires-new-semantic-dispatch
```

and requires semantics equivalent to:

```text
head_change_invalidates_stale_candidate_evidence: true
head_change_requires_fresh_exact_candidate_bound_work: true
head_change_alone_authorizes_new_external_dispatch: false
fresh_candidate_external_dispatch_requires_effect_lineage_clearance: true
```

This validator does not edit the frozen Release Spec; it detects drift.

## 23. Schema/version compatibility

Existing `ai-sdlc.operator/v1` canonical API version does not change.

Existing exact reservation schema remains readable and immutable. New lineage schemas use additive versioned identities, conceptually:

```text
ai-sdlc.effect-lineage/v1
ai-sdlc.effect-lineage-member/v1
ai-sdlc.effect-lineage-proposal/v1
ai-sdlc.effect-lineage-event/v1
ai-sdlc.effect-resolution/v1
```

No Worker/canonical request gains authority-bearing lineage or resolution fields.

MCP remains read-only.

## 24. Deterministic validation strategy

Use pure in-memory Store snapshots and exact CAS conflict fixtures; no sleeps.

Mandatory tests:

1. root exact work creates anchor + reservation + member atomically;
2. exact replay returns existing member/key;
3. revision change, same causal work → same lineage, successor proposal only while predecessor unresolved;
4. candidate A→B → A evidence rejected by existing exact fence; B proposal gets no reservation/key while A unresolved;
5. never-authorized predecessor + trusted NOT_LAUNCHED proof → retirement and successor activation/reservation occur atomically;
6. durable launch authorization + NOT_LAUNCHED observation → `PROVE_NOT_LAUNCHED` rejected and successor remains proposal-only;
7. LAUNCHED receipt correlation/adoption → no predecessor relaunch;
8. UNKNOWN → remains blocked across revision/candidate/generation takeover;
9. concurrent planners from same predecessor → one proposal/activation lineage chain, no sibling active descendants;
10. CAS loser fully re-reads/re-plans and cannot replay stale activation;
11. stale resolution wrong lineage/key/operation/generation/revision/ref/candidate/proposal/policy/evidence → rejected;
12. cancellation/supersession does not retire predecessor;
13. stale-runner race from §17 proves no K1 reservation/key while K0 authorization executable;
14. legacy exact reservation safely attaches without reservation rewrite;
15. unresolved ambiguous legacy reservation → `LEGACY_UNRESOLVED_LINEAGE` and no new key;
16. projection delete/corrupt/rebuild equivalence;
17. mixed-writer fence rejects direct non-lineage production launch path when enforcement enabled;
18. lineage identity cannot be selected by Worker/canonical/Feature branch fields;
19. unknown resolution choice / FORCE/IGNORE/NEW_KEY bypass rejected;
20. contract validator rejects legacy candidate field/test semantics;
21. existing Operation Store validators remain green;
22. existing vertical-loop completion/reconcile/remediation/recovery validators remain green;
23. lifecycle/cross-repository/security/public-runtime/protocol regressions remain green.

## 25. Implementation sequencing

Recommended implementation order:

1. lineage schemas/model/reducer and immutable path rules;
2. trusted lineage identity resolver;
3. lineage-gated reservation atomic command;
4. vertical planner integration and mixed-writer fence;
5. predecessor-state reducer over existing launch history;
6. bounded resolution authority/evidence verifier/application;
7. legacy resolver/migration;
8. machine-readable candidate-contract validator;
9. deterministic concurrency/stale-runner/regression tests;
10. implementation evidence and independent Code Review.

## 26. Non-goals and release boundary

This Design does not implement or claim:

- #221 real-runtime fault injection/dogfood;
- overall v0.3 effect-safety proof;
- external exactly-once execution;
- a new generic post-authorization revocation protocol;
- alternate Store backend work from #220;
- release evidence ledger work from #218;
- general Decision/Notification product scope;
- Product Acceptance automation;
- VERSION/final release manifest/publication.

Feature completion proves only deterministic implementation of the frozen Effect Lineage safety contract. Release-level proof remains separate under #221.
