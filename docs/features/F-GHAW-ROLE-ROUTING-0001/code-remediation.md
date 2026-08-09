# Code Remediation Evidence — F-GHAW-ROLE-ROUTING-0001

Task: `F-GHAW-ROLE-ROUTING-0001-CODE-REMEDIATION-1`

Source review: `docs/features/F-GHAW-ROLE-ROUTING-0001/code-review.md`

Remediation code candidate: `cdc0beb3fbe48fa846163631e0894900fce60e72`

## Scope

Addressed only CR-MAJOR-1: preferred-selection routing audit did not explicitly retain the complete ordered policy candidate list.

No routing policy, candidate selection, credential readiness, provider maturity, worker allowlisting, target command boundary, autonomous-role eligibility, live failure semantics, Gate authority, or merge/release authority was changed.

## Change

`RoutingResolution` now retains `candidate_order` separately from evaluated `decisions`.

For Developer / implementation preferred selection, audit output now contains both:

```json
"candidate_order": ["codex", "copilot"],
"candidates": [
  {"profile":"codex","ready":true,"reason":"SELECTED"}
]
```

This records the complete policy order while truthfully representing that the tail fallback candidate was not readiness-evaluated after a successful preferred selection.

Fallback selection retains the same complete order and records both evaluated decisions:

```json
"candidate_order": ["codex", "copilot"],
"candidates": [
  {"profile":"codex","ready":false,"reason":"MISSING_CREDENTIAL"},
  {"profile":"copilot","ready":true,"reason":"SELECTED"}
]
```

No-ready routing remains fail closed.

## Regression coverage

`validate_gh_aw_profile_routing.py` now explicitly proves:

- preferred Codex selection records full `[codex, copilot]` candidate order;
- preferred selection does not invent a decision for an unevaluated fallback candidate;
- fallback selection records the same full order and deterministic skip/selection decisions;
- no-ready failure remains fail closed;
- incomplete readiness maps remain fail closed;
- audit output still contains no credential identity fixture and `entitlement_verified` remains false;
- no provider/profile-name-specific branch was added.

The generic provider/profile literal-branch guard continues to cover the routing module.

## CI evidence

Remediation code candidate `cdc0beb3fbe48fa846163631e0894900fce60e72`:

- Validate AI-SDLC protocol — SUCCESS — run `31314025114`.
- Validate Public Runtime Distribution — SUCCESS — run `31314025096`.
- Validate AI-SDLC gh-aw Worker Compile — SUCCESS — run `31314025210`.
- Required PR Gate — SUCCESS — run `31314025087`.

## Developer boundary

This evidence marks the bounded remediation implementation as complete only. It does not close the independent Code Review, approve `implementation-v1`, or pass `code-gate`; those require a fresh independent Reviewer decision.
