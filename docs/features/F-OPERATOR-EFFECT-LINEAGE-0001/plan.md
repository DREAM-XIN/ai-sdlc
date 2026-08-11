# Plan — F-OPERATOR-EFFECT-LINEAGE-0001

## 1. Goal and authority boundary

Implement approved `requirement-v1` and approved `design-v1` as the frozen v0.3 durable Effect Lineage / UNKNOWN-resolution safety layer.

This Plan is implementation orchestration only. It does not change the frozen protocol, does not add a post-`dispatch.launch.authorized` revocation primitive, does not make Effect Lineage Feature lifecycle authority, and does not perform the real-runtime fault-injection/dogfood tracked by Issue #221.

Feature Manifest + trusted Feature Event/Persist remain the only Feature lifecycle authority. Effect Lineage remains protected orchestration safety state beside the existing Operation Store.

Implementation must extend the accepted `F-OPERATOR-OPERATION-STORE-0001` and `F-OPERATOR-VERTICAL-LOOP-0001` runtime rather than create a parallel lifecycle or dispatch authority.

## 2. Verified implementation baseline

At Plan authoring time the approved Design targets the frozen protected `main` baseline `5f37ffd6d9c74a2e350ec369d467ae2026d1753b`.

Relevant accepted runtime boundaries on that baseline include:

- `scripts/operator_store.py` for immutable exact reservations, generation-specific claims, `dispatch.launch.authorized`, launch lookup observations, cancellation/takeover, and Persist facts;
- `scripts/operator_store_model.py` plus the protected Store snapshot / mutation model;
- `scripts/operator_store_git.py` for Git-ref compare-and-set persistence;
- `scripts/operator_vertical_store.py` for vertical exact reservation/Persist overlays;
- `scripts/operator_vertical_executor.py` / `TrustedVerticalExecutor` for current launch ordering;
- `scripts/operator_vertical_controller.py`, callback coordinator/runtime modules, and current deterministic vertical validators;
- `scripts/validate.py` as the authoritative repository validation aggregation path.

The current vertical dispatch path creates an exact reservation before claim and launch authorization. The Effect Lineage implementation therefore must replace that production reservation entry point with a lineage-gated composition boundary; lineage may not be attached after an independently launchable successor reservation already exists.

## 3. Work-unit execution rules

Every work unit below is bounded. A Developer may split code into smaller commits, but must preserve the dependency and safety order here.

For every work unit, completion evidence must record:

1. exact changed paths and exact candidate SHA;
2. the approved Design section/invariant implemented;
3. deterministic validation command(s) and result;
4. any intentional non-scope or deferred proof source;
5. whether the work changes protected Store semantics, vertical integration, migration, or release-contract validation.

No work unit may mark `code-gate`, `verification-gate`, or `release-gate` PASS. Implementation completion only prepares an exact candidate for independent Code Review.

---

## WU1 — Effect Lineage schemas, immutable model, reducer, and rebuildable projection

### Modify scope

Introduce the Design-equivalent lineage model and durable schemas, expected to include focused modules such as:

- `scripts/operator_effect_lineage_model.py`;
- `scripts/operator_effect_lineage.py`;
- `spec/operator/effect-lineage/lineage-anchor.schema.json`;
- `spec/operator/effect-lineage/lineage-member.schema.json`;
- `spec/operator/effect-lineage/lineage-proposal.schema.json`;
- `spec/operator/effect-lineage/lineage-event.schema.json`;
- `spec/operator/effect-lineage/lineage-projection.schema.json`;
- `spec/operator/effect-lineage/effect-resolution-record.schema.json`.

Add only the necessary additive Store-model/Git-mutation hooks so the protected state layout can persist:

- immutable lineage anchor;
- immutable exact lineage member;
- immutable successor proposal;
- append-only lineage event/fact;
- immutable resolution record;
- replaceable lineage projection cache.

Enforce immutable create-once behavior for anchor/member/proposal/event/resolution paths. Projection is the only replaceable lineage path.

### Dependencies

Approved Design only; may begin before later integration work.

### Key safety invariants

- immutable lineage facts are authoritative; projection is never authoritative history;
- one exact `semantic_effect_key` belongs to at most one lineage;
- one member binds the existing immutable reservation and its stable `external_dispatch_key` without rewriting that reservation;
- launch-eligible members form an ordered predecessor chain; sibling active descendants are invalid;
- unknown event/schema types fail closed;
- target Feature branches and Worker payloads never become authoritative lineage storage.

