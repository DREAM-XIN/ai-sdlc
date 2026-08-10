# Code Review Remediation v2 — F-OPERATOR-EFFECT-LINEAGE-0001

## Role and scope

Role: independent Implementation / Remediation Developer responding only to the remaining MAJOR from independent Code Re-review v2 / PR Review `4898427815`.

This remediation does not re-review itself, does not PASS `code-gate`, does not enter Verification/QA, and does not claim Issue #221 real-runtime fault injection or overall v0.3 release readiness.

Validated remediation functional candidate:

`88cfb6e07f70c43597102d2c3d20edded4d6a7d8`

PR: `#228`

## Remaining MAJOR — current protected Effect Resolution policy freshness

Closed by removing stale caller-owned Authority/evidence state from the public resolution application boundary.

### Application-time current policy source

`plan_effect_resolution(...)` no longer accepts `EffectResolutionAuthority` or `TrustedEffectEvidenceVerifier` as current authority-bearing planner arguments.

It requires `ProtectedEffectResolutionPolicyVerifier`, which is bound by trusted composition to exact:

- repository;
- protected Operator state ref;
- reviewed vertical Operation profile;
- protected/default-branch/installation policy loader;
- trusted evidence-fact loader.

Every resolution application calls `verify_current()` before evaluating choice, resolver identity, evidence, predecessor retirement or successor activation.

The current protected policy is re-derived and validates:

- policy schema;
- repository/state-ref/profile binding;
- trusted control-state `policy_ref`;
- explicit `policy_epoch`;
- canonical `policy_digest`;
- bounded frozen resolution choices;
- trusted resolver identities;
- trusted vertical profile digest;
- currently authorized strong-evidence types;
- currently selected evidence source id/digest.

A stale `EffectResolutionAuthority` object can no longer be supplied to `plan_effect_resolution(...)` as proof of current policy.

### Proposal-to-resolution policy binding

The existing immutable proposal `trusted_profile_digest` field is now a combined trusted profile/policy binding over:

- Operation profile;
- reviewed trusted profile digest;
- current resolution-policy epoch;
- current canonical resolution-policy digest.

`TrustedVerticalExecutor` requires a `ProtectedEffectResolutionPolicyVerifier` whenever `effect_lineage_required` is active. Immediately before lineage-gated reservation/proposal planning it re-reads the current policy and places the resulting combined digest into the immutable proposal identity.

At resolution application, the policy verifier is read again. If the current protected policy/profile binding differs from the proposal binding, application fails `STALE_RESOLUTION` before predecessor retirement or successor reservation/member creation.

Production `build_trusted_vertical_runtime(...)` now requires the same verifier, verifies that it is bound to the runtime repository/state-ref/profile, performs a fail-closed construction-time policy read, and passes the verifier to the executor. Proposal planning still performs another fresh read rather than relying on the construction-time result.

### Strong-evidence capability freshness

Strong evidence is no longer authorized by an independently passed verifier object.

The evidence verifier used by each resolution is constructed from the policy returned by that exact application-time `verify_current()` call. Therefore `EXTERNAL_KEY_INVALIDATED` / `NON_OVERLAPPING_SCOPE` capability selection and trusted evidence-source identity/digest come from the current protected policy epoch.

If the current policy does not authorize a strong evidence type, the resolution remains fail-closed with `INSUFFICIENT_EVIDENCE`.

## Deterministic adversarial coverage

The focused Effect Lineage validator remains available through the stable entry point:

`scripts/validate_operator_effect_lineage.py`

which now routes to the current policy-freshness suite in:

`scripts/validate_operator_effect_lineage_v2.py`.

The suite preserves the prior candidate/stale-runner/CAS/migration/mixed-writer/release-contract cases and adds the exact Re-review v2 adversarial case:

1. candidate, Feature revision, target ref and Operation/profile remain unchanged;
2. a successor proposal is created under protected resolution policy epoch 1;
3. an exact resolution id is computed under epoch 1;
4. only the current protected resolution policy changes to epoch 2, producing a different canonical `policy_digest`;
5. the same `ProtectedEffectResolutionPolicyVerifier` re-reads epoch 2 at application time;
6. the old proposal/resolution is rejected with `STALE_RESOLUTION`;
7. no successor reservation/member/key is created from the stale policy state.

The suite also preserves negative coverage for candidate drift, target-ref drift, trusted profile drift, fabricated strong evidence, authorized + `NOT_LAUNCHED`, CAS re-plan, legacy ambiguity/restart reconstruction and mixed-writer fencing.

It additionally proves a lineage-required `TrustedVerticalExecutor` cannot be constructed without the current protected resolution-policy verifier.

## Exact remediation-candidate validation

Exact functional candidate `88cfb6e07f70c43597102d2c3d20edded4d6a7d8`:

- **Validate AI-SDLC protocol** — run `31405923598` — **SUCCESS**.
  - `python scripts/validate.py` — SUCCESS.
  - `cross-repo-control` — SUCCESS.
- **Validate Public Runtime Distribution** — run `31405925657` — **SUCCESS**.
- **Required PR Gate** — run `31405925614` — **SUCCESS**.

The Protocol `validate` job executed the authoritative `scripts/validate.py`, including the Effect Lineage policy-freshness suite.

## Preserved boundaries

This remediation intentionally preserves the previously accepted semantics:

- exact semantic effect identity remains revision/stage/task/role/candidate-bound;
- causal Effect Lineage remains stable across candidate/revision/generation changes;
- candidate A → B produces fresh exact work but proposal-only state while predecessor is unresolved;
- blocked successors receive no external reservation/key;
- root/safe successor activation remains protected CAS-atomic;
- CAS losers re-read/re-plan;
- `dispatch.launch.authorized` remains launch linearization;
- authorized + current `NOT_LAUNCHED` cannot satisfy `PROVE_NOT_LAUNCHED`;
- no new generic post-authorization revocation primitive exists;
- legacy migration still reconstructs from durable Store/Feature/profile facts and fails closed on ambiguity;
- protected mixed-writer rollout/fence remains enforced;
- amended candidate-head Release Spec contract remains enforced;
- Issue #221 remains separate release-level fault-injection work.

## Developer conclusion

The sole remaining MAJOR from Review `4898427815` has a bounded remediation implementation and exact-candidate deterministic evidence at `88cfb6e07f70c43597102d2c3d20edded4d6a7d8`.

The only authorized next review action after legal remediation lifecycle recording is a fresh independent Code Re-review. `code-gate` remains PENDING.
