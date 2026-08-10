# Implementation — F-OPERATOR-VERTICAL-LOOP-0001

## Status

Implementation complete for the approved `F-OPERATOR-VERTICAL-LOOP-0001` scope at functional candidate:

`dc88354429e1a81468ca78971cc3c51f30c2af62`

PR: `#217`

This document is Developer implementation output only. It is not an independent Code Review, QA verdict, Product Acceptance decision, or v0.3 release-readiness claim.

## Implemented vertical loop

The trusted Operator runtime now implements the approved bounded vertical slice:

`Implementation → independent Code Review → remediation → fresh Re-review → Verification QA → Operation DONE`

Feature Manifest + trusted Feature Event/Persist remain lifecycle authority. The Operation journal remains orchestration state only.

### Trusted runtime composition

- profile-bound `operation.start` uses trusted `vertical-implementation-review-qa/v1` composition and immediately advances to the next stable state;
- canonical clients, Feature files and Workers cannot select or override the Operation profile, Store ref, role policy, collector policy, candidate binding or trusted runtime dependencies;
- `operation.resume` is exposed only for the supported vertical profile and uses bounded restart reconciliation;
- legacy/unprofiled Operations remain compatible with the existing Store status/cancel behavior but are not silently adopted by the vertical profile.

### Worker result authority boundary

Strict Developer/Reviewer/QA result schemas are evidence/recommendation inputs only. Workers cannot author authoritative Feature Events, Manifest patches, gate mutations, trusted context, collector provenance, artifact/evidence IDs, or authoritative repository URIs/paths.

Trusted role-specific translators generate bounded Feature Events from fresh Feature truth. All lifecycle mutation still flows through existing Feature Event + trusted Persist.

### Collected output provenance

Trusted collected-output receipts bind materialized output to:

- Operation id/generation/profile;
- semantic effect key and stable external dispatch key;
- dispatch id and role;
- trusted Worker and collector identities;
- repository, Feature id and exact revision;
- exact candidate head where applicable;
- bounded feature worker-run namespace;
- size and SHA-256 content identity.

The trusted runtime requires a collector content loader and fails closed on missing materialization, namespace escape, digest/size mismatch, stale revision/stage/candidate, or dispatch/role/identity mismatch.

### Reviewer / QA independence

Reviewer and QA independence is enforced from trusted identities reconstructed from accepted durable callback history. The caller cannot pass a weaker role-separation policy into callback processing.

- Reviewer cannot equal the original Developer or remediation Developer identity.
- QA cannot equal Developer, accepted Reviewer, or remediation Developer identities.
- a fresh Reviewer result after remediation is bound to the post-remediation exact candidate head.

### Exact Feature and candidate fencing

Every dispatch/callback/Persist transition is checked against fresh trusted Feature truth and durable Store bindings:

- exact Feature revision;
- current lifecycle stage;
- repository / Feature / target ref;
- role/task identity;
- exact gate-role candidate head;
- durable reservation + launch authorization identity.

Developer work may legitimately produce a new candidate head; its callback is bound to the fresh trusted resulting candidate while the immutable launch reservation continues to represent the exact pre-work launch candidate. Reviewer/QA dispatch and callback remain exact-candidate bound.

### Operation Store safety integration

The vertical loop reuses the completed Operation Store safety substrate rather than introducing a parallel effect model:

- generation-independent semantic-effect reservations;
- stable external dispatch key;
- immutable dispatch claim;
- `dispatch.launch.authorized` as launch linearization;
- cancellation fencing;
- honest `NOT_LAUNCHED / LAUNCHED / UNKNOWN` lookup state;
- Persist requested → linearized → confirmed ordering;
- protected remote Store CAS and semantic re-planning.

Recorded `UNKNOWN` remains a fail-closed BLOCKED state. This Feature does not invent or implement UNKNOWN resolution/effect lineage semantics; that work remains separate under Issue #219.

### Restart recovery

`TrustedRecoveringVerticalExecutor` provides bounded recovery for the approved cases:

- missing launch acknowledgement: lookup by stable external dispatch key, adopt `LAUNCHED`, retry the same key only after `NOT_LAUNCHED`, fail closed on `UNKNOWN`;
- durable callback recorded before local translation completion: reconstruct trusted envelope and continue under trusted role/collector policy;
- translated Feature Event recorded before Persist completion: recover the exact stored Event;
- Persist linearized but local acknowledgement lost: exact Event lookup/confirmation, or exact idempotent Event replay when lookup proves it absent;
- cancellation before launch/Persist linearization fences new side effects; cancellation after Persist linearization permits only the already-linearized exact Event to finish.

The reconciler is bounded by the configured auto-step limit and stable-stops rather than spinning indefinitely.

## Lifecycle result boundaries

The bounded translators preserve role authority:

- Developer completion can move Implementation to DONE / Code Review READY but cannot PASS code-gate;
- Reviewer PASS can PASS code-gate and make Verification READY;
- Reviewer REWORK creates one bounded Developer remediation task;
- remediation completion leads to a fresh Reviewer dispatch on the new exact candidate;
- QA PASS can PASS verification-gate and make Acceptance READY;
- QA PASS cannot complete Product Acceptance or PASS release-gate;
- Operation DONE at the end of this slice is distinct from Feature workflow DONE.

## Deterministic validation

The repository validators cover:

- strict Worker/result and collected-output provenance failures;
- trusted role independence rejection;
- Developer → Reviewer PASS → QA PASS;
- Reviewer REWORK → remediation → fresh Re-review PASS → QA PASS;
- Acceptance READY / release-gate PENDING boundary after QA PASS;
- exact revision/stage/candidate fencing;
- `NOT_LAUNCHED`, adopted `LAUNCHED`, `UNKNOWN`, cancellation and stable-key retry behavior;
- callback durability/replay and misbinding rejection;
- Persist linearization, cancellation ordering and lost-ack reconciliation;
- fresh runtime reconstruction and bounded resume;
- existing Operator Store, canonical API/MCP, lifecycle and repository regression suites.

Exact validation evidence is recorded separately in `evidence/implementation-verification.md`.

## Explicit non-scope

This implementation does not add:

- Effect Lineage / UNKNOWN Resolution protocol semantics from Issue #219;
- release-level real-runtime effect-safety proof or fault injection assigned to #221;
- Decision/Notification persistence;
- complete `operator.inbox`;
- a second AI client adapter;
- full v0.3 dogfood;
- Naming/Benchmark work;
- Product Acceptance authority or overall v0.3 release readiness.
