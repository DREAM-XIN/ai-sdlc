# Requirement — F-OPERATOR-EFFECT-LINEAGE-0001

## 1. Purpose

Implement the frozen AI-SDLC v0.3 Effect Lineage / UNKNOWN-resolution contract as trusted durable control-plane behavior.

The Feature SHALL close the implementation gap identified by Issue #219: exact `semantic_effect_key` identity remains revision/stage/task/role/candidate-bound, while one real-world causal work slot may continue across Feature revision, candidate-head, Operation generation/id, restart, cancellation, or takeover. That continuity SHALL be represented by trusted durable Effect Lineage state so an unresolved or still-executable predecessor cannot be bypassed by deriving a different exact semantic key.

This Feature is an implementation Feature. It SHALL consume the frozen protocol; it SHALL NOT silently redefine it.

## 2. Normative upstream

This Feature consumes, without weakening or silently changing:

- protected `main` baseline `5f37ffd6d9c74a2e350ec369d467ae2026d1753b`;
- `docs/v0.3-release-spec.md`, including the Effect Lineage amendment frozen by PR #225;
- `release/v0.3.0-draft.yaml` as the machine-readable release contract;
- Issue #219 and the independently reviewed amendment source in PR #224;
- independent Amendment Re-review `4896122306` at proposal head `e809f587ce3c52cb18468a49700c8eb07b9123bd`;
- `F-OPERATOR-OPERATION-STORE-0001` / Issue #214 / PR #215;
- `F-OPERATOR-VERTICAL-LOOP-0001` / Issue #216 / PR #217;
- existing launch/Persist linearization, Feature Event/Persist authority, role independence, candidate binding, protected-state CAS, and trusted callback/result boundaries.

If an implementation choice would contradict the frozen Release Spec, the implementation SHALL fail closed and require a separately reviewed protocol amendment rather than reinterpret the requirement locally.

## 3. Product and safety outcome

For every potentially external Worker effect owned by the supported v0.3 Operator slice, trusted planning SHALL be able to determine one of the following before a new exact external reservation is created:

1. there is no prior overlapping lineage member and the exact effect may become the root member;
2. current exact work is an idempotent replay/recovery of the existing exact member;
3. current exact work is a successor proposal blocked by an unresolved predecessor;
4. trusted durable evidence proves predecessor safety is resolved and the successor may become launch-eligible;
5. the new work is provably semantically distinct and belongs to a different lineage;
6. safety cannot be proven, in which case the Operation remains fail-closed `BLOCKED`.

A Feature revision, candidate SHA, Operation generation/id, process/session identity, cancellation, supersession, restart, or missing callback SHALL NOT by itself turn case 3 or 6 into a new launchable external identity.

## 4. Scope boundaries

This Feature SHALL implement the bounded deterministic Effect Lineage control-plane semantics required by v0.3.

It SHALL NOT:

