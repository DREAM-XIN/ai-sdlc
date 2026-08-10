# Design Re-review v2 — F-OPERATOR-VERTICAL-LOOP-0001

## Role

Fresh independent Design Reviewer after Architect remediation.

## Verdict

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0

## MAJOR-1 re-review — PASS

`design-v2` closes Operation-profile provenance end to end:

- `vertical-implementation-review-qa/v1` is selected only by trusted runtime composition;
- canonical/client/Feature/Worker input cannot choose or override it;
- trusted Store start planner receives the profile explicitly;
- immutable `operation.started` records it;
- projection rebuild exposes it;
- equivalent active start requires compatible profile;
- profile mismatch fails closed;
- vertical resume requires exact supported profile;
- legacy unprofiled Operations remain status/cancel compatible but are not silently migrated/resumed.

This provides a durable, auditable authorization boundary for `operation.resume` without changing the frozen canonical request schema.

## MAJOR-2 re-review — PASS

`design-v2` removes Worker authority over lifecycle artifact/evidence identity and location:

- Worker result schemas prohibit authoritative URI/path/id/Event/gate fields;
- Worker may provide only bounded logical output labels;
- trusted collector owns bounded materialization path and content hashing;
- collector emits immutable `CollectedOutputReceipt` bound to Operation/generation/profile/dispatch/role/worker/repository/Feature/revision/candidate;
- translators generate lifecycle IDs and register only receipts that satisfy namespace, digest and exact-binding validation;
- missing/digest-mismatched/stale/misbound receipts fail closed.

This is sufficient to keep Worker output evidence-only rather than allowing indirect arbitrary repository/evidence references.

## Requirement Review MINOR-1 — PASS

Operation `DONE` is now explicitly distinct from Feature lifecycle `DONE`. QA PASS may make the bounded vertical Operation terminal while authoritative Feature state remains `acceptance: READY` with `release-gate: PENDING`; Product Acceptance authority remains outside this Feature.

## Other boundaries

- role independence remains trusted-policy based;
- Store UNKNOWN/cancellation/launch/Persist ordering is reused rather than reimplemented inconsistently;
- NEEDS_USER remains a stable Operation stop without fabricating Decision objects;
- complete inbox/Decision/Notification and full lifecycle automation remain out of scope;
- MCP production surface remains read-only.

## Gate recommendation

`design-gate`: **PASS** using `design-v2` as the approved implementation design.