### Deterministic validation

- schema accept/reject fixtures for every record type;
- duplicate equivalent create is idempotent only where existing Store semantics permit it; conflicting create fails;
- update/delete attempts against immutable lineage paths fail;
- projection delete/corruption followed by rebuild produces the same canonical projection/history digest;
- reducer rejects impossible predecessor/sibling/member bindings and unknown event types.

### Completion condition

All lineage facts can be represented, validated, rebuilt deterministically, and persisted under existing protected Store mutation semantics without any vertical dispatch integration yet.

### Evidence requirement

Implementation evidence maps each durable record/path and reducer invariant to tests and records projection-rebuild equivalence output.

---

## WU2 — Trusted lineage identity and `CausalWorkResolver`

### Modify scope

Implement the approved trusted canonical lineage material and a centralized `CausalWorkResolver` owned by reviewed operation-profile semantics.

`effect_lineage_id` must be derived from versioned canonical trusted material equivalent to:

- target repository;
- Feature id;
- operation profile;
- effect kind;
- lifecycle role;
- durable causal work id;
- trusted external effect scope.

The resolver may consume trusted lifecycle task/remediation provenance, stage/role, and approved profile semantics. It must not accept Worker/client-generated lineage ids or arbitrary fresh discriminators.

### Dependencies

WU1.

### Key safety invariants

- Feature revision, candidate SHA, Operation generation/id, process/session id, runner id, cancellation, supersession, or restart cannot manufacture a fresh lineage when causal work is the same;
- exact `semantic_effect_key` remains independently revision/stage/task/role/candidate-bound;
- `external_effect_scope` is profile-defined trusted material, not a caller escape hatch;
- ambiguous same-versus-distinct causal work returns `AMBIGUOUS_LINEAGE` and blocks planning.

### Deterministic validation

- same causal work across revision/candidate/generation/session changes derives the same lineage id;
- genuinely reviewed distinct causal work/scope derives a distinct lineage;
- Worker/canonical request fields attempting to choose lineage id/scope are rejected or ignored as non-authoritative;
- ambiguous provenance fails closed rather than generating a new id.

### Completion condition

Every supported vertical external effect can deterministically resolve trusted lineage material or an explicit fail-closed ambiguity outcome.

### Evidence requirement

Record a table of identity inputs that are included/excluded, with exact test vectors proving stability and distinctness.

---

## WU3 — Atomic lineage-gated reservation and protected-state CAS composition

### Modify scope

Implement the Design-equivalent `plan_lineage_gated_reservation(...)` boundary, likely in `scripts/operator_effect_lineage_integration.py`, and compose it with the existing Store snapshot / `StoreMutationPlan` / Git-ref CAS machinery.

Against one Store snapshot the planner must:

1. derive/validate trusted lineage identity;
2. rebuild/validate lineage projection;
3. classify current exact work as root, existing-member recovery, blocked successor proposal, or safely activatable successor;
4. when blocked, create/reuse only immutable proposal + blocking facts;
5. when eligible, create exact reservation + lineage member + activation facts in one `StoreMutationPlan`;
6. commit the whole plan with one protected state-ref CAS.

CAS conflict handling must re-read and semantically re-plan from trusted inputs. It must never replay a stale mutation plan by changing only the expected ref.

### Dependencies

WU1 + WU2; existing Operation Store CAS behavior remains authoritative.

### Key safety invariants

- lineage gate occurs before any new launch-eligible exact reservation for lineage-required effects;
- blocked successor proposal contains no `external_dispatch_key`, claim, launch authorization, or independent dispatch identity;
- predecessor clearance and successor reservation/member activation are atomic when activation is allowed;
- concurrent planners cannot create sibling active descendants or independent external keys;
- exact existing-member recovery preserves the existing reservation/key.

### Deterministic validation

- root activation creates anchor + reservation + member + events atomically;
- unresolved predecessor yields proposal only and no reservation path for successor;
- two planners racing the same lineage produce at most one active successor;
- injected CAS conflict forces re-read/re-plan and converges on current lineage truth;
- stale planned activation is rejected after another planner changes the lineage leaf.

### Completion condition

No production-callable lineage-required reservation path can create an independently launchable successor without lineage clearance in the same protected snapshot/CAS plan.