- perform the real-runtime release fault-injection/dogfood tracked by #221;
- claim release-level duplicate-effect safety solely from deterministic Feature tests;
- introduce a generic post-`dispatch.launch.authorized` revocation primitive unless a later independent Design review proves it is required and completely defines its linearization/fencing semantics;
- implement alternative Operation Store backends merely for abstraction purity (#220 remains separate);
- implement release-evidence-ledger synchronization (#218 remains separate);
- broaden general Decision/Notification product scope beyond the bounded Effect Resolution Authority required by the frozen amendment;
- change `VERSION`, create `release/v0.3.0.yaml`, mark v0.3 release-candidate, or claim overall release readiness;
- introduce an external exactly-once execution claim.

## 5. Exact semantic identity and lineage identity

### 5.1 Exact semantic identity remains immutable

The existing exact `semantic_effect_key` contract SHALL remain bound to immutable exact execution facts, including the frozen revision/stage/task/role/candidate dimensions.

The implementation SHALL NOT mutate, rebind, or reinterpret an existing exact semantic reservation to represent a later Feature revision or candidate head.

One exact semantic reservation SHALL continue to have at most one stable `external_dispatch_key`.

### 5.2 Trusted Effect Lineage identity

Every launch-eligible exact semantic reservation in the supported scope SHALL belong to one trusted durable `effect_lineage_id` or a semantically equivalent immutable lineage identity.

Lineage identity SHALL be derived from trusted durable causal-work semantics. At minimum, the design SHALL provide trusted sources for equivalent material to:

- target repository;
- Feature id;
- approved Operation/profile identity;
- effect kind;
- lifecycle role;
- durable causal work/task identity;
- external effect scope when needed to distinguish genuinely separate effects.

A Worker, callback, target Feature branch, ordinary AI client, or arbitrary user payload SHALL NOT select or inject a new lineage discriminator that can bypass predecessor safety.

### 5.3 Stability and distinctness

When the real causal work/effect remains the same, lineage identity SHALL remain stable across:

- Operation generation change;
- Operation id replacement/takeover where the same causal work is inherited;
- Feature revision change;
- candidate-head change;
- process restart;
- chat/client session disappearance;
- runner identity change;
- cancellation/supersession metadata.

A new lineage MAY be created only from reviewed trusted profile semantics plus durable causal facts proving that the new work has genuinely non-overlapping real-world effect scope.

Ambiguity SHALL fail closed; it SHALL NOT default to a fresh lineage.

## 6. Durable lineage state and rebuildability

The implementation SHALL introduce versioned durable Effect Lineage state under the trusted protected Operator persistence boundary.

It SHALL durably represent semantic equivalents of:

- an immutable lineage anchor;
- exact lineage members;
- immutable successor proposals;
- append-only lineage events/facts;
- a rebuildable lineage projection/cache.

The exact file/schema names are Design-level choices, but the authoritative lineage facts SHALL be immutable through the trusted path and the current lineage projection SHALL be deterministically rebuildable from durable facts.

Lineage state SHALL NOT live on the target Feature branch as authoritative orchestration state and SHALL NOT become Feature lifecycle authority.

## 7. Lineage relations and ordered launch eligibility

The supported v0.3 lineage model SHALL enforce an ordered chain of launch-eligible exact members. Concurrent launch-eligible sibling descendants are forbidden.

The model SHALL durably or deterministically represent semantics equivalent to:

- `predecessor`: the prior exact member whose unresolved external state constrains a successor;
- `blocks_on`: derived safety blocking, never a client-controlled waiver flag;
- `supersedes`: current Feature truth no longer wants the predecessor exact work, but this is intent only;
- `adopts`: trusted correlation/use of an already-launched predecessor effect/receipt without relaunch.

`supersedes` SHALL NOT by itself retire an external effect, revoke launch authority, clear `UNKNOWN`, or authorize a successor.

## 8. Lineage gate before new exact reservation

The trusted planner SHALL reconstruct/read lineage state **before** creation of a new exact semantic reservation for work that may overlap an existing causal lineage.

If current authoritative truth proposes exact work `K1` while predecessor `K0` is unresolved or still has executable launch authority:

- the planner SHALL create or reuse an immutable successor proposal for `K1`;
- the successor proposal SHALL bind the current exact semantic material and trusted revision/stage/candidate/provenance context;
- the successor proposal SHALL have no independent `external_dispatch_key`;
- no exact external reservation for `K1` SHALL be created;
- no dispatch claim for `K1` SHALL be made;
- no `dispatch.launch.authorized` for `K1` SHALL be recorded;
- no external launch for `K1` SHALL occur;
- the Operation/lineage SHALL remain `BLOCKED` with a machine-readable reason when no safe recovery action is currently available.

Only after predecessor resolution is durably established may the successor become an active exact member and proceed through the existing reservation/claim/launch fences.

## 9. Concurrency and CAS

Lineage creation, proposal activation, resolution application, predecessor retirement/adoption, and successor activation SHALL use the protected-state compare-and-set discipline already required by the Operation Store.

Before activating a successor, trusted code SHALL prove against one current durable snapshot that:

- the expected lineage anchor/material matches;
- the expected predecessor is still the relevant lineage leaf;
- no incompatible active descendant already exists;
- predecessor safety is resolved under an allowed resolution rule;
- current Feature revision/stage/candidate applicability still matches the successor proposal;
- current Operation generation/ownership is allowed to orchestrate;
- policy/authority inputs remain current.

Concurrent planners racing for the same lineage SHALL converge so at most one activation wins. A CAS loser SHALL re-read and semantically re-evaluate; it SHALL NOT blindly replay a stale activation plan.

## 10. Launch authorization and external lookup semantics

### 10.1 Preserve launch linearization

`dispatch.launch.authorized` SHALL remain the normative launch linearization point.

This Feature SHALL preserve the frozen ordering:

- cancellation/supersession durable before launch authorization: launch forbidden;
- launch authorization durable first: that exact already-authorized launch may still complete using its existing stable external key;
- later cancellation/supersession SHALL NOT retroactively revoke that launch authority.

### 10.2 Distinguish never-authorized from authorized-but-not-yet-launched

Lineage recovery SHALL distinguish at minimum:

- predecessor that never crossed durable launch authorization;
- predecessor with durable launch authorization and no conclusive external result;
- predecessor with durable launch authorization whose current trusted lookup is `NOT_LAUNCHED`;
- predecessor conclusively `LAUNCHED`/correlated;
- predecessor `UNKNOWN`.

These states SHALL NOT be collapsed merely because the current external lookup reports no launch.

### 10.3 `NOT_LAUNCHED` is not revocation

If `dispatch.launch.authorized(K0)` is durable and trusted lookup currently reports `NOT_LAUNCHED`, that observation SHALL NOT:

- revoke or retire the already-linearized launch authority;
- authorize a new reservation/key for `K1`;
- make a successor launch-eligible;
- be treated as proof that a paused/stale authorized runner can no longer execute `K0`.

Allowed recovery is limited to same-existing-key recovery/correlation for `K0` under existing fences or `BLOCKED`, unless stronger trusted no-duplicate evidence satisfies the bounded retirement rule.

## 11. Bounded predecessor resolution outcomes

The implementation SHALL expose only trusted resolution outcomes semantically equivalent to:

```text
CORRELATE_EXISTING_RECEIPT
PROVE_NOT_LAUNCHED
RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN
REMAIN_BLOCKED
```

No generic `FORCE_RETRY`, `IGNORE_UNKNOWN`, `DROP_RESERVATION`, `NEW_KEY_ANYWAY`, or equivalent bypass SHALL be accepted.

### 11.1 `CORRELATE_EXISTING_RECEIPT`

Trusted evidence SHALL prove that the predecessor external key launched and SHALL bind the existing receipt/execution.

The implementation SHALL adopt/correlate the existing effect and SHALL NOT relaunch it.

Current Feature/candidate applicability of any returned Worker result remains separately subject to exact translator/Persist fences.

### 11.2 `PROVE_NOT_LAUNCHED`

This outcome SHALL require both trusted external non-launch evidence and durable launch-authorization history.

Retirement of predecessor `K0` for activation of a different successor SHALL be allowed only when trusted durable history proves no still-executable `dispatch.launch.authorized(K0)` exists.

A durable launch authorization plus current `lookup(K0) == NOT_LAUNCHED` is insufficient for successor activation.

### 11.3 `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`

This outcome SHALL require durable trusted evidence proving that activating the successor cannot duplicate or overlap the predecessor's real-world effect.

For an already launch-authorized predecessor, the proof SHALL be strictly stronger than a current `NOT_LAUNCHED` observation, for example an authoritative external invalidation/fence honored by every possible launcher or an authoritative non-overlapping external scope proof.

If no such proof exists, the lineage SHALL remain `BLOCKED`.

### 11.4 `REMAIN_BLOCKED`

When safe next action cannot be proven from durable facts, `REMAIN_BLOCKED` SHALL be the required outcome.

A `BLOCKED` lineage is a safe suspended orchestration state, not an error that authorizes key regeneration.

## 12. Effect Resolution Authority and audit

Effect resolution authority SHALL originate only from protected/default-branch, installation-level, or trusted-control policy.

Feature branches, Workers, callbacks, ordinary AI clients, or self-asserted model output SHALL NOT expand or self-grant resolution authority.

Every resolution SHALL bind enough exact current state to reject stale or mis-targeted decisions, including semantic equivalents of:

- resolution id;
- target repository;
- Feature id;
- effect lineage id;
- predecessor exact semantic effect key;
- predecessor external dispatch key;
- current Operation id and generation;
- current Feature revision;
- current target ref;
- current candidate head when applicable;
- successor proposal id and proposed exact key when applicable;
- allowed resolution choice;
- trusted policy reference/digest;
- resolver identity;
- evidence references/digests;
- resolution time.

Resolution application SHALL re-read current Feature/lineage/Operation/policy state under CAS and reject stale/mismatched decisions.

A resolution SHALL NOT itself perform an external dispatch. Existing reservation, dispatch claim, launch authorization, candidate, generation, cancellation, and policy fences SHALL still pass afterward.

Audit evidence SHALL allow an independent reviewer to determine who resolved which exact lineage/effect/key, under what trusted policy, using what evidence, what successor became eligible if any, and why the decision cannot create a duplicate real-world effect under the implemented model.

## 13. Candidate-head changes and stale evidence

Candidate-head movement from A to B SHALL have two distinct consequences that the implementation MUST NOT conflate.

### 13.1 Evidence/work applicability

- Reviewer/QA evidence bound to A SHALL be rejected for B.
- A result SHALL NOT be rebound to B.
- current candidate B SHALL receive fresh exact candidate-bound semantic work identity/proposal.

### 13.2 External launch eligibility

Candidate change alone SHALL NOT authorize a new reservation, new external key, launch authorization, or external dispatch.

If the candidate-A predecessor is `UNKNOWN`, launch-authorized-unconfirmed, launch-authorized with current `NOT_LAUNCHED`, or otherwise unresolved in the same overlapping lineage, candidate-B work SHALL remain a blocked successor proposal with no independent external identity.

Only after predecessor safety is durably resolved may B be activated as its own exact member/reservation.

## 14. Revision, generation, restart, cancellation and supersession continuity

Unresolved lineage safety SHALL survive:

- Feature revision advance caused by unrelated authoritative state changes;
- candidate-head advance;
- Operation generation takeover;
- Operation id/session/process replacement where causal work is inherited;
- cancellation/supersession;
- local callback or acknowledgement loss;
- restart/recovery.

None of those facts alone is proof that an old external effect did not launch, can no longer launch, or is semantically distinct.

Takeover/restart SHALL reconstruct the unresolved lineage/predecessor and existing exact external identity before planning a successor.

## 15. Legacy reservations and mixed-writer migration

Existing legacy exact semantic reservations SHALL remain readable.

Trusted migration MAY attach immutable lineage metadata when causal lineage can be reconstructed from durable trusted evidence without ambiguity.

If an unresolved legacy reservation has no safely provable lineage for potentially overlapping new work, the system SHALL fail closed using `LEGACY_UNRESOLVED_LINEAGE` or a semantically equivalent machine-readable state and SHALL block the overlapping new launch.

Before Effect Lineage semantics become authoritative in a production path, old writers capable of creating exact reservations without lineage gating SHALL be fenced/quiesced. Mixed old/new semantic writers are forbidden.

The implementation SHALL document and deterministically test the migration/fencing boundary.

## 16. Integration with Operation Store and vertical loop

The Feature SHALL extend the existing trusted Operation Store/vertical-loop implementation rather than create a parallel orchestration authority.

Integration SHALL preserve:

- protected control-state persistence and Git-ref CAS;
- existing exact semantic reservation and external key immutability;
- generation-specific dispatch claims;
- launch authorization linearization;
- trusted receipt lookup/callback correlation;
- Persist linearization and Feature Event/Persist authority;
- candidate-head exact binding;
- Reviewer/QA independence;
- restart/resume recovery;
- honest `BLOCKED`, `WAITING_EXTERNAL`, `NEEDS_USER`, `DONE`, and `CANCELLED` semantics.

Existing Operation projections may reference lineage state, but lineage projection SHALL NOT replace authoritative immutable lineage facts.

## 17. Machine-readable contract and validator consistency

The implementation SHALL keep `release/v0.3.0-draft.yaml`, deterministic validator behavior, and the frozen Release Spec semantically consistent.

The post-amendment validator SHALL reject the removed/ambiguous candidate contract rather than allowing it to reappear as normative behavior:

- `worker_result_contract.head_change_requires_new_semantic_dispatch: true`;
- required test identity `new-head-requires-new-semantic-dispatch`.

The effective contract SHALL instead prove:

- stale candidate evidence invalidation;
- fresh exact candidate-bound work/proposal after head change;
- `head_change_alone_authorizes_new_external_dispatch: false` semantics;
- Effect Lineage clearance before fresh candidate external dispatch;
- absence of the legacy contradictory field/test identity once the amended validator is active.

## 18. Deterministic verification requirements

Implementation SHALL include deterministic tests for at least:

1. `UNKNOWN@R → unrelated Feature revision R+1 → same causal work`: same lineage; no new external reservation/key/launch.
2. `UNKNOWN@R → candidate A→B`: stale A evidence rejected; fresh B exact proposal; no B external reservation/key/launch while A unresolved.
3. never-launch-authorized predecessor plus trusted `NOT_LAUNCHED` proof: predecessor retirement becomes durable before successor activation/new key.
4. launched predecessor: trusted receipt is correlated/adopted; predecessor is not relaunched.
5. no trustworthy proof: lineage remains `BLOCKED` and generic retry/ignore cannot change eligibility.
6. generation takeover preserves lineage/predecessor/external-key blocking.
7. concurrent planners cannot create two active descendants or independent external keys from one unresolved predecessor.
8. stale Effect Resolution is rejected for wrong lineage/effect/key/Operation/generation/revision/ref/candidate/proposal/policy/evidence.
9. Operation cancellation/supersession does not retire unresolved lineage state.
10. unresolved legacy reservation without safely provable lineage fails closed for overlapping launches.
11. stale-runner adversarial race:

```text
K0 exact reservation exists
→ dispatch.launch.authorized(K0, E0) becomes durable
→ stale Runner A pauses before external dispatch(E0)
→ trusted lookup(E0) returns NOT_LAUNCHED
→ current Feature/candidate truth proposes K1 in same Effect Lineage
→ K1 is recorded/reused as successor proposal only
→ PROVE_NOT_LAUNCHED successor activation is rejected because K0 launch authority remains executable
→ assert no K1 reservation/external key/launch authorization exists
→ Runner A resumes and may execute only exact E0
→ system correlates/adopts E0 or remains same-key recovery/BLOCKED
→ assert no execution path permits both E0 and a successor E1 to launch because of the NOT_LAUNCHED observation
```

12. never-authorized stale-runner control branch: no launch authorization exists, trusted non-launch proof resolves predecessor, durable lineage CAS retires it, and only then may successor receive a new exact reservation/key.
13. candidate contract validator rejects the legacy `head_change_requires_new_semantic_dispatch` and `new-head-requires-new-semantic-dispatch` semantics.
14. lineage projection deletion/rebuild yields equivalent authoritative state.
15. concurrent resolution/proposal races converge through CAS and stale decisions fail closed.
16. existing Operation Store, vertical-loop, Feature lifecycle, callback/Persist recovery, cross-repository trust, public-runtime distribution, and protocol validation regressions remain green.

These deterministic tests SHALL NOT be represented as the real-runtime fault-injection evidence required by #221.

## 19. Acceptance criteria

The Feature is acceptable only when independent review and QA can demonstrate all of the following from durable implementation evidence:

1. every supported launch-eligible exact reservation is lineage-bound before launch planning;
2. exact semantic reservation/external key identity remains immutable;
3. same causal work cannot obtain a fresh external identity merely through revision/candidate/generation/session/restart/cancel/supersede change;
4. unresolved predecessor state blocks successor reservation/key/launch before reservation creation;
5. concurrent planners cannot create sibling launch-eligible descendants;
6. `dispatch.launch.authorized` ordering remains unchanged and stale pre-authorized runners remain safe against successor activation;
7. authorized + current `NOT_LAUNCHED` is not treated as revocation;
8. the bounded four resolution outcomes are enforced with exact policy/state/evidence binding and stale-decision rejection;
9. candidate A→B stale evidence and fresh work identity semantics are correct while external eligibility remains lineage-gated;
10. ambiguous legacy unresolved state fails closed and old non-lineage writers are fenced before authoritative rollout;
11. trusted Feature/Event/Persist, role independence, protected-state, and client/Worker authority boundaries do not regress;
12. deterministic validation covers the frozen adversarial cases and existing repository regressions.

Product Acceptance for this Feature SHALL mean only that the deterministic implementation of the frozen Effect Lineage / UNKNOWN-resolution contract is complete at Feature scope.

It SHALL NOT mean that v0.3 has release-level effect-safety proof. That proof remains dependent on the separate real-runtime scenarios and evidence tracked by #221.
