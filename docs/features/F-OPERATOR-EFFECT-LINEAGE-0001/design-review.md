# Independent Design Review — F-OPERATOR-EFFECT-LINEAGE-0001

## Role

Independent Design Reviewer.

This review is limited to Design Gate authority. It does not modify runtime code, perform Implementation, perform Code Review, or claim v0.3 release readiness.

## Reviewed state and candidate

The reviewer re-read GitHub state rather than relying on the handoff prompt.

- Feature: `F-OPERATOR-EFFECT-LINEAGE-0001`
- Issue: `#226`
- PR: `#228`
- frozen implementation baseline: `5f37ffd6d9c74a2e350ec369d467ae2026d1753b`
- Design author/lifecycle head before this reviewer started: `615ea9473f02ae7835f58b09851cb1a1292b553b`
- reviewer START Event commit: `dbc984062672620d4e21817ab6b8cd5ba3d9fca8`
- trusted Persist result / reviewed lifecycle head: `40cbd741f8459decc3ab61ae84ff191545da1176`
- authoritative Manifest at review time: revision `7`, `design-review: WORKING`, `design-gate: PENDING`
- approved Requirement: `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/requirement.md`
- Requirement Review: `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/requirement-review.md`
- Design under review: `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/design.md`

The reviewer-owned START Event and its trusted Manifest materialization do not alter the Design content from the author/lifecycle head.

## Review basis

Reviewed against:

- Issue #226 scope and deterministic verification expectations;
- the approved Requirement and Requirement Review carry-forward constraint;
- frozen `docs/v0.3-release-spec.md` at protected baseline `5f37ffd6d9c74a2e350ec369d467ae2026d1753b`;
- amended `release/v0.3.0-draft.yaml` at the same baseline;
- frozen Effect Lineage amendment from #219 / PR #224 and freeze PR #225;
- completed `F-OPERATOR-OPERATION-STORE-0001` architecture and Design Re-review;
- completed `F-OPERATOR-VERTICAL-LOOP-0001` architecture and independent re-review evidence;
- current PR #228 changed-file set and exact Design content.

At the reviewed lifecycle head, PR-triggered `Validate AI-SDLC protocol` and `Required PR Gate` runs are reported by GitHub as `action_required` with no executed jobs. They are therefore not represented as green supporting evidence. This review verdict rests on the Design/Requirement/frozen-contract analysis; exact-head executable CI remains a later implementation/merge concern rather than a substitute for Design review.

## Verdict

**PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR**

The Design satisfies the approved Requirement and preserves the frozen v0.3 Effect Lineage / UNKNOWN-resolution protocol without creating a second lifecycle authority or a post-launch revocation shortcut.

## 1. Effect Lineage identity — PASS

The Design keeps exact `semantic_effect_key` identity revision/stage/task/role/candidate-bound and introduces a distinct trusted `effect_lineage_id` above it.

The lineage key is derived from versioned trusted causal material equivalent to target repository, Feature, approved Operation profile, effect kind, role, causal work id, and trusted external effect scope. The `CausalWorkResolver` is explicitly owned by reviewed trusted profile semantics, maps logical work slots rather than candidate/revision instances, rejects client/Worker random identity as a discriminator, and returns `AMBIGUOUS_LINEAGE` / `BLOCKED` when durable facts cannot prove same-versus-distinct work.

Operation generation/id, process/session identity, runner identity, Feature revision, and candidate SHA therefore cannot directly manufacture a fresh lineage for the same causal work. Exact reservation identity remains immutable and is not rewritten to represent later candidates/revisions.

## 2. Atomic safety boundary — PASS

The critical lineage gate and exact reservation activation are composed at one trusted boundary: `plan_lineage_gated_reservation(...)`.

Against one Store snapshot it rebuilds/validates lineage state, determines predecessor safety, and then either:

- creates/reuses only an immutable blocked successor proposal; or
- creates the exact reservation + lineage member + activation events in one `StoreMutationPlan`.

The complete mutation is committed under one protected state-ref CAS. There is no successful durable intermediate state in which predecessor clearance is committed but the successor reservation/member has not yet been atomically established.

Resolution application uses the same discipline: when a resolution makes a successor eligible, predecessor retirement/adoption, successor activation, reservation creation, member creation, and relevant resolution facts are committed in one CAS plan. A CAS loser re-reads and semantically re-plans instead of replaying stale bytes.

This closes the clearance-before-reservation race and prevents concurrent planners from creating sibling launch-eligible descendants or independent external identities.

## 3. Launch semantics — PASS

`dispatch.launch.authorized` remains the unique launch linearization point inherited from the accepted Operation Store.

The Design explicitly distinguishes:

- `NEVER_AUTHORIZED`;
- `AUTHORIZED_UNCONFIRMED`;
- `AUTHORIZED_NOT_LAUNCHED_OBSERVED`;
- `LAUNCHED_CORRELATED`;
- `UNKNOWN`;
- trusted retired/legacy states.

A durable authorization cannot be erased by a later lookup observation. For `dispatch.launch.authorized(K0)` plus current `NOT_LAUNCHED`, `PROVE_NOT_LAUNCHED` is rejected with `AUTHORIZED_EFFECT_STILL_EXECUTABLE`; recovery is same-existing-key or `BLOCKED`. The stale runner may resume only the exact existing external key.

The Design introduces no generic post-authorization revocation state or gateway fence.

## 4. UNKNOWN / predecessor resolution — PASS

