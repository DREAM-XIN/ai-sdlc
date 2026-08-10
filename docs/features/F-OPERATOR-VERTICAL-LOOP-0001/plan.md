# Plan — F-OPERATOR-VERTICAL-LOOP-0001

## Goal

Implement approved `design-v2` as a bounded durable vertical loop without changing Feature authority or expanding later v0.3 workstreams.

## WU1 — Operation Store profile and stable-stop extensions

Modify Store model/planners/backend composition to support:

- trusted optional `operation_profile` on `operation.started`;
- profile in deterministic projection;
- same-profile active-start convergence and profile-conflict rejection;
- `NEEDS_USER` status and `operation.needs-user`;
- bounded vertical audit facts accepted by reducer;
- backwards compatibility for legacy unprofiled Operations.

Tests:

- trusted profile immutability/rebuild;
- canonical request cannot inject profile;
- conflicting profile start fails closed;
- legacy profile remains status/cancel compatible but not vertical resumable;
- NEEDS_USER stable stop.

## WU2 — Strict Worker Result and trusted collected-output receipts

Add JSON schemas and validation for Developer/Reviewer/QA Worker payloads plus `CollectedOutputReceipt`.

Implement:

- `additionalProperties: false` role payloads;
- explicit rejection of Event/Manifest/gate/URI/path/id authority fields;
- trusted receipt namespace/digest/context binding validation;
- translator-generated lifecycle artifact/evidence IDs.

Tests cover forbidden payload fields, namespace traversal, digest mismatch, stale revision/candidate, dispatch/role/identity mismatch and valid collected output.

## WU3 — Role independence and bounded translators

Implement trusted `RoleIndependencePolicy` and deterministic translators:

- Developer completion/remediation: no gate PASS;
- Reviewer PASS: exact code review evidence → code-gate PASS → verification READY;
- Reviewer REWORK: exactly one bounded Developer remediation task;
- QA PASS: verification-gate PASS → acceptance READY only;
- unsupported QA REWORK transition fails closed rather than inventing lifecycle authority.

Tests explicitly prove Operation DONE never changes Feature release-gate/Acceptance authority.

## WU4 — Pure vertical-loop planner/controller

Implement deterministic next-step derivation from fresh `FeatureSnapshot` + Store projection:

- IMPLEMENTATION_WORK;
- CODE_REVIEW;
- CODE_REMEDIATION;
- CODE_REREVIEW;
- VERIFICATION_QA;
- DONE/BLOCKED/NEEDS_USER/CANCELLED stable stops.

Generate deterministic semantic task identities and reuse existing Store reservation/claim/launch authorization primitives.

Tests cover happy/rework paths, stale candidate/revision/stage and restart reconstruction.

## WU5 — Trusted callback/Persist coordinator and canonical resume

Implement trusted interfaces/fixtures for:

- role dispatch `launch/lookup`;
- collector callback normalization;
- Feature truth inspection/Persist/event lookup;
- exact Persist requested/linearized/confirmed flow;
- lost launch/callback/Persist acknowledgement reconciliation;
- automatic callback-driven `advance()`;
- profile-bound canonical `operation.resume` backend.

No direct Manifest writer is introduced.

Tests cover NOT_LAUNCHED/LAUNCHED/UNKNOWN, duplicate/conflicting callbacks, cancellation before/after launch/Persist, generation takeover and CAS re-plan.

## WU6 — Conformance and repository regression integration

Create `scripts/validate_operator_vertical_loop.py` and wire it into `scripts/validate.py`.

Required scenario matrix:

1. Developer → Reviewer PASS → QA PASS;
2. Reviewer REWORK → remediation → fresh Reviewer PASS → QA PASS;
3. Operation DONE while Feature remains Acceptance READY/release-gate PENDING;
4. reviewer/QA independence rejection;
5. arbitrary Worker Event/proposed-events/URI/path/id payload rejection;
6. collector receipt provenance failures;
7. stale launch/Persist binding;
8. duplicate/lost callback ack;
9. launch lookup NOT_LAUNCHED/LAUNCHED/UNKNOWN;
10. UNKNOWN takeover inheritance;
11. cancellation ordering;
12. Store CAS re-plan;
13. lost Persist acknowledgement exact reconciliation;
14. fresh runtime reconstruction;
15. unsupported profile resume;
16. capability honesty: inbox/Decision/Notification unavailable and MCP still read-only;
17. all existing validators.

## Dependency order

`WU1 → WU2 → WU3 → WU4 → WU5 → WU6`

WU2 may be developed in parallel with WU1 only if it does not consume unapproved Store structures; final translator/controller integration follows the strict order above.

## Completion evidence

Implementation completion requires an exact runtime candidate head where:

- `python scripts/validate.py` succeeds;
- Protocol workflow succeeds;
- Required PR Gate succeeds;
- Public Runtime succeeds;
- Implementation Evidence pins exact candidate SHA and documents all bounded non-scope.

Developer completion does not PASS code-gate; fresh independent Code Review follows.
