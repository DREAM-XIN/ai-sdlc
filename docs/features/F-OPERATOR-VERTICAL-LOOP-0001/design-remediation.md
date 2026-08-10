# Design Remediation — F-OPERATOR-VERTICAL-LOOP-0001

## Role

Architect remediation for `F-OPERATOR-VERTICAL-LOOP-0001-DESIGN-REMEDIATION-1`.

## Outcome

**DONE** — `design-v2.md` supersedes the draft design for implementation review.

## MAJOR-1 closure — trusted Operation profile provenance

Design v2 makes the vertical profile a trusted runtime-composition value, not canonical/client/Feature/Worker input. The Store start planner receives the profile only from the trusted backend, persists it immutably in `operation.started`, rebuilds it into projection, includes it in active-start compatibility, and requires exact `vertical-implementation-review-qa/v1` before vertical resume. Legacy unprofiled Operations remain readable/cancellable but cannot be silently adopted by vertical resume.

## MAJOR-2 closure — trusted output provenance

Design v2 removes authoritative URI/id/path choice from Worker Result payloads. Workers may return logical output labels only. A trusted collector chooses the bounded materialization namespace, computes digest/content identity, and emits immutable `CollectedOutputReceipt` records bound to Operation/generation/profile/dispatch/role/worker/repository/Feature/revision/candidate. Translators may register only validated collector receipts and generate lifecycle record IDs themselves. Missing bytes, digest mismatch, stale binding or namespace mismatch fails closed.

## Additional retained safety properties

- QA PASS leaves Feature at Acceptance READY / release-gate PENDING while the vertical Operation may become DONE.
- Worker arbitrary Event/Manifest/gate payloads remain prohibited.
- Reviewer/QA independence is based only on trusted identities.
- existing Operation Store launch/Persist/cancel/UNKNOWN semantics remain normative.
- `NEEDS_USER` is an honest stable Operation stop only; no Decision object is fabricated.

## Re-review request

Fresh Design Re-review should verify the two MAJOR findings against `design-v2.md`; no waiver is requested.