### Evidence requirement

Include before/after snapshot assertions for proposal-only and atomic activation cases, plus deterministic CAS-conflict traces.

---

## WU4 — Launch/predecessor effective state and stale-runner safety

### Modify scope

Extend lineage reduction/integration over existing immutable Operation Store history so predecessor effective state distinguishes at least:

- `NEVER_AUTHORIZED`;
- `AUTHORIZED_UNCONFIRMED`;
- `AUTHORIZED_NOT_LAUNCHED_OBSERVED`;
- `LAUNCHED_CORRELATED`;
- `UNKNOWN`;
- trusted retired state(s) such as `RETIRED_NO_DUPLICATE_PROVEN`;
- legacy unresolved state.

Do not change the meaning or ordering of existing `dispatch.launch.authorized`; it remains the unique launch linearization point.

### Dependencies

WU1 + WU3 and existing Store launch/lookup history.

### Key safety invariants

- cancellation/supersession durable before launch authorization forbids launch;
- durable launch authorization first means only that exact existing key may still complete;
- current `NOT_LAUNCHED` is an observation and cannot erase durable authorization;
- authorized + `NOT_LAUNCHED` cannot retire K0, activate K1, or create a new key;
- same-key recovery/correlation for K0 or `BLOCKED` is the only frozen v0.3 behavior absent stronger separately trusted proof.

### Required deterministic stale-runner case

Implement a no-sleep deterministic harness with an explicit controllable runner/gateway barrier:

1. durably record `dispatch.launch.authorized(K0)`;
2. pause stale runner after authorization but before external call;
3. trusted lookup records `NOT_LAUNCHED` for K0;
4. current truth produces K1 successor proposal in the same lineage;
5. K1 reservation/member/key activation attempt must fail/return blocked (`AUTHORIZED_EFFECT_STILL_EXECUTABLE` or equivalent); assert no K1 reservation, claim, authorization, or external key exists;
6. resume stale runner; it may launch only the exact existing K0 external key;
7. assert the observation never creates a state in which K0 and K1 are both externally launchable.

Also cover never-authorized K0 separately; it must not be conflated with the stale-runner case.

### Completion condition

Predecessor state is derived from durable launch history plus observations, and no lookup observation can revoke an already-linearized launch.

### Evidence requirement

Record the full stale-runner state/event sequence and exact assertions proving absence of any K1 external identity before safe clearance.

---

## WU5 — Bounded Effect Resolution Authority and stale-resolution rejection

### Modify scope

Implement `EffectResolutionAuthority`, typed trusted evidence verification, and the resolution planner/application path, expected in `scripts/operator_effect_resolution.py` plus WU1 schemas.

Allow only:

- `CORRELATE_EXISTING_RECEIPT`;
- `PROVE_NOT_LAUNCHED`;
- `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`;
- `REMAIN_BLOCKED`.

Bind immutable resolution records to exact current state equivalent to lineage/effect/key, Operation id/generation, Feature revision/ref/candidate, successor proposal, policy digest, resolver identity, evidence digests, and time.

### Dependencies

WU1 + WU3 + WU4.

### Key safety invariants

- no FORCE/IGNORE/DROP/NEW_KEY or semantically equivalent bypass;
- Worker/model assertions are not trusted evidence;
- `PROVE_NOT_LAUNCHED` requires trusted external non-launch evidence and durable proof that no still-executable launch authorization exists; any durable K0 authorization makes current `NOT_LAUNCHED` insufficient;
- `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN` requires stronger typed trusted proof; this Feature does not invent a new generic gateway invalidation primitive;
- stale lineage/effect/key/generation/revision/ref/candidate/proposal/policy/evidence binding rejects resolution;
- resolution record/event by itself never dispatches;
- when resolution safely activates a successor, retirement/adoption + reservation/member activation occur atomically in one CAS plan.

### Deterministic validation

- each of the four allowed outcomes with valid/invalid evidence;
- forbidden resolution names rejected at schema and command boundary;
- wrong predecessor key, external key, Operation/generation, revision, target ref, candidate, proposal, policy digest, or evidence digest rejected;
- already-authorized + `NOT_LAUNCHED` rejects `PROVE_NOT_LAUNCHED`;
- launched receipt correlates/adopts exact predecessor without relaunch;
- insufficient stronger proof remains `BLOCKED`;
- resolution commit does not create dispatch claim or authorization.

