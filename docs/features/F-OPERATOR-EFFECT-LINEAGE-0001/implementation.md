# Implementation — F-OPERATOR-EFFECT-LINEAGE-0001

## Status

Implementation of the approved `requirement-v1`, `design-v1`, and `plan-v1` is complete for the bounded `F-OPERATOR-EFFECT-LINEAGE-0001` scope.

Validated functional candidate:

`b05d2affc7ff5e272e493e1f9dc67e01b6adc97e`

PR: `#228`

This document is Developer implementation output only. It is not an independent Code Review, QA verdict, Product Acceptance decision, #221 real-runtime fault-injection result, or v0.3 release-readiness claim. `code-gate` remains PENDING.

## Durable Effect Lineage state

The Operator Store now supports protected durable Effect Lineage facts alongside the accepted Operation Store:

- immutable lineage anchor;
- immutable exact lineage members;
- immutable successor proposals;
- append-only lineage events;
- immutable Effect Resolution records;
- replaceable/rebuildable lineage projection cache.

`operator_store_model.py` treats anchor/member/proposal/event/resolution paths as immutable Store paths and only allows lineage projections to use replaceable projection semantics. Existing exact semantic reservations remain immutable; a lineage member binds a reservation and its stable `external_dispatch_key` without rewriting the reservation.

`operator_effect_lineage_model.py` validates path/record binding and deterministically rebuilds the current lineage leaf, proposal, blocking relation, predecessor state, relations, sequence and history digest from immutable facts.

## Trusted lineage identity

`CausalWorkResolver` is the trusted v0.3 vertical-profile resolver for causal-work identity.

`effect_lineage_id` is derived from canonical trusted material equivalent to:

- target repository;
- Feature id;
- approved Operation profile;
- effect kind;
- lifecycle role;
- durable logical causal-work id;
- trusted external-effect scope.

Feature revision, candidate SHA, Operation id/generation, runner/process/session identity, cancellation and supersession are not lineage discriminators. Unsupported or ambiguous logical work mappings fail closed instead of generating a fresh lineage.

Exact `semantic_effect_key` identity remains separately bound to the existing exact revision/stage/task/role/candidate material.

## Atomic lineage-gated reservation

`plan_lineage_gated_reservation(...)` is the production composition boundary before creation of a new launch-eligible exact reservation.

Against one Store snapshot it:

1. validates the current Operation generation/profile/revision fence;
2. derives trusted lineage identity;
3. rebuilds current lineage state;
4. converges exact current-member recovery onto the existing reservation/key;
5. creates a root reservation + member + lineage facts atomically when no predecessor exists;
6. creates only an immutable proposal + blocking facts when a different exact successor is blocked;
7. returns no successor external key while blocked.

All mutations are returned in one `StoreMutationPlan` and use the existing protected state-ref CAS. Existing `commit_replanned(...)` semantics re-read and semantically re-plan after a CAS conflict.

## Launch and stale-runner fencing

`dispatch.launch.authorized` remains the unique launch linearization point.

Lineage reduction distinguishes:

- `NEVER_AUTHORIZED`;
- `AUTHORIZED_UNCONFIRMED`;
- `AUTHORIZED_NOT_LAUNCHED_OBSERVED`;
- `LAUNCHED_CORRELATED`;
- `UNKNOWN`;
- trusted retired/legacy states.

A lookup observation cannot erase durable launch authorization.

The production claim and launch paths are additionally fenced through `plan_lineage_dispatch_claim(...)` and `plan_lineage_authorize_launch(...)`, which re-check that the exact effect is still the current launch-eligible lineage member. This establishes both sides of the critical ordering:

- if predecessor retirement + successor activation wins the protected CAS before old authorization, a stale predecessor runner cannot later authorize the old non-leaf effect;
- if `dispatch.launch.authorized(K0)` is durable first, `PROVE_NOT_LAUNCHED` cannot retire K0 merely because current lookup reports `NOT_LAUNCHED`.

The deterministic stale-runner harness proves:

`dispatch.launch.authorized(K0)` → paused runner → `NOT_LAUNCHED` observation → K1 proposal → no K1 reservation/key/claim/authorization → `PROVE_NOT_LAUNCHED` rejected → only exact K0 remains launch-authorized.

## Bounded Effect Resolution Authority

`operator_effect_resolution.py` exposes only the frozen choices:

- `CORRELATE_EXISTING_RECEIPT`;
- `PROVE_NOT_LAUNCHED`;
- `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`;
- `REMAIN_BLOCKED`.

`EffectResolutionAuthority` may only narrow that frozen set and binds trusted resolver identities and protected policy material. The evidence verifier accepts typed trusted evidence facts rather than Worker/model assertions.

Resolution identity/application binds exact lineage, predecessor semantic/external key, current Operation/generation, Feature revision/ref/candidate, successor proposal/key, trusted policy, resolver and evidence digests. Changed bindings reject stale resolution.

