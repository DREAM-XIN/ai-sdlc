# Independent Code Re-review v2 — F-OPERATOR-EFFECT-LINEAGE-0001

## Role and reviewed state

Role: fresh independent Code Reviewer after Developer remediation for PR Review `4898019303`.

This re-review independently re-read the authoritative Feature Manifest, approved Design, original Code Review, remediation evidence, actual remediation runtime changes, deterministic Effect Lineage validation, CI, and the exact current PR head.

Reviewed final PR head:

`b2d2235fff504f2d7d57d77aad3347a8aaad3a2b`

Validated remediation functional candidate:

`04982803b67ec4f495f91c74b97133f3fb663bca`

Exact comparison from the functional candidate to the reviewed final head contains only:

- `docs/features/F-OPERATOR-EFFECT-LINEAGE-0001/code-review-remediation.md`;
- the legal `CODE-REMEDIATION-DONE` Feature Event;
- authoritative Feature Manifest materialization.

No runtime source, test, schema, or release-contract file changed after the validated remediation candidate.

## Verdict

**REWORK — 0 BLOCKER / 1 MAJOR / 0 MINOR**

MAJOR-2 and MAJOR-3 from the prior Code Review are closed. MAJOR-1 is materially improved but is not fully closed because current trusted resolution policy truth is still not re-verified at application time.

## MAJOR-1 re-review — PARTIAL / REWORK

### What is closed

The remediation correctly fixes several parts of the original finding:

- `plan_effect_resolution(...)` now requires a fresh trusted `FeatureSnapshot`; repository, Feature revision, target ref and candidate head are derived from that snapshot rather than caller copies.
- Proposal revision/ref/candidate bindings are rechecked against fresh Feature truth.
- Operation generation/profile and current lineage leaf/member/reservation/proposal bindings are rechecked.
- `TrustedEffectEvidenceVerifier` now resolves evidence references through a loader and records source identity/digest instead of accepting arbitrary evidence dictionaries directly.
- Strong evidence kinds are disabled by default; a verifier must explicitly enable a reviewed strong-evidence capability.
- Fabricated `EXTERNAL_KEY_INVALIDATED` / `NON_OVERLAPPING_SCOPE` fixtures fail closed under the default verifier.
- Durable `dispatch.launch.authorized` plus current `NOT_LAUNCHED` still rejects `PROVE_NOT_LAUNCHED` with `AUTHORIZED_EFFECT_STILL_EXECUTABLE`.

These are substantive safety improvements and should be preserved.

### Remaining MAJOR — protected resolution policy freshness is still caller-shaped

The prior Code Review explicitly required:

> Re-check current trusted profile/policy material at application time and reject a resolution when it changed after proposal creation.

The current implementation still accepts `EffectResolutionAuthority` directly as a planner argument. There is no trusted policy/authority loader or verifier in `plan_effect_resolution(...)` that re-reads the current protected/default-branch/installation resolution policy at application time.

`_verify_fresh_feature(...)` verifies fresh Feature identity/revision and that the durable Operation profile matches `authority.operation_profile`, but it does not verify that `authority.trusted_policy_ref` / `authority.trusted_policy_digest` are the current protected policy values.

The successor proposal stores and checks `trusted_profile_digest`, but it does not bind the separate Effect Resolution `trusted_policy_digest`. The vertical proposal's current `trusted_profile_digest` is derived from the vertical profile / lineage-required material, not from the Effect Resolution policy digest.

`resolution_id` includes the supplied Authority's `trusted_policy_digest`, which prevents replay of an old resolution id if the caller also supplies a different Authority. It does **not** detect the important adversarial case where protected resolution policy has changed but a stale Authority object is still supplied to the planner: no current policy source is consulted, so the stale Authority remains indistinguishable from current policy.

The focused regression currently described as profile/policy drift changes `authority.trusted_profile_digest`; it does not change only `trusted_policy_digest` or simulate a stale Authority against newer protected policy truth. Therefore the exact prior remediation requirement is not proven.

This is a MAJOR because resolution policy controls the strongest branch, `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`, which may retire a predecessor and atomically activate a successor reservation/member. A stale policy object must not remain sufficient authority for that transition.

### Required remediation

1. Add a trusted current Effect Resolution authority/policy source at application time, sourced from protected/default-branch/installation control state or an equivalent reviewed trusted verifier. The planner must not treat the passed `EffectResolutionAuthority` object itself as proof that policy is current.
2. Bind the proposal/resolution application precondition to the relevant current resolution policy epoch/digest and reject when the protected policy changed after proposal creation, as required by the previous Code Review.
3. Ensure strong-evidence capability selection is authorized by current trusted policy/source rather than only by construction of a verifier object that may itself be stale.
4. Add a deterministic adversarial test in which candidate/ref/revision/profile remain unchanged, the protected/current `trusted_policy_digest` changes, and a stale Authority/verifier from the old policy is rejected.
5. Preserve the existing candidate/ref/profile drift, fabricated strong-evidence, and authorized+`NOT_LAUNCHED` regressions.