### Completion condition

Only the frozen bounded resolution authority can change predecessor safety state, all decisions are exact-state/evidence bound, and activation remains separately fenced.

### Evidence requirement

Provide a resolution matrix showing required evidence, allowed state transition, forbidden conditions, and exact deterministic test names.

---

## WU6 — Candidate A→B, revision continuity, and generation takeover

### Modify scope

Integrate current exact candidate/revision work selection with trusted lineage identity and proposal semantics.

Candidate A→B must remain two separate mechanisms:

- existing Reviewer/QA exact-candidate fences reject A evidence/results for B;
- B receives fresh exact candidate-bound semantic work/proposal;
- lineage decides whether B may obtain an external reservation/key.

Generation takeover/restart/revision advance must reconstruct existing lineage before any successor planning.

### Dependencies

WU2 + WU3 + WU4 + WU5.

### Key safety invariants

- candidate/revision/generation movement never by itself retires predecessor or creates a new lineage for same causal work;
- unresolved candidate-A predecessor means candidate-B has proposal only, with no reservation/external key;
- stale A callback/result is never rebound to B;
- after safe predecessor clearance, B activates as its own exact member/reservation under current B exact binding;
- generation takeover may recover the existing exact K0 key under current Store rules but cannot create K1 merely because generation changed.

### Deterministic validation

- candidate A→B with A `UNKNOWN`: reject stale A evidence, create fresh B proposal, assert no B external identity;
- candidate A→B after safe never-authorized retirement: atomically activate B and then use normal claim/launch fences;
- unrelated Feature revision advance with same causal work preserves lineage/block;
- G→G+1 takeover preserves unresolved lineage and same-key recovery responsibility;
- cancellation/supersession does not retire unresolved lineage.

### Completion condition

Fresh work applicability and external launch safety are demonstrably separate, with lineage continuity surviving candidate/revision/generation changes.

### Evidence requirement

Record A and B exact semantic materials, shared lineage id, proposal/member state, and stale-evidence rejection assertions.

---

## WU7 — Vertical Loop integration without a second lifecycle authority

### Modify scope

Replace the production vertical reservation composition in `scripts/operator_vertical_store.py` / `scripts/operator_vertical_executor.py` (or Design-equivalent focused integration) so `TrustedVerticalExecutor._dispatch()` uses the lineage-gated reservation result before dispatch claim.

Reuse:

- existing `TrustedVerticalExecutor` feature-stage/candidate fences;
- existing generation-specific `plan_dispatch_claim`;
- existing `plan_authorize_launch` linearization;
- existing launch lookup/callback correlation;
- existing Persist requested/linearized/confirmed flow;
- existing Feature Event/Persist lifecycle authority.

Map lineage outcomes only into normal orchestration behavior: existing-member recovery, normal dispatch, or safe `BLOCKED` stop.

### Dependencies

WU3 + WU4 + WU5 + WU6.

### Key safety invariants

- no second lifecycle planner/gate authority is introduced;
- lineage cannot PASS Feature gates, create arbitrary Feature Events, or replace Manifest truth;
- generation takeover preserves lineage blocking;
- dispatch claim and launch authorization remain later existing Store steps after an eligible member/reservation exists;
- callback/Persist candidate/revision fences remain unchanged and cannot be bypassed by lineage correlation.

### Deterministic validation

- happy vertical path with root lineage member proceeds through existing claim/launch flow;
- blocked successor causes executor stable `BLOCKED` without claim/authorization;
- resume/takeover reconstructs lineage and does not regenerate key;
- existing callback coordinator still rejects stale candidate/revision/role/provenance;
- Operation DONE still does not imply Acceptance/release-gate PASS.

### Completion condition

All supported production vertical external dispatches traverse the lineage gate while existing lifecycle/launch/Persist authorities remain intact.

### Evidence requirement

Include an integration call-flow trace from `select_vertical_action` through lineage gate → reservation/member → claim → `dispatch.launch.authorized` → gateway, and a blocked call-flow proving dispatch is never reached.

---

## WU8 — Legacy migration and fail-closed mixed-writer rollout

### Modify scope

Implement `LegacyLineageResolver` / migration helper expected in `scripts/operator_effect_migration.py` plus trusted rollout enforcement such as `effect_lineage_required` under protected/default-branch or installation-controlled configuration.

Existing legacy exact reservations remain readable and immutable.