`PROVE_NOT_LAUNCHED` requires trusted external non-launch evidence and a predecessor state of `NEVER_AUTHORIZED`. A durable launch authorization plus current `NOT_LAUNCHED` fails with `AUTHORIZED_EFFECT_STILL_EXECUTABLE`.

`RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN` requires stronger typed proof (`EXTERNAL_KEY_INVALIDATED` or `NON_OVERLAPPING_SCOPE`); this Feature does not invent a new generic post-authorization revocation/fencing protocol.

When an allowed retirement makes a successor eligible, predecessor retirement, exact successor reservation, lineage member activation and resolution facts are composed in one Store mutation plan. Resolution itself does not create a dispatch claim or launch authorization.

## Candidate/revision/generation continuity

Candidate movement remains split into two mechanisms:

- existing exact Reviewer/QA candidate/revision fences reject stale evidence;
- Effect Lineage controls external launch eligibility.

A candidate-B exact work item in the same unresolved causal lineage becomes a fresh immutable B proposal but receives no external reservation/key. Candidate/revision/generation/session changes do not manufacture a new lineage.

Generation takeover preserves Operation `lineage_blocks` in the deterministic Operation projection, so takeover does not clear unresolved lineage state.

## Vertical Loop integration

`TrustedVerticalExecutor` now defaults to `effect_lineage_required=True` and routes production dispatch through:

`lineage gate → exact reservation/member when eligible → lineage-aware claim → lineage-aware dispatch.launch.authorized → existing gateway/lookup/callback/Persist flow`.

Blocked lineage planning returns the Operation's durable `BLOCKED` state before claim/authorization/gateway invocation.

Existing lifecycle authority remains unchanged:

- Feature Manifest + trusted Feature Event/Persist remain Feature truth;
- Reviewer/QA candidate and role-independence fences remain in the accepted vertical implementation;
- Persist requested/linearized/confirmed remains unchanged;
- lineage state cannot PASS Feature gates or synthesize lifecycle authority.

The historical `validate_operator_vertical_reconcile.py` fault/replay fixtures deliberately construct pre-lineage reservations to verify accepted legacy Store recovery. Only that fixture explicitly selects `effect_lineage_required=False`; production runtime defaults and the new lineage validation remain fail-closed.

## Legacy migration and rollout

`operator_effect_migration.py` preserves legacy reservation readability while requiring trusted reconstruction for lineage attachment.

- unique trusted provenance may attach an immutable member without rewriting the legacy reservation;
- ambiguous reconstruction yields `LEGACY_UNRESOLVED_LINEAGE` and no member/new key;
- `effect_lineage_required=True` with old writers not quiesced fails with `MIXED_WRITER_FORBIDDEN`.

This gives the rollout order required by the approved Design: quiesce/fence old production reservation writers before lineage-required semantics become authoritative.

## Release-contract validator

`validate_v03_effect_lineage_contract.py` validates the amended machine-readable v0.3 contract and contains negative fixtures that reject reintroduction of:

- `worker_result_contract.head_change_requires_new_semantic_dispatch`;
- candidate test identity `new-head-requires-new-semantic-dispatch`.

It requires the amended semantics instead: stale candidate evidence invalidation, fresh exact candidate-bound work, `head_change_alone_authorizes_new_external_dispatch: false`, and Effect Lineage clearance before fresh-candidate external dispatch.

## Deterministic validation

`validate_operator_effect_lineage.py`, wired into the authoritative `scripts/validate.py`, exercises the production lineage planners and Store mutation/CAS model, including:

- trusted lineage identity stability and ambiguous fail-closed behavior;
- candidate A→B proposal-only blocking;
- never-authorized trusted non-launch retirement and atomic successor activation;
- authorized + `NOT_LAUNCHED` stale-runner rejection;
- concurrent planner CAS loss + semantic re-plan;
- stale resolution rejection;
- generation takeover preserving lineage block;
- legacy ambiguous/safe migration;
- deterministic projection rebuild;
- mixed-writer fencing;
- schema validation and FORCE/IGNORE/NEW_KEY bypass rejection;
- amended v0.3 release-contract validation;
- all existing Operation Store and Vertical Loop validation aggregated through `scripts/validate.py`.

Exact-head workflow evidence is recorded in `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/evidence/implementation-verification.md`.

## Explicit non-scope

This implementation does not:

- perform Issue #221 real-runtime fault injection/dogfood;
- claim external exactly-once execution;
- introduce a generic post-`dispatch.launch.authorized` revocation primitive;
- add unrelated Decision/Notification product scope;
- implement alternative Operation Store backends for #220;
- implement release-evidence-ledger work for #218;
- change `VERSION`, create `release/v0.3.0.yaml`, or claim overall v0.3 release readiness;
- PASS `code-gate`, `verification-gate`, or `release-gate`.

The next authority after legal Implementation completion is a fresh independent Code Reviewer bound to the actual PR head.
