# Code Remediation — F-OPERATOR-OPERATION-STORE-0001

## Role

Implementation Developer remediation for `F-OPERATOR-OPERATION-STORE-0001-CODE-REMEDIATION-1`.

## Source review findings

Independent Code Review identified two MAJOR findings:

1. the prior Git adapter used a local clone ref as the CAS authority, so state could disappear with the runner and concurrent runners did not share one authoritative remote compare-and-set boundary;
2. the prior production composition accepted only an abstract protection verifier while the only concrete repository implementation was static/test-controlled, so positive protection was not proven against the real remote repository policy.

## Remediation delivered

### MAJOR-1 — durable remote CAS

The production Store path now uses a remote state-ref backend rather than local-only `git update-ref` as the durable authority.

The backend:

- fetches the exact configured remote Operator state ref before planning/commit;
- binds each generated Store commit to the exact fetched remote state parent;
- writes the exact state ref without force;
- treats remote non-fast-forward/ref movement as `CasConflict`;
- on conflict, re-fetches the durable remote snapshot and invokes the semantic planner again rather than replaying stale bytes;
- preserves the immutable-path / projection-only replacement checks from the prior implementation.

Deterministic tests use a shared bare Git remote plus independent clones to prove that a write from one checkout survives and is visible from a fresh checkout, while a stale concurrent writer is rejected and must re-plan from the updated remote state.

### MAJOR-2 — concrete remote protection verification

A concrete GitHub branch-protection verifier now checks the configured repository/ref through trusted GitHub repository policy state instead of accepting a client/Worker assertion.

The verifier is repository/ref-bound and fail-closed:

- positive protection evidence is required before returning `PROTECTED`;
- missing/unprotected state returns `UNPROTECTED`;
- API ambiguity/failure returns `UNKNOWN`;
- normal production composition rejects `StaticProtectionVerifier`, which remains test-only;
- the production runtime constructs the remote Store backend only from trusted control configuration and uses the concrete verifier path for semantic writes.

Tests cover positive protection, missing protection, unknown/failed verification, Operator-App restriction checks, binding mismatch, and rejection of the test-only static verifier in the normal production runtime.

## Validation evidence

Exact remediation implementation head: `0a6cd5d19f51aef1ded3c6610740e0fc57cc4ba1`.

GitHub Actions on that exact head:

- Validate AI-SDLC protocol — SUCCESS (`31355460908`);
- Validate Public Runtime Distribution — SUCCESS (`31355460912`);
- Required PR Gate — SUCCESS (`31355460962`).

The Protocol run includes the Store and trusted runtime validators, including the fresh-clone remote durability/CAS scenarios and concrete protection verification tests.

## Scope preservation

The remediation does not add vertical role orchestration, `operation.resume`, Decision/Notification persistence, `operator.inbox` availability, MCP write tools, or Feature Manifest/Gate authority.

## Result

Both Code Review MAJOR findings have implementation remediation and exact-head green regression evidence. A fresh independent Code Re-review is required; this document is not a Code Gate approval.