Safe migration may add immutable lineage attachment facts only when trusted Store + Feature + profile history proves a unique causal lineage.

### Dependencies

WU1 + WU2 + WU3 + WU7.

### Key safety invariants

- unresolved or potentially executable legacy reservation with ambiguous lineage fails closed as `LEGACY_UNRESOLVED_LINEAGE` or exact semantic equivalent;
- migration never invents a new lineage merely to unblock launch;
- old production writers that can create launch-eligible reservations without lineage gating must be quiesced/fenced before `effect_lineage_required` becomes authoritative;
- after the flag is active, lineage-required production launch refuses reservations lacking a valid lineage member;
- supported rollout has no mixed old/new writer period for the same profile/effect scope.

### Deterministic validation

- unique provable legacy attachment succeeds without rewriting reservation;
- ambiguous legacy overlap blocks new successor/key;
- old writer output is rejected by lineage-required launch path;
- rollout-order fixture proves enabling lineage-required before quiescing writer is invalid/fail-closed;
- new writer after fence produces normal lineage member/reservation.

### Completion condition

Legacy data remains readable, ambiguous unresolved state is safe, and rollout cannot silently reintroduce pre-lineage writers.

### Evidence requirement

Document migration decision inputs, ambiguous cases, rollout ordering, capability/config source, and mixed-writer rejection results.

---

## WU9 — Release-contract validator and candidate-semantics consistency

### Modify scope

Add or extend the deterministic v0.3 Release Spec / `release/v0.3.0-draft.yaml` consistency validator and wire it into the authoritative validation path.

The validator must reject reintroduction of the removed legacy candidate contract:

- `worker_result_contract.head_change_requires_new_semantic_dispatch: true`;
- required test id `new-head-requires-new-semantic-dispatch`.

It must require the amended semantics, including:

- stale candidate evidence invalidation;
- fresh exact candidate-bound work after head change;
- `head_change_alone_authorizes_new_external_dispatch: false`;
- fresh candidate external dispatch requires Effect Lineage clearance;
- effect-lineage stale-runner/concurrency/candidate test identities required by the frozen draft.

### Dependencies

Frozen Release Spec/draft are normative. Final validator assertions should land after WU6 semantics are concretely testable.

### Key safety invariants

- validator cannot accept both old and amended contradictory candidate semantics;
- machine-readable draft and frozen Release Spec remain semantically aligned;
- validation only checks the frozen contract; it does not rewrite Release Spec or claim release readiness.

### Deterministic validation

- fixture containing legacy field is rejected;
- fixture containing legacy test identity is rejected;
- fixture missing amended candidate/lineage fields is rejected;
- current amended `release/v0.3.0-draft.yaml` passes;
- validator is invoked from `scripts/validate.py` or an equivalently authoritative aggregation path.

### Completion condition

A future regression cannot silently restore “head change itself requires/authorizes new semantic external dispatch” semantics.

### Evidence requirement

Record positive current-draft validation plus negative legacy-contract fixtures and exact validator integration path.

---

## WU10 — Deterministic adversarial verification and full regression integration

### Modify scope

Create the focused Effect Lineage validator, expected as `scripts/validate_operator_effect_lineage.py`, and wire all required focused validation into `scripts/validate.py` without removing any existing validators.

Reuse deterministic in-memory backends, controllable clocks/gateways, CAS-conflict injection, and explicit runner barriers. Do not use sleeps or timing luck as correctness proof.

### Dependencies

WU1–WU9.

### Required deterministic scenario matrix

At minimum include:

