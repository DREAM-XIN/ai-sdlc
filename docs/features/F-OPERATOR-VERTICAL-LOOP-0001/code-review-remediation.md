# Code Review Remediation — F-OPERATOR-VERTICAL-LOOP-0001

## Role and scope

Role: independent Implementation / Remediation Developer responding to the existing independent Code Review in `code-review.md` and PR Review `4894292495`.

This remediation addresses exactly the two MAJOR findings from that review. It does not perform Code Re-review, does not PASS `code-gate`, and does not enter QA.

Validated remediation functional candidate:

`c7b48b931c0ef99e43975391381f073dfa1eb381`

PR: `#217`

## MAJOR-1 — parallel callback ingress

Closed within the approved Design boundary.

- `TrustedVerticalExecutor.handle_worker_callback(...)` is now a non-authoritative compatibility trap that always fails with `CAPABILITY_UNAVAILABLE`; it can no longer translate or Persist lifecycle state.
- Production callback-to-lifecycle translation remains exclusively behind `TrustedVerticalCallbackCoordinator`.
- The coordinator still records callbacks through `plan_vertical_callback_record(...)`, which validates the exact durable semantic reservation and `dispatch.launch.authorized` binding before the callback becomes durable.
- Role independence is reconstructed by `derive_role_independence_policy(...)` from accepted durable callback history; no caller-supplied policy can authorize a Reviewer or QA result through the production callback path.
- Production construction and callback processing require a callable trusted collector content loader.
- Deterministic adversarial coverage proves:
  - direct executor callback invocation cannot drive lifecycle translation;
  - a callback without a durable reservation/launch authorization is rejected;
  - a missing collector loader cannot construct the production callback coordinator;
  - same-size materialized bytes with a different SHA-256 digest are rejected.

No new callback/effect protocol was introduced.

## MAJOR-2 — repeated REWORK lineage

Closed by reconstructing durable ordered identity lineage instead of overwriteable scalar authorization state.

- `derive_role_independence_policy(...)` now walks accepted `worker.result.validated` callback facts in Operation journal order.
- It reconstructs all unique Developer/remediation Developer identities contributing to the candidate lineage and all accepted Reviewer identities.
- Fresh Reviewer authorization excludes every candidate-contributor identity, including earlier remediation Developers from prior REWORK rounds.
- QA authorization excludes the complete candidate-contributor lineage plus the accepted Reviewer lineage.
- Compatibility scalar fields remain read-only projections for existing callers; they are not the authorization set.
- Fresh re-review predecessor selection no longer sorts content-hashed remediation task ids. It uses the authoritative remediation task order materialized by Feature lifecycle Persist and selects the last completed code-review remediation task.
- Deterministic two-REWORK coverage proves:
  1. the first remediation Developer cannot later satisfy Reviewer or QA independence;
  2. a deliberately lexically misleading older task id cannot replace the actual latest remediation predecessor;
  3. rebuilding from a copied/restarted durable Store and Feature Manifest yields the same identity lineage and re-review predecessor.

## Deterministic validation

The focused validator `scripts/validate_operator_vertical_remediation.py` is wired into `scripts/validate.py`.

Exact remediation candidate CI:

- Validate AI-SDLC protocol — run `31367583591` — **SUCCESS**.
  - `python scripts/validate.py` — SUCCESS.
  - `cross-repo-control` — SUCCESS.
- Validate Public Runtime Distribution — run `31367583602` — **SUCCESS**.
- Required PR Gate — run `31367583576` — **SUCCESS**.

## Preserved boundaries

This remediation intentionally does not absorb:

- Issue #219 Effect Lineage / UNKNOWN Resolution semantics;
- Issue #221 real-runtime fault injection / release-level effect-safety proof;
- Decision/Notification persistence or complete `operator.inbox`;
- a second adapter;
- Naming/Benchmark;
- Product Acceptance or overall v0.3 release readiness.

`UNKNOWN`, cancellation, stable external dispatch identity and Persist linearization continue to use the existing Operation Store semantics unchanged.

## Developer conclusion

Both MAJOR findings have a bounded remediation implementation and deterministic exact-candidate evidence at `c7b48b931c0ef99e43975391381f073dfa1eb381`.

The only authorized next review action after lifecycle recording is a **fresh independent Code Re-review** of the resulting exact PR head. `code-gate` remains PENDING.