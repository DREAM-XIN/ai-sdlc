# Code Review Remediation — F-OPERATOR-EFFECT-LINEAGE-0001

## Role and scope

Role: independent Implementation / Remediation Developer responding to `code-review.md` and PR Review `4898019303`.

This remediation addresses exactly the three MAJOR findings from that independent Code Review. It does not perform Code Re-review, does not PASS `code-gate`, does not enter Verification/QA, and does not claim Issue #221 real-runtime fault injection or overall v0.3 release readiness.

Validated remediation functional candidate:

`04982803b67ec4f495f91c74b97133f3fb663bca`

PR: `#228`

## MAJOR-1 — fresh trusted Effect Resolution truth and real evidence source

Closed within the frozen v0.3 resolution contract.

- `plan_effect_resolution(...)` no longer accepts caller copies of current repository / Feature revision / target ref / candidate head as authoritative current truth. It requires a fresh trusted `FeatureSnapshot` and derives those bindings from that snapshot at application time.
- Resolution re-checks the durable Operation generation/profile, current lineage leaf, predecessor reservation/member binding, current successor proposal, exact Feature revision/ref/candidate, and proposal `trusted_profile_digest` against the current trusted `EffectResolutionAuthority` profile digest.
- `EffectResolutionAuthority` now binds the reviewed Operation profile and trusted profile digest in addition to resolver identities and protected policy material.
- `TrustedEffectEvidenceVerifier` no longer promotes caller-provided dictionaries into trusted evidence. The planner accepts only evidence references; a trusted loader/source resolves them and the verifier binds each resulting fact to a trusted source id/digest.
- Strong evidence types (`EXTERNAL_KEY_INVALIDATED`, `NON_OVERLAPPING_SCOPE`) are disabled by default. A verifier must explicitly expose an already-reviewed strong-evidence capability for those types; otherwise `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN` fails closed with insufficient evidence.
- The frozen stale-runner rule remains intact: durable `dispatch.launch.authorized(K0)` plus current `NOT_LAUNCHED` still rejects `PROVE_NOT_LAUNCHED` with `AUTHORIZED_EFFECT_STILL_EXECUTABLE`.

Deterministic adversarial coverage proves:

1. candidate head changes after proposal creation, without Manifest revision movement, reject the resolution as stale;
2. target-ref drift rejects the resolution;
3. trusted profile/policy digest drift rejects the resolution;
4. fabricated invalidation-fence receipts and fabricated non-overlap proof digests cannot become strong trusted evidence through the default verifier;
5. authorized + `NOT_LAUNCHED` remains non-retirable through `PROVE_NOT_LAUNCHED`.

No generic post-authorization revocation primitive was introduced.

## MAJOR-2 — trusted legacy lineage reconstruction

Closed by removing the caller boolean `LegacyMigrationEvidence.unique_lineage_proven` from the attachment authority path.

- `reconstruct_legacy_lineage(...)` derives the causal work mapping from immutable legacy reservation fields, the durable creating Operation/profile projection, and fresh trusted Feature/Manifest truth.
- It validates repository, Feature, revision, Operation profile, stage, role, exact task identity and candidate binding before resolving a causal work slot.
- Vertical task identity patterns are reconstructed rather than selected by the migration caller:
  - primary implementation;
  - code remediation with an authoritative remediation task;
  - initial code review;
  - code re-review with a completed authoritative remediation predecessor;
  - verification QA.
- Missing, malformed, contradictory or non-vertical history cannot be asserted away and remains `LEGACY_UNRESOLVED_LINEAGE` with no member/new external key.
- Attachment also refuses to create a competing ordered member when the reconstructed lineage already contains active causal work.
- The provenance digest is deterministic from durable reservation / Operation / Feature / causal-work material, allowing restart reconstruction to prove the same identity.

Deterministic adversarial coverage proves wrong causal slot, wrong/missing remediation task, incomplete Operation history, a competing plausible same-lineage legacy member, and restart reconstruction behavior.

## MAJOR-3 — protected rollout truth and authoritative mixed-writer fence

Closed by moving rollout authority out of default booleans and into trusted production composition.

