# Code Review — F-OPERATOR-CANONICAL-API-0001

## Verdict

REWORK

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

The implementation is close to the approved Requirement/Design/Plan boundary, but the canonical `system.capabilities` result is not actually validated against the strict capability-discovery schema. This weakens the typed API contract and can expose unbounded backend availability reasons through discovery/error surfaces. `code-gate` must not PASS until this is corrected and revalidated on the new candidate head.

## Authoritative baseline reviewed

- Feature: `F-OPERATOR-CANONICAL-API-0001`
- Issue: `#208`
- PR: `#209`
- Feature branch: `feature/F-OPERATOR-CANONICAL-API-0001`
- Manifest revision observed before Code Review start: `12`
- implementation: `DONE`
- code-review: `READY`
- code-gate: `PENDING`
- PR head observed at review start: `55458e4a771588f72ee416382408180cbad0d0e9`
- immutable Release Spec baseline: `c1980bba3205062495e49e685f9501a248df8365`

The upstream Release Spec Review was used only as frozen normative context and was not reused as Code Review evidence.

## Material reviewed

- approved Requirement, Design and Plan artifacts
- `docs/features/F-OPERATOR-CANONICAL-API-0001/implementation.md`
- `docs/features/F-OPERATOR-CANONICAL-API-0001/evidence/implementation-verification.md`
- PR #209 changed-file inventory and actual implementation diff
- `scripts/operator_api.py`
- `scripts/validate_operator_api.py`
- `spec/operator/request-envelope.schema.json`
- `spec/operator/error.schema.json`
- `spec/operator/capabilities.schema.json`
- `spec/operator/capabilities/system-capabilities.response.schema.json`
- relevant lifecycle/review rules

## Findings

### MAJOR-1 — `system.capabilities` bypasses the strict capability-discovery schema

The implementation defines a strict reusable `spec/operator/capabilities.schema.json` that requires exactly 12 capability rows, constrains each capability id, requires `available`, and bounds `reason` to the approved reason-code enum.

However, `spec/operator/capabilities/system-capabilities.response.schema.json` does not reference that schema. Its `capabilities` property is only declared as `{"type":"array"}` with no item schema, cardinality, id allowlist, `available` requirement, or reason-code constraint.

`dispatch()` validates backend results only against the per-capability response schema. Therefore an injected/trusted `system.capabilities` backend can return structurally arbitrary capability rows and still pass canonical result validation. In addition, `SystemCapabilitiesBackend` copies `backend.availability(...)[1]` directly into each capability row, while ordinary unavailable errors likewise place the backend-provided reason directly in error details. The implementation's secret-redaction helper only protects raised exceptions; it does not sanitize availability reason strings.

Impact:

1. The public capability-discovery contract is not actually typed as required by Requirement/Design/Plan.
2. The exact 12-capability vocabulary and bounded availability reason model are not enforced at the canonical response boundary.
3. A trusted backend accidentally returning internal/secret-bearing availability text can expose it through `system.capabilities` or `CAPABILITY_UNAVAILABLE` details.
4. `validate_operator_api.py` does not catch this because it validates `capabilities.schema.json` as a standalone schema but does not prove that `system-capabilities.response.schema.json` incorporates it or reject malformed discovery rows/reasons through `dispatch()`.

Required remediation:

- Make `system-capabilities.response.schema.json` reference or equivalently embed the strict `capabilities.schema.json` contract so dispatcher result validation enforces the exact typed discovery shape.
- Ensure backend availability reasons crossing the canonical boundary are restricted to trusted bounded reason codes; unknown/internal/secret-bearing reason text must fail closed or map to a safe bounded reason rather than being serialized verbatim.
- Extend `validate_operator_api.py` with negative tests proving malformed capability rows, unknown ids, wrong cardinality, and unsafe/unknown availability reasons cannot become successful canonical discovery results and cannot leak through unavailable error details.
- Re-run `python scripts/validate_operator_api.py`, Feature Manifest validation, and required PR checks on the exact remediated candidate head.

Severity: MAJOR because this violates the approved typed-contract/security acceptance boundary and deterministic result validation. Under the repository review policy, MAJOR requires REWORK and prevents `code-gate` PASS.

## Other rubric results

- Requirement/Design scope discipline: PASS. No durable Operation Store, release publication, or generic lifecycle authority was added.
- Frozen capability metadata matrix in trusted code: PASS.
- Unsupported-version and unknown-capability ordering: PASS by code inspection and Developer evidence.
- Idempotency/expected-revision matrix: PASS by code inspection.
- Client/trusted identity separation: PASS for this bounded foundation.
- Prohibited generic mutation capability absence: PASS.
- Default availability honesty: PASS in intent (`system.capabilities` only), subject to MAJOR-1 response-boundary correction.
- Exact-head CI: previous implementation-code head had green required checks, but the current lifecycle/evidence head is not treated as substitute exact-head implementation evidence. Remediation must establish fresh exact-head checks.

## Gate recommendation

`code-gate`: FAIL / REWORK.

Create a bounded Developer remediation task for MAJOR-1 against PR #209. Keep independent Code Review authority separate; after remediation is durably completed, a fresh Code Reviewer must re-read the new exact head and re-review before the gate may PASS.

This review does not approve Verification, Acceptance, supported AI-client adapters, Operation Store/recovery, dogfood, publication, or v0.3 release readiness.