## MAJOR-2 re-review — PASS

The legacy migration caller-boolean defect is closed.

- `LegacyMigrationEvidence.unique_lineage_proven` is no longer an attachment authority.
- `reconstruct_legacy_lineage(...)` derives the vertical causal slot from immutable reservation fields, durable creating Operation/profile state, and fresh trusted Feature/Manifest history.
- repository, Feature, revision, Operation profile, stage/role/task identity and candidate-bound task formats are checked before `CausalWorkResolver` is called.
- remediation and re-review identities must map to authoritative Feature remediation tasks; completed remediation is required for re-review.
- malformed slot/task identity, missing Operation history and competing same-lineage legacy work all remain `LEGACY_UNRESOLVED_LINEAGE` with no new external key/member.
- restart reconstruction deterministically produces the same provenance digest.

The deterministic migration cases cover wrong slot, missing remediation task, incomplete durable Operation history, competing plausible same-lineage work and restart reconstruction.

## MAJOR-3 re-review — PASS

The prior declarative mixed-writer configuration defect is closed within the approved Design's rollout model.

- production `TrustedVerticalExecutorConfig` no longer defaults to lineage-required + writers-quiesced; non-lineage execution is fail-closed except for explicit test-only legacy compatibility.
- `ProtectedEffectLineageRolloutVerifier` requires protected/default-branch policy binding to repository, state ref and vertical profile plus an auditable `QUIESCED` writer-fence receipt covering raw reservation/claim/launch authorization capabilities.
- production `build_trusted_vertical_runtime(...)` requires the rollout verifier and installs `EffectLineageWriteFence` as the active Operator Store runtime `plan_guard`.
- `OperatorStoreRuntime.commit_replanned(...)` invokes that guard for every fresh CAS re-plan before commit.
- raw vertical reservation, raw dispatch claim and raw launch authorization are rejected after enforcement; lineage-aware claim/authorization carry the trusted writer capability marker, while root/successor atomic activation is recognized by the immutable lineage facts in the same CAS plan.
- the deterministic test actively submits raw reservation → claim → authorization through the guarded authoritative runtime and requires `MIXED_WRITER_FENCED`; a non-QUIESCED rollout also fails before authority is enabled.

This satisfies the approved rollout sequence: old writers must first be quiesced/fenced by trusted installation control, and the active production runtime then refuses non-lineage external-effect writes/launch authorization.

## Regression / CI evidence

Exact remediation functional candidate `04982803b67ec4f495f91c74b97133f3fb663bca`:

- Validate AI-SDLC protocol — run `31403074662` — **SUCCESS**;
  - `python scripts/validate.py` — SUCCESS;
  - `cross-repo-control` — SUCCESS.
- Validate Public Runtime Distribution — run `31403074552` — **SUCCESS**.
- Required PR Gate — run `31403074308` — **SUCCESS**.

The current final head's PR-triggered workflows are `action_required`, not executed green runs. This review therefore relies on the exact green functional candidate plus the independently verified three-file documentation/lifecycle-only comparison to the final reviewed head; it does not misstate the final head as exact-head green.

## Preserved safety conclusions

No regression was found in the previously accepted central Effect Lineage semantics:

- exact semantic identity remains revision/stage/task/role/candidate-bound;
- candidate A → B remains fresh exact work but proposal-only while predecessor is unresolved;
- blocked successor has no reservation/external key;
- root/safe successor activation remains one protected CAS plan;
- CAS losers re-read and semantically re-plan;
- `dispatch.launch.authorized` remains launch linearization;
- authorized + current `NOT_LAUNCHED` cannot satisfy `PROVE_NOT_LAUNCHED`;
- lineage-aware claim/authorization re-check the current leaf;
- generation takeover preserves unresolved lineage blocking;
- legacy ambiguity fails closed;
- the amended candidate-head release contract remains enforced;
- Issue #221 real-runtime fault injection remains outside this Feature and no overall v0.3 release-readiness claim is made.

## Gate decision

`code-gate` remains **PENDING**.

A second Developer remediation must address only the remaining MAJOR above. After a new exact runtime candidate and deterministic evidence exist, a fresh independent Code Re-review must verify closure. This Reviewer does not modify implementation code and does not enter Verification QA or Acceptance.