- `ProtectedEffectLineageRolloutVerifier` requires a protected/default-branch rollout policy bound to exact repository, protected Operator state ref and reviewed vertical profile.
- The policy digest is re-derived and checked; Effect Lineage enforcement must be explicitly selected.
- Enabling lineage-required writes requires an auditable writer-fence receipt proving `QUIESCED` and covering all old raw external-effect capabilities:
  - raw semantic reservation;
  - raw dispatch claim;
  - raw launch authorization.
- `build_trusted_vertical_runtime(...)` now requires the rollout verifier before it constructs a writable production runtime and rejects test-only rollout evidence.
- `TrustedVerticalExecutorConfig` is fail-closed: it no longer defaults to lineage-required + old-writers-quiesced. Production lineage mode requires verified rollout policy/fence digests; non-lineage mode is available only through an explicit test-only legacy compatibility setting.
- The active Operator Store runtime supports a trusted `plan_guard`; production Vertical composition installs `EffectLineageWriteFence` there, so every CAS re-plan is guarded against the fresh mutation plan before commit.
- The fence rejects raw Vertical reservation/claim/launch-authorization plans after enforcement unless they arise from the trusted lineage-aware composition boundary. Lineage-aware claim/authorization wrappers carry the trusted writer capability marker, while atomic root/successor activation is recognized by the immutable lineage facts created in that same CAS plan.
- The old pre-lineage reconciliation fixture is the only explicit compatibility path and now names `legacy_compatibility_mode=True`; it cannot be confused with production rollout authority.

Deterministic coverage actively attempts raw reservation → raw claim → raw launch authorization after enforcement and requires `MIXED_WRITER_FENCED`; the lineage-aware authorization path remains accepted. A non-QUIESCED rollout receipt is also rejected before write authority is enabled.

## Preserved reviewed positives

The remediation preserves the previously reviewed safety properties:

- exact `semantic_effect_key` remains revision/stage/task/role/candidate-bound;
- one causal lineage remains stable across revision/candidate/generation changes;
- blocked successor proposals have no reservation or external dispatch key;
- root activation and safe successor activation retain protected CAS atomicity;
- CAS losers re-read and semantically re-plan;
- `dispatch.launch.authorized` remains launch linearization;
- authorized + current `NOT_LAUNCHED` remains `AUTHORIZED_NOT_LAUNCHED_OBSERVED` rather than revocation;
- lineage-aware claim and launch authorization re-check the current lineage leaf;
- generation takeover preserves unresolved lineage blocking;
- immutable lineage facts remain rebuildable into the same projection;
- the amended v0.3 release-contract validator remains enforced;
- Feature Manifest + trusted Feature Event/Persist remain lifecycle authority.

## Exact remediation-candidate validation

All required PR-triggered workflows for exact functional candidate `04982803b67ec4f495f91c74b97133f3fb663bca` completed successfully:

- **Validate AI-SDLC protocol** — run `31403074662` — **SUCCESS**.
  - `python scripts/validate.py` — SUCCESS.
  - `cross-repo-control` — SUCCESS.
  - the main validator executes the Effect Lineage adversarial harness plus the full existing Vertical reconciliation/recovery/remediation suite.
- **Validate Public Runtime Distribution** — run `31403074552` — **SUCCESS**.
- **Required PR Gate** — run `31403074308` — **SUCCESS**.

The functional candidate includes the runtime source, compatibility fixture and deterministic tests. Later commits for this remediation are limited to Developer evidence and legal lifecycle recording unless a fresh exact-head comparison proves otherwise.

## Explicit non-scope

This remediation does not implement or claim:

- Issue #221 real-runtime fault injection / release-level duplicate-effect proof;
- external exactly-once execution;
- a new post-authorization revocation protocol;
- alternate Store backend work from #220;
- release evidence-ledger work from #218;
- broader Decision/Notification product scope;
- Product Acceptance or overall v0.3 release readiness.

## Developer conclusion

All three MAJOR findings from Review `4898019303` now have bounded implementation changes and exact-candidate deterministic evidence at `04982803b67ec4f495f91c74b97133f3fb663bca`.

The next authority after legal remediation lifecycle recording is a **fresh independent Code Re-review** bound to the resulting exact PR head. `code-gate` remains PENDING.
