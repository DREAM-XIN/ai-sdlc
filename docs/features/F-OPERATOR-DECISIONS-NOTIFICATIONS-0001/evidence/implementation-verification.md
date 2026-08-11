# Implementation Verification Evidence — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

Role: Developer implementation verification only.

Functional candidate: `e2d1d9ea60e34f64df6fdec50f60384f1845bc37` on PR #230.

## Exact functional-head workflow evidence

- Validate AI-SDLC protocol — run `31451664032` — SUCCESS.
- Required PR Gate — run `31451664028` — SUCCESS.
- Validate Public Runtime Distribution — run `31451664049` — SUCCESS.

These runs are bound to exact functional head `e2d1d9ea60e34f64df6fdec50f60384f1845bc37`.

## Authoritative validation coverage

The Protocol path runs `scripts/validate.py`. The Decision/Notification validator is wired into that authoritative aggregate through `validate_operator_api()` and covers durable Decision/Notification/inbox semantics rather than existing only as a standalone optional test.

Observed implementation regressions during development were resolved through the normal exact-head CI loop:

1. Operation projection schema initially rejected the new deterministic Decision/Notification projection fields; `operation-projection.schema.json` was updated to explicitly declare them.
2. A repeated `authorization.expiring` reconcile at a later trusted timestamp initially conflicted with the first immutable Notification record. The planner now treats first durable `created_at` as authoritative for an already-existing exact semantic Notification while continuing to reject any other semantic-record conflict.
3. Cancellation-before-response initially surfaced `POLICY_DENIED` after deterministic projection had already classified the pending Decision as superseded. The response planner now gives the owning Operation's durable `CANCELLED` state precedence for unresolved late responses and returns `CANCELLED_OPERATION`, while equivalent replay of an already-resolved response remains idempotent.

The succeeding exact functional-head runs above demonstrate the accepted Operator Store, API/MCP, Vertical Loop, Effect Lineage, lifecycle, cross-repository, security, and public-runtime aggregate remained regression-green with this Feature implementation.

## Boundary

This evidence does not PASS `code-gate`, `verification-gate`, or `release-gate`; it is not independent Reviewer/QA/Product evidence and does not claim Issue #221, second-adapter, #218, or overall v0.3 release readiness.