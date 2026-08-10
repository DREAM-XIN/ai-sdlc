# Requirement Review — F-OPERATOR-EFFECT-LINEAGE-0001

## Role

Independent Requirement Reviewer.

## Reviewed candidate

PR #228 requirement candidate at exact head `f68141852066ef5a60327318caf21cea5a7b152b`.

The only change after the author Requirement candidate is the reviewer-owned `requirement-review` START Event; the Requirement content itself is unchanged from the prior author head.

## Verdict

**PASS_WITH_NOTES**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1

## Review basis

Reviewed against:

- Issue #226 Feature scope;
- protected `main` baseline `5f37ffd6d9c74a2e350ec369d467ae2026d1753b`;
- frozen `docs/v0.3-release-spec.md` after Effect Lineage amendment PR #225;
- `release/v0.3.0-draft.yaml` amended machine-readable contract;
- protocol Issue #219;
- reviewed amendment source PR #224 exact head `e809f587ce3c52cb18468a49700c8eb07b9123bd` and Amendment Re-review `4896122306`;
- completed Operation Store Feature #214 / PR #215;
- completed Vertical Loop Feature #216 / PR #217;
- current PR #228 exact diff and current authoritative Feature Manifest;
- exact-head CI on the author Requirement candidate: `Validate AI-SDLC protocol` run `31387306192` SUCCESS and `Required PR Gate` run `31387306380` SUCCESS.

Green CI is supporting evidence only and is not the reason for Gate PASS.

## Findings

### Frozen protocol consumption and scope — PASS

The Requirement correctly treats the frozen Release Spec as authoritative upstream rather than re-authoring #219. It keeps #221 real-runtime fault injection, #220 alternate Store backends, #218 release-ledger work and overall v0.3 release authorization out of this Feature.

### Exact semantic identity vs Effect Lineage identity — PASS

The Requirement preserves exact `semantic_effect_key` binding while adding a trusted durable causal lineage above it. Revision, candidate, generation, Operation/session, restart, cancellation and supersession cannot manufacture a fresh lineage for overlapping causal work.

### Pre-reservation lineage gate — PASS

The Requirement places the lineage safety check before creation of a new exact semantic reservation. An unresolved predecessor produces/reuses an immutable successor proposal with no independent external key, claim, launch authorization or dispatch. This closes the cross-revision/candidate escape hatch identified by #219.

### Launch linearization / NOT_LAUNCHED semantics — PASS

`dispatch.launch.authorized` remains the launch linearization point. The Requirement distinguishes never-authorized predecessors from already-authorized predecessors and correctly states that current `NOT_LAUNCHED` is observation, not revocation. Same-key recovery or `BLOCKED` remains mandatory unless stronger trusted no-duplicate proof exists.

### Bounded predecessor resolution — PASS

The allowed outcomes match the frozen amendment: `CORRELATE_EXISTING_RECEIPT`, `PROVE_NOT_LAUNCHED`, `RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN`, and `REMAIN_BLOCKED`. Generic FORCE/IGNORE/new-key bypasses are explicitly excluded. Resolution is exact state/policy/evidence bound and does not dispatch by itself.

### Candidate A→B semantics — PASS

The Requirement cleanly separates stale evidence/work applicability from external launch eligibility. A→B invalidates A Reviewer/QA evidence and requires fresh exact candidate-B work/proposal, while a same-lineage unresolved predecessor still blocks B from obtaining an external reservation/key/dispatch.

This is consistent with the frozen amendment replacing the legacy `head_change_requires_new_semantic_dispatch` / `new-head-requires-new-semantic-dispatch` contract.

### Concurrency, rebuildability and migration — PASS

Protected-state CAS, no sibling active descendants, deterministic projection rebuild, legacy unresolved fail-closed behavior and mixed-writer fencing are all explicit and testable requirements.

### Authority boundaries — PASS

Effect Lineage remains orchestration safety state, not Feature lifecycle truth. Feature Manifest + trusted Feature Event/Persist remain authoritative. Workers, callbacks, Feature branches and ordinary AI clients cannot choose lineage identity, grant resolution authority or directly mutate authoritative lineage state.

### Verification and release boundary — PASS

The deterministic test matrix covers the frozen adversarial cases, including the stale-runner `dispatch.launch.authorized(K0) → NOT_LAUNCHED → K1 proposal` race. The Requirement explicitly prevents those deterministic tests from being represented as #221 real-runtime release evidence.

## MINOR-1 — revocation exception remains protocol-amendment-gated

Requirement §4 says this Feature SHALL NOT introduce a generic post-`dispatch.launch.authorized` revocation primitive unless a later independent Design review proves one is required and fully defines its ordering/fencing semantics.

Read together with Requirement §2, this does not currently authorize a protocol change: §2 requires any implementation choice that contradicts the frozen Release Spec to fail closed and go through a separately reviewed protocol amendment.

Design MUST preserve that stronger interpretation explicitly:

- this Feature's Design Review cannot by itself authorize a new post-authorization revocation/fencing protocol;
- if implementation appears to require such a primitive, work stops at the existing frozen same-key/BLOCKED semantics;
- a separate protocol amendment/review/freeze must first define and approve ordering against `dispatch.launch.authorized`, stale runners, cancellation/supersession, gateway admission and successor activation;
- only after that upstream amendment may this Feature consume the new frozen contract.

This is a Design carry-forward note and does not require Requirement rework.

## Gate recommendation

`requirement-gate`: **PASS** with MINOR-1 carried as a mandatory Design constraint.

On Gate PASS, `requirement-v1` may be approved, `requirement-review` may become DONE, and `design` may become READY. This review does not approve any Design or Implementation choice.
