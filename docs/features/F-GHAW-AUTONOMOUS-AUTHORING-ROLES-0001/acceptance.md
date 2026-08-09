# Acceptance Evidence — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Acceptance identity

Role: **independent Product / Acceptance**  
Feature: `F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001`  
Issue: `#204`  
PR: `#207`  
Manifest revision at Acceptance start: `21`  
Frozen production-code candidate: `7a9029ae8f48416c477f32df05ff530ed86891b5`  
Acceptance lifecycle head inspected: `6b51e6ed32f1137a1c36ce2c9a0d9e8264ab0cf2`

This is the manual/trusted Product Acceptance role required by the approved Requirement. It is independent from autonomous Product/requirement authoring and does not derive release authority from any authoring worker result.

## Decision

**ACCEPT / RELEASE-GATE PASS RECOMMENDED**

The Feature goal is met, all sixteen approved acceptance criteria have passing independent evidence, all prerequisite lifecycle Gates are authoritative PASS, and the implementation preserves the required non-goals and trust boundaries.

`release-gate` may PASS only after this Acceptance Evidence-bearing head itself passes the required final PR checks.

## Product goal acceptance

Issue #204 requires trusted gh-aw autonomous execution to extend to the artifact-producing front half of the standard Feature lifecycle while preserving independent review and release authority.

Accepted delivered behavior:

- Product / `requirement` has a bounded autonomous authoring path.
- Architect / `design` has a bounded autonomous authoring/remediation path after the requirement gate.
- Orchestrator / `plan` has a bounded autonomous authoring path after the design gate.
- Existing Developer, independent Code Reviewer, and QA autonomous paths remain compatible.
- Requirement Review, Design Review, Product Acceptance, merge, release, and Gate authority remain independent/trusted.
- Authoring workers cannot choose authoritative provider/profile/model/worker/path/Gate/Event authority from target-controlled input.
- Authored drafts are persisted only through trusted collector + Feature Event/Persist mechanics and are durably bound to exact trusted source-run provenance.

This satisfies the stated user/product outcome without expanding the Feature into autonomous review or release authority.

## Acceptance-criteria sign-off

| AC | Acceptance result | Product evidence |
|---:|---|---|
| 1 | PASS | Exact autonomous Product/requirement, Architect/design, Orchestrator/plan routes exist; Product/acceptance and Requirement/Design Review remain manual. |
| 2 | PASS | Trusted deterministic non-experimental routing is Claude→Copilot, Claude→Copilot, Codex→Copilot for the three authoring roles. |
| 3 | PASS | Six exact authoring role-worker identities are registered with trusted source/compiled-lock identity; unknown identities fail closed. |
| 4 | PASS | Deterministic worker rendering and complete 18-target strict compile matrix pass with pinned `github/gh-aw@v0.83.4`. |
| 5 | PASS | Closed authoring-result contract is separate from generic Developer completion semantics. |
| 6 | PASS | Trusted collector verifies exact Actions run/workflow/task/role/stage/Feature/revision/repository/ref/contract provenance before translation. |
| 7 | PASS | Product completion is bounded to requirement draft + requirement DONE + requirement-review READY; no requirement-gate authority. |
| 8 | PASS | Architect completion is bounded to design draft + design DONE + design-review READY; no design-gate authority. |
| 9 | PASS | Orchestrator completion is bounded to plan draft + plan DONE + implementation READY; no Gate authority. |
| 10 | PASS | Design remediation is task-bound and cannot self-approve Design Review. |
| 11 | PASS | Canonical artifact path mapping is trusted/closed; traversal, unrelated docs, state and .github path attempts are rejected. |
| 12 | PASS | Target-controlled syntax cannot select provider/model/profile/worker/candidate order/experimental opt-in/routing policy or expand artifact paths. |
| 13 | PASS | Bot comment/file alone is insufficient provenance; durable evidence binds the exact validated source run, and wrong/stale/replayed provenance fails closed. |
| 14 | PASS | Existing Developer, Code Reviewer and QA routing/result/security regressions remain green. |
| 15 | PASS | Product Acceptance remains manual; no authoring path can create/PASS release-gate, merge or release state. |
| 16 | PASS | QA Evidence-bearing candidate passed Protocol, Public Runtime Distribution, Required PR Gate and complete strict compile matrix. Acceptance Evidence head must re-pass before final Gate. |

## Independent lifecycle evidence accepted

Authoritative Manifest at revision 21 records:

- `requirement-gate: PASS` with independent Requirement Review evidence;
- `design-gate: PASS` with independent Design Review evidence;
- `code-gate: PASS` with Code Review v2 evidence;
- `verification-gate: PASS` with independent QA evidence;
- `release-gate: PENDING` while Acceptance is WORKING.

Code Review history is intentionally preserved: v1 recorded REWORK due to durable source-run provenance and explicit path-negative coverage; the Developer remediation closed both findings, passed required CI, and independent Code Review v2 passed. Product Acceptance treats that visible remediation history as a positive evidence-quality property rather than erasing the initial failed review.

## Frozen-candidate integrity

A commit comparison from the remediated production candidate `7a9029ae...` through the Acceptance lifecycle head `6b51e6ed...` shows no subsequent production-code changes. The delta is limited to:

- Code Review v2 evidence;
- QA Verification evidence;
- legal lifecycle Feature Events;
- trusted Manifest materialization.

Therefore Product Acceptance is evaluating the same production implementation independently reviewed and verified by QA.

## Required checks accepted

The QA Evidence-bearing head `dd84e3857512c1d70de83b5ce369d41b77881805` passed:

| Required check | Run | Result |
|---|---:|---|
| Validate AI-SDLC protocol | `31323683856` | SUCCESS |
| Validate AI-SDLC gh-aw Worker Compile | `31323683891` | SUCCESS — complete 18-target matrix |
| Required PR Gate | `31323683862` | SUCCESS |
| Validate Public Runtime Distribution | `31323683861` | SUCCESS |

The strict compile matrix uses the pinned gh-aw compiler `v0.83.4`; Protocol includes release-readiness validation in addition to lifecycle, persistence, cross-repository, security, authoring/provenance, adapter, effective-model and runtime-preflight regression checks.

## Non-goal / authority confirmation

Product Acceptance confirms the Feature did **not** add:

- autonomous Requirement Review;
- autonomous Design Review;
- autonomous Product Acceptance or release-gate execution;
- direct Gate writes by authoring workers;
- merge/release authority for authoring workers;
- adaptive retry/circuit breaker or dynamic cost/quality routing;
- provider maturity promotion or a new provider;
- weakened Safe Output, revision, Event/Persist, merge, or release trust boundaries.

Experimental provider profiles may exist in the shared Registry/compile matrix, but the new production authoring routing rules do not opt into them.

## Residual note

No live paid external-provider inference call is claimed by this Acceptance evidence. The approved Feature acceptance criteria require deterministic trusted routing, strict worker compilation, bounded result/provenance/security behavior and final repository CI; those requirements are covered. Live provider availability/entitlement remains runtime readiness, not a release criterion for this Feature.

The Code Review suggestion about an unused `GATE_ROLE_STAGES` import is non-functional and has no product, security, compatibility or release impact.

## Acceptance conclusion

The Feature delivers the Issue #204 goal within its approved scope and preserves independent review/release authority. Product Acceptance finds no unresolved acceptance blocker and recommends `release-gate: PASS` after the Acceptance Evidence-bearing head itself completes all required PR checks successfully. Merge must occur only after trusted Persist records Acceptance DONE / Release Gate PASS and the final PR head satisfies merge/readiness requirements.