Resolution authority is bounded to the four frozen outcomes:

- `CORRELATE_EXISTING_RECEIPT`;
- `PROVE_NOT_LAUNCHED`;
- `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`;
- `REMAIN_BLOCKED`.

`EffectResolutionAuthority` originates only from protected/default-branch, installation, or trusted-control policy. Worker/model assertions are not evidence. The verifier returns typed evidence facts, and resolution records bind exact lineage/effect/key, Operation/generation, Feature revision/ref/candidate, proposal, policy and evidence state.

Application re-reads current Feature/Operation/lineage/policy/evidence state under CAS and rejects stale decisions. A resolution never dispatches by itself.

For `PROVE_NOT_LAUNCHED`, current v0.3 requires no durable `dispatch.launch.authorized` history plus trusted external non-launch evidence. For an already-authorized predecessor, current `NOT_LAUNCHED` is insufficient. `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN` requires stronger trusted proof and does not invent such a proof source locally.

## 5. Requirement Review MINOR-1 — CLOSED

The Requirement Review required the Design not to treat its own review as authority to invent a new post-authorization revocation/fencing protocol.

The Design closes this constraint explicitly:

- frozen same-key / `BLOCKED` semantics remain authoritative after durable launch authorization;
- this Feature does not add a generic revocation primitive;
- if implementation discovers a need for such a primitive, work must stop fail-closed;
- a separate protocol amendment/review/freeze must precede any future consumption by this Feature.

This is the required stronger interpretation of the approved Requirement and frozen amendment.

## 6. Candidate A → B semantics — PASS

The Design correctly separates candidate evidence applicability from external effect safety.

A→B causes fresh exact candidate-B semantic material and a fresh/reused candidate-B successor proposal; stale A Reviewer/QA result remains rejected by existing exact candidate/revision/Persist fences.

If candidate A is an unresolved overlapping predecessor in the same lineage, B receives no reservation, `external_dispatch_key`, claim, launch authorization, or external dispatch. B can become its own exact member only after predecessor safety is durably resolved under the lineage gate.

This matches the frozen replacement for the legacy `head_change_requires_new_semantic_dispatch` / `new-head-requires-new-semantic-dispatch` contract.

## 7. Migration, rollout and backward compatibility — PASS

Legacy exact reservations remain readable and immutable.

`LegacyLineageResolver` may attach lineage metadata only when trusted durable Store/Feature/profile facts prove a unique safe lineage. Unresolved or potentially executable legacy reservations with ambiguous lineage fail closed as `LEGACY_UNRESOLVED_LINEAGE`; no new lineage/key is invented.

The rollout sequence requires old production vertical writers to be quiesced/fenced before `effect_lineage_required` becomes authoritative. Once enabled, the production launch path requires a valid lineage member. There is no supported mixed-writer mode in which old and new production writers both create launch-eligible reservations for the same profile.

## 8. Operation Store / Vertical Loop integration — PASS

The Design extends the existing protected Operator Store rather than introducing parallel authority:

- immutable lineage facts live under the same trusted protected Operator state ref;
- existing reservations remain immutable;
- Git exact-ref CAS and semantic re-plan are reused;
- generation-specific dispatch claims and stable reservation/external keys remain unchanged;
- launch and Persist linearization remain Store-owned;
- trusted receipt/callback correlation remains in the existing vertical path;
- Feature Manifest + trusted Feature Event/Persist remain lifecycle authority.

Vertical orchestration replaces raw launch-eligible reservation planning with the lineage-gated composition boundary. Lineage outcomes map only to orchestration states such as `BLOCKED` or normal existing launch flow; lineage state cannot PASS Feature gates or synthesize lifecycle Events.

This is compatible with the completed Store and Vertical Loop boundaries and does not create a second state authority.

## 9. Deterministic verification design — PASS

The Design specifies deterministic in-memory/CAS-conflict tests without sleeps and covers the frozen adversarial set, including:

- same causal work across revision/candidate/generation preserving lineage blocking;
- candidate A→B stale evidence plus proposal-only behavior;
- never-authorized + trusted `NOT_LAUNCHED` retirement with atomic successor activation;
- durable launch authorization + `NOT_LAUNCHED` rejection;
- launched receipt correlation/adoption without relaunch;
- no-proof `BLOCKED` behavior;
- concurrent planners and CAS loser re-plan;
- stale resolution binding rejection;
- cancellation/supersession continuity;
- deterministic stale-runner race;
- safe/ambiguous legacy migration;
- projection rebuild equivalence;
- mixed-writer fencing;
- inability of Worker/canonical/Feature-branch fields to choose lineage identity;
- rejection of FORCE/IGNORE/NEW_KEY bypasses;
- candidate-contract validator rejecting the removed legacy semantics;
- Operation Store, Vertical Loop, lifecycle, cross-repository, security, public-runtime and protocol regressions.

The Design also keeps #221 real-runtime fault injection outside this Feature, so deterministic validation is not misrepresented as release-level effect-safety proof.

## Gate recommendation

`design-gate`: **PASS** using `design-v1` as the approved Design.

Authorized lifecycle transition from the reviewed state:

- register this file as `evidence-design-review-v1` with `status: pass`;
- approve `design-v1` using this evidence;
- set `design-gate: PASS` with this evidence;
- set `design-review: DONE`;
- set `plan: READY`.

The next legal role is Plan / Orchestrator. This Design Reviewer stops after the Design Gate transition and does not enter Implementation.
