# Design Review — F-OPERATOR-VERTICAL-LOOP-0001

## Role

Independent Design Reviewer.

## Verdict

**REWORK**

- BLOCKER: 0
- MAJOR: 2
- MINOR: 0

## Review basis

Reviewed approved Requirement v1, Requirement Review MINOR-1, frozen v0.3 Release Spec, current canonical API implementation, current Operation Store model/planners, and existing autonomous Developer/Reviewer/QA authority boundaries.

## MAJOR-1 — vertical Operation profile provenance is not closed

Design requires `operation.started` to carry immutable `operation_profile = vertical-implementation-review-qa/v1` and requires `operation.resume` to reject Operations outside that profile.

Current Store start path does not support this:

- canonical `operation.start` has no client profile selector, which is desirable;
- `OperationStartBackend` currently calls `plan_operation_start()` without any trusted profile parameter;
- `plan_operation_start()` writes only repository/Feature/expected revision to `operation.started`;
- the projection does not currently retain an Operation profile.

Therefore the Design does not yet define how the vertical profile is selected by trusted policy, immutably bound at start, returned/rebuilt from Store state, and prevented from being chosen/overridden by client/Feature/Worker input.

### Required remediation

Define a trusted profile-start composition in which:

1. profile selection comes only from protected/default-branch or trusted runtime configuration;
2. the vertical start backend supplies the trusted profile to an extended Store start planner;
3. `operation.started` immutably records the profile;
4. projection rebuild exposes the profile to trusted runtime;
5. equivalent `operation.start` convergence requires compatible profile identity and fails closed on profile conflict;
6. canonical client payload cannot select/override the profile;
7. legacy Operations without profile remain readable/cancellable but are not vertical-resumable.

Without this, the core `operation.resume` authorization boundary is ambiguous.

## MAJOR-2 — Worker artifact/evidence provenance is not sufficiently bounded

Design defines Worker payload fields `artifacts[]` and `evidence[]` and role translators that may register lifecycle artifacts/evidence, but it does not explicitly prohibit using Worker-supplied ids/URIs/paths as authoritative records.

A Worker Result is untrusted. Allowing the translator to register a Worker-provided repository URI/id would let a Worker reference unrelated existing files, stale evidence, or paths outside the intended collector output namespace while still passing the role payload schema.

### Required remediation

Split Worker recommendation data from trusted collected outputs:

- Worker may describe produced artifacts/evidence using bounded logical labels/metadata only;
- trusted collector owns materialization into a bounded repository/runtime namespace;
- collector computes digest, content identity, media/type and trusted URI/path;
- trusted Result Envelope contains `collected_outputs[]` receipts built by collector, not Worker;
- translators may register only exact collected-output receipts bound to dispatch/operation/role/revision/candidate;
- Worker-supplied artifact/evidence URI/id/path fields must be prohibited by schema;
- collector receipt mismatch, missing output, stale candidate/revision or digest mismatch fails closed.

This boundary must apply to Developer, Reviewer and QA evidence/artifacts.

## Requirement Review MINOR-1

Resolved by Design: Operation DONE is explicitly distinct from Feature lifecycle DONE; QA PASS leaves Feature at Acceptance READY and release-gate PENDING.

## Gate recommendation

`design-gate`: remain **PENDING** until both MAJOR findings are remediated and independently re-reviewed.
