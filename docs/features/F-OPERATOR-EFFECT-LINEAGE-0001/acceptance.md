# Product Acceptance — F-OPERATOR-EFFECT-LINEAGE-0001

## Role and accepted state

Role: independent Product / Acceptance owner for `F-OPERATOR-EFFECT-LINEAGE-0001` / Issue #226.

Accepted PR: `#228`

Accepted lifecycle head before this evidence commit:

`e6eb158552815f0d2df18098fe7cac7fd4d0d76f`

Authoritative Feature Manifest at acceptance start:

- revision: `22`;
- current stage: `acceptance`;
- Requirement / Design / Plan / Implementation: DONE;
- Code Review: DONE;
- `code-gate: PASS`;
- Verification: DONE;
- `verification-gate: PASS`;
- Acceptance: READY;
- `release-gate: PENDING`.

Validated executable functional candidate:

`88cfb6e07f70c43597102d2c3d20edded4d6a7d8`

The comparison from that exact green functional candidate to the accepted lifecycle head contains only remediation/review/verification evidence, legal Feature Events, and authoritative Manifest materialization. No runtime source, schema, validator, or release-contract implementation changed after the functional candidate.

## Acceptance verdict

**ACCEPT — 0 BLOCKER / 0 MAJOR / 0 MINOR**

The Feature-level product outcome defined by the approved Requirement and Issue #226 is satisfied. The implementation provides the reviewed deterministic durable Effect Lineage / UNKNOWN-resolution safety layer while preserving the frozen launch, lifecycle and candidate semantics.

## Accepted product outcomes

Acceptance confirms the Feature provides the bounded v0.3 behavior required for this workstream:

- exact `semantic_effect_key` remains revision/stage/task/role/candidate-bound;
- trusted durable Effect Lineage remains stable across revision/candidate/generation/restart continuity for the same causal work;
- unresolved predecessor safety is checked before a fresh external semantic reservation is created;
- blocked successor proposals receive no independent external reservation/key/claim/launch authority;
- protected-state CAS prevents sibling active descendants and requires semantic re-plan after conflicts;
- `dispatch.launch.authorized` remains the launch linearization point;
- durable launch authorization plus current `NOT_LAUNCHED` does not revoke the old launch or authorize a new key;
- bounded Effect Resolution rejects stale Feature/ref/candidate/Operation/policy/evidence state and re-reads current protected resolution policy at application time;
- current policy controls strong-evidence capabilities and evidence source binding;
- legacy unresolved reservations fail closed when unique lineage reconstruction is not provable;
- mixed old/new writers are fenced before lineage-required production execution;
- candidate A→B invalidates stale A Reviewer/QA applicability and creates fresh exact B work/proposal without making B externally launchable while A remains unresolved;
- the machine-readable v0.3 contract rejects the removed `head_change_requires_new_semantic_dispatch` / `new-head-requires-new-semantic-dispatch` semantics;
- Feature Manifest + trusted Feature Event/Persist remain the sole lifecycle authority.

## Verification evidence accepted

Independent Code Re-review v3 concluded **PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR** and independently closed the final protected-policy freshness MAJOR.

Independent Verification QA concluded **PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR**. QA verified that the authoritative Effect Lineage validator executes identity stability, candidate blocking, stale-runner authorized+`NOT_LAUNCHED`, policy-only epoch/digest drift, CAS/generation/rebuild, legacy reconstruction, mixed-writer fencing, executor policy-verifier requirements, schema/bypass rejection, and amended release-contract validation.

For exact functional candidate `88cfb6e07f70c43597102d2c3d20edded4d6a7d8`:

- Validate AI-SDLC protocol — run `31405923598` — SUCCESS;
- Validate Public Runtime Distribution — run `31405925657` — SUCCESS;
- Required PR Gate — run `31405925614` — SUCCESS.

## Explicit release boundary

This Acceptance is intentionally Feature-scoped. It does **not** claim or approve:

- Issue #221 real-runtime release fault injection / dogfood;
- release-level duplicate-effect proof from deterministic tests alone;
- external exactly-once semantics;
- Decision / Authorization / Notification or full `operator.inbox` completion;
- a second materially independent write-capable AI client adapter;
- #218 release-evidence ledger completion;
- VERSION change or `release/v0.3.0.yaml` creation;
- overall v0.3 release readiness.

Those remain separate downstream release blockers/workstreams under the frozen v0.3 Release Spec.

## Gate decision

`release-gate: PASS` is supported for **F-OPERATOR-EFFECT-LINEAGE-0001 only**.

Authorized lifecycle result:

- `acceptance: DONE`;
- `release-gate: PASS`.

After trusted Feature Event/Persist records this decision, PR #228 may proceed through its normal merge checks. Completion of this Feature unblocks the separate #221 real-runtime fault-injection work; it does not satisfy #221 by itself.
