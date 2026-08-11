# Independent Code Review — F-OPERATOR-EFFECT-LINEAGE-0001

## Verdict

**REWORK — 0 BLOCKER / 3 MAJOR / 0 MINOR**

Review role: independent Code Reviewer.

PR Review: `4898019303`.

Reviewed the authoritative Feature state, Issue #226 scope, approved Requirement, Requirement Review, approved Design, independent Design Review, Plan, Developer Implementation/Evidence, the actual PR #228 implementation diff, current Operation Store / Vertical Loop composition, and exact reviewed PR head:

`9da6d1e0bf0db4485da0dc84306a0d80d74e254b`

The Developer's validated functional candidate is:

`b05d2affc7ff5e272e493e1f9dc67e01b6adc97e`

That functional candidate has recorded SUCCESS evidence for Protocol, Public Runtime and Required PR Gate. The later Implementation completion and Code Review lifecycle commits do not modify runtime source/test/schema after that candidate. The exact reviewed head's current PR-triggered workflows are `action_required` rather than executed green jobs, so this review does not misstate them as exact-head CI success.

The implementation gets the central stale-runner ordering right: lineage-gated reservation precedes claim/authorization, blocked successors have proposal-only state, `dispatch.launch.authorized` remains the durable launch linearization point, authorized + current `NOT_LAUNCHED` does not make a successor launch-eligible, and claim/authorization re-check the current lineage leaf.

However, three trusted-boundary defects remain and require Developer remediation before Code Gate PASS.

## MAJOR-1 — Effect Resolution does not re-verify fresh Feature/candidate/policy truth and its default evidence verifier accepts caller assertions

The approved Design requires resolution application to re-read current Store lineage, Operation history, Feature/candidate truth, policy digest and trusted evidence before writing. It also explicitly says this Feature does not invent a generic post-authorization revocation primitive; `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN` may proceed only when an already-reviewed trusted evidence source proves an authoritative external fence or genuine non-overlap.

The implemented `plan_effect_resolution(...)` receives `current_target_ref`, `current_candidate_head_sha`, `EffectResolutionAuthority` and the evidence list as caller parameters. It checks candidate/ref against the already-stored proposal, but it has no trusted Feature gateway or equivalent fresh candidate/PR truth input. A proposal can therefore become stale because the PR/candidate changed without a Feature Manifest revision, while a caller can still replay the proposal's old candidate/ref values and satisfy the planner's checks.

The default `TrustedEffectEvidenceVerifier` is also a structural validator rather than a trust-establishing verifier. It promotes caller-shaped records to verified facts when required strings are merely present:

- `EXTERNAL_KEY_INVALIDATED` requires only the exact key plus a non-empty `fence_receipt`;
- `NON_OVERLAPPING_SCOPE` requires only a non-empty `proof_digest`.

No external gateway/control-plane proof is verified and no protected evidence-source identity is checked. The retirement branch then accepts either typed record and may retire an already launch-authorized predecessor and atomically create a successor reservation/member.

This undermines the approved safety contract specifically in the strongest resolution path: a caller assertion can masquerade as the stronger no-duplicate proof that is supposed to be unavailable unless independently trusted.

### Required remediation

1. Bind resolution application to fresh trusted Feature/candidate/target-ref truth, not only proposal-copied caller values.
2. Re-check current trusted profile/policy material at application time and reject a resolution when it changed after proposal creation.
3. Make strong evidence originate from a real trusted verifier/source. Arbitrary input dictionaries must not become verified `EXTERNAL_KEY_INVALIDATED` / `NON_OVERLAPPING_SCOPE` facts merely because strings are present.
4. If no reviewed trusted strong-evidence source exists, `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN` must fail closed / remain blocked for the unsupported branch.
5. Add deterministic negative tests for:
   - candidate head changes after proposal creation without Manifest revision movement;
   - target-ref/profile-policy drift;
   - fabricated invalidation fence receipts;
   - fabricated non-overlap proof digests;
   - durable authorized + `NOT_LAUNCHED` remaining non-retirable through `PROVE_NOT_LAUNCHED`.

## MAJOR-2 — Legacy migration “unique lineage proven” is a caller boolean instead of trusted reconstruction

The approved Requirement/Design requires legacy attachment to reconstruct causal material from trusted durable Store + Feature + profile history and attach only when exactly one causal lineage is actually proven. Ambiguous potentially executable legacy work must stay `LEGACY_UNRESOLVED_LINEAGE`.

The implementation instead exposes `LegacyMigrationEvidence` with:

- an enum-like `source` string;
- an arbitrary non-empty `provenance_digest`;
- `unique_lineage_proven: bool`.

`plan_legacy_lineage_attachment(...)` trusts that boolean. It also accepts `logical_work_slot` and `task_id` as caller parameters and feeds them into `CausalWorkResolver`; it does not independently prove that those supplied causal inputs correspond to the legacy reservation's durable task/stage/profile history.

