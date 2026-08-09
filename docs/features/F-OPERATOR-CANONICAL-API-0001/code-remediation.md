# Code Remediation — F-OPERATOR-CANONICAL-API-0001

## Scope

Developer remediation for `remediation-code-review-v1` on PR #209 only. This does not perform Code Re-review or change `code-gate` authority.

## MAJOR-1 remediation

The failed Code Review found that `system.capabilities` results were not bound to the strict capability-discovery schema and backend-owned availability reason text could cross the canonical boundary without bounded normalization.

The remediation makes the following bounded changes:

1. `spec/operator/capabilities/system-capabilities.response.schema.json` now references the canonical `capabilities.schema.json` contract directly.
2. `capabilities.schema.json` enforces exactly 12 rows and requires each canonical capability id to occur exactly once, in addition to the existing typed `available` field and bounded reason enum.
3. `scripts/operator_api.py` normalizes availability reasons to the public bounded vocabulary. Unknown/internal reason text is mapped fail-closed to `BACKEND_NOT_CONFIGURED`; available backends normalize to `AVAILABLE`.
4. `scripts/validate_operator_api.py` adds negative coverage for wrong discovery cardinality, unknown capability ids, duplicate/missing canonical ids, missing required row fields, unsafe discovery reasons, and unsafe unavailable-backend reason leakage.
5. `scripts/validate.py` invokes `validate_operator_api()` so the canonical Operator validation suite is part of the Required PR Gate rather than optional/manual-only evidence.

## Exact remediation candidate

Code/test candidate head validated before lifecycle-only remediation Evidence commits:

`14aeba5509c6526a48e341b2421cdad65626c15e`

GitHub Actions on that exact head:

- Required PR Gate — run `31335897022` — SUCCESS
- Validate AI-SDLC protocol — run `31335896975` — SUCCESS
- Validate Public Runtime Distribution — run `31335896984` — SUCCESS

Within Required PR Gate, `protocol-validation` completed successfully after `scripts/validate.py` was changed to execute `validate_operator_api()`. This makes the remediation-specific canonical validation part of the exact-head required suite.

## Security/result

Malformed capability discovery cannot pass as a successful canonical result, and unsafe backend availability text is no longer serialized verbatim into either `system.capabilities` rows or `CAPABILITY_UNAVAILABLE` details.

## Remaining authority boundary

This Developer evidence does not approve the implementation. `code-gate` remains failed until a fresh independent Code Reviewer re-reads PR #209 and the remediated candidate, verifies MAJOR-1 closure, and produces new review Evidence through trusted Persist.

No supported AI-client adapter, durable Operation Store/recovery, Decision/Notification persistence, dogfood, publication, VERSION change, or v0.3 release-readiness blocker is claimed complete by this remediation.