1. `UNKNOWN@R → unrelated revision R+1 → same causal work`: same lineage, no new external key;
2. `UNKNOWN@R → candidate A→B`: stale A evidence rejected, fresh B proposal, no B reservation/key while A unresolved;
3. never-authorized predecessor + trusted `NOT_LAUNCHED`: durable retirement before atomic successor activation;
4. launched predecessor: exact receipt correlation/adoption, no relaunch;
5. no trustworthy proof: `BLOCKED`;
6. generation takeover preserves lineage blocking and same-key recovery;
7. concurrent planners: one activation winner, no sibling active descendants/external keys;
8. stale resolution rejected for wrong lineage/effect/key/Operation/generation/revision/ref/candidate/proposal/policy/evidence;
9. cancellation/supersession does not retire unresolved lineage;
10. unresolved legacy reservation without provable lineage fails closed;
11. required stale-runner sequence: `dispatch.launch.authorized(K0)` → pause → `NOT_LAUNCHED` → K1 proposal → K1 activation rejected/no key → stale runner can resume only exact K0;
12. projection rebuild equivalence from immutable facts;
13. mixed-writer fence / `effect_lineage_required` enforcement;
14. Worker/client cannot choose lineage id or resolution authority;
15. FORCE/IGNORE/NEW_KEY bypass names rejected;
16. release-contract validator rejects `head_change_requires_new_semantic_dispatch` and `new-head-requires-new-semantic-dispatch`;
17. existing Operation Store validators remain green;
18. existing Vertical Loop completion/recovery/reconcile/remediation/gh-aw validators remain green;
19. lifecycle, cross-repository, security, canonical API, MCP, public-runtime and protocol validators remain green.

### Key safety invariants

- deterministic tests prove the reviewed model only; they are not #221 real-runtime duplicate-effect dogfood evidence;
- no validator may simulate success by bypassing the same planner/Store/vertical boundary production uses;
- all old validators remain present in `scripts/validate.py` unless an independently reviewed replacement is explicitly justified.

### Completion condition

`python scripts/validate.py` succeeds at the exact implementation candidate and contains the Effect Lineage validator in the authoritative path. Exact-head CI required by repository policy is green before Implementation completion evidence is finalized.

### Evidence requirement

Implementation Evidence must pin exact candidate SHA and record:

- focused Effect Lineage command/result;
- full `python scripts/validate.py` result;
- exact-head `Validate AI-SDLC protocol` run/result;
- exact-head `Required PR Gate` run/result;
- exact-head Public Runtime / applicable public-runtime validation result;
- any other required repository checks;
- explicit statement that #221 real-runtime fault injection remains pending/out of scope.

---

## 4. Dependency order

Primary safety order:

```text
WU1 → WU2 → WU3 → WU4 → WU5 → WU6 → WU7 → WU8
                                      └──────→ WU9
WU1..WU9 ───────────────────────────────────→ WU10
```

Allowed limited parallelism:

- schema fixtures in WU1 may be developed alongside identity test vectors for WU2 after canonical fields are fixed;
- WU9 validator scaffolding may begin earlier, but its final accepted assertions must match the implemented WU6 candidate semantics;
- WU10 harness scaffolding may begin early, but no scenario may be declared complete until it runs through the final production composition boundaries.

No work may reorder WU3 so that a successor reservation is created before lineage clearance.

## 5. Implementation completion / Definition of Done

Implementation is complete only when all of the following are true at one exact PR head:

- approved Requirement and Design invariants are mapped to code/tests with no silent protocol amendment;
- Effect Lineage durable schemas/model/reducer/projection are implemented and projection rebuild is deterministic;
- trusted `CausalWorkResolver` and lineage identity are stable across revision/candidate/generation/session changes for the same causal work;
- production vertical dispatch uses atomic lineage-gated reservation before claim/launch authorization;
- `dispatch.launch.authorized` remains the unique launch linearization point;
- authorized + `NOT_LAUNCHED` stale-runner case cannot activate a successor/new key;
- only the four frozen resolution outcomes are accepted with exact state/policy/evidence binding;
- candidate A→B produces fresh B work/proposal but no B external identity until predecessor clearance;
- generation takeover/cancellation/restart preserve lineage blocking;
- legacy ambiguous lineage fails closed and mixed old/new writers are fenced;
- release-contract validator rejects the legacy candidate contract;
- the full deterministic adversarial matrix and all existing Store/Vertical Loop/repository regressions pass;
- `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/implementation.md` and `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/evidence/implementation-verification.md` (or repository-equivalent Implementation Evidence paths) pin the exact candidate and contain the WU/evidence matrix;
- no Feature lifecycle Gate is self-approved by the Developer;
- no `VERSION`, final `release/v0.3.0.yaml`, or overall v0.3 release-ready claim is introduced.

## 6. Handoff after Plan

After this Plan is durably registered and `implementation` becomes `READY`, the next role is an independent Implementation Developer.

The Developer should execute WU1–WU10 against the real current Feature/PR head, produce exact-head Implementation Evidence, and stop at Implementation completion. Fresh independent Code Review must follow; this Plan does not authorize the Developer to PASS `code-gate` or perform Verification/Acceptance.