The current deterministic migration test therefore proves only that `False` blocks and `True` attaches. It does not prove that the implementation itself establishes uniqueness from durable history.

A caller inside the trusted process can assert `unique_lineage_proven=True` with the wrong durable slot/task mapping, attach the reservation to the wrong lineage, and leave the actually overlapping causal lineage looking empty. A subsequent planner for the true lineage can then create a fresh root/reservation/key instead of being blocked by the legacy predecessor.

### Required remediation

1. Replace the free boolean assertion with a trusted reconstruction result whose uniqueness is computed from durable Store/Feature/profile facts.
2. Derive or validate causal work slot/task identity from the legacy reservation and authoritative lifecycle history; do not let the migration caller choose a fresh causal lineage discriminator.
3. Validate the complete relevant identity/provenance boundary (repository, Feature, stage/role/task/candidate and Operation/profile history as applicable).
4. Ambiguous, incomplete or contradictory history must remain `LEGACY_UNRESOLVED_LINEAGE` with no member/new external key.
5. Add deterministic adversarial tests for wrong slot, wrong remediation/task id, incomplete history, competing plausible lineage identities and restart reconstruction.

## MAJOR-3 — Mixed-writer rollout fencing is declarative and defaults to “quiesced” instead of protected runtime truth

The approved Design requires a real rollout fence:

1. lineage-aware readers/reducers deploy;
2. state-ref protection/schema support is verified;
3. old production vertical writers are quiesced/fenced;
4. only then `effect_lineage_required` becomes authoritative;
5. after enforcement, no non-lineage writer can create/authorize launch-eligible reservations.

The implementation represents this with two booleans on `TrustedVerticalExecutorConfig`:

- `effect_lineage_required: bool = True`;
- `old_writers_quiesced: bool = True`.

`validate_lineage_rollout(...)` only rejects the combination `required=True / quiesced=False` supplied to the constructor. The production `TrustedVerticalLoopConfig` does not expose a protected rollout policy/receipt, and `build_trusted_vertical_runtime(...)` constructs `TrustedVerticalExecutorConfig` without either field, so production simply inherits the defaults and asserts that old writers are already quiesced.

This is not the mixed-writer fence required by Design. The raw pre-lineage reservation/claim/authorization primitives remain available to an old process/version. If such a writer is still running, it can continue creating launch-eligible work outside the lineage gate while the new production runtime believes enforcement is safe.

### Required remediation

1. Source `effect_lineage_required` and writer-fence state from trusted protected installation/default-branch/runtime policy, not constructor defaults.
2. Require an auditable quiescence/version/capability/fence receipt or equivalent protected proof before enabling lineage-required production writes.
3. Unknown/unverified writer-fence state must fail closed; it must never default to `old_writers_quiesced=True`.
4. Enforce the fence at an authoritative production write/launch boundary so a retained old writer cannot still use the raw reservation → claim → authorization path after rollout.
5. Add deterministic mixed-writer coverage where an old writer actively attempts reservation/claim/authorization after enforcement and is rejected by the authoritative fence, rather than merely testing an invalid boolean pair at object construction.

## Reviewed positives to preserve

The following implementation aspects are consistent with the approved contract and should not be regressed during remediation:

- protected immutable anchor/member/proposal/event/resolution paths and replaceable lineage projection only;
- deterministic lineage projection rebuild from immutable facts;
- exact semantic identity remains revision/stage/task/role/candidate-bound;
- reviewer candidate A→B remains fresh exact work but proposal-only while predecessor is unresolved;
- root reservation/member creation and safe successor activation use one Store mutation plan / protected ref CAS;
- CAS losers re-read and semantically re-plan;
- `dispatch.launch.authorized` remains the only launch linearization point;
- authorized + current `NOT_LAUNCHED` is represented as `AUTHORIZED_NOT_LAUNCHED_OBSERVED` and cannot satisfy `PROVE_NOT_LAUNCHED`;
- lineage-aware dispatch claim and launch authorization re-check current lineage leaf, closing the opposite stale-runner race;
- generation takeover preserves `lineage_blocks` and does not manufacture a fresh lineage;
- blocked successor proposals contain no external dispatch key;
- the release-contract validator rejects the removed `head_change_requires_new_semantic_dispatch` field and `new-head-requires-new-semantic-dispatch` test identity;
- #221 real-runtime fault injection remains correctly outside this Feature's deterministic evidence.

## Gate decision

`code-gate` remains **PENDING**. No PASS or waiver is authorized by this review.

A separate Developer remediation must address exactly the three MAJOR findings above and produce a new exact runtime candidate with deterministic evidence. A fresh independent Code Re-review must then bind to the actual resulting PR head and verify closure before `code-gate` can PASS.
