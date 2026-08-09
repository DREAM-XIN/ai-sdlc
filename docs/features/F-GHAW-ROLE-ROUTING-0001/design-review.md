# Design Review — F-GHAW-ROLE-ROUTING-0001

Role: independent Design Reviewer

Verdict: **REWORK**

Severity summary: 0 BLOCKER / 1 MAJOR / 0 MINOR

## Scope

Reviewed the Design against the approved Requirement, Requirement Review notes, current Provider Registry metadata model, generated credential-presence surfaces, static preflight contract, dispatch boundaries, and lifecycle authority constraints.

## Positive findings

- Policy schema and deterministic candidate ordering are appropriately bounded.
- Static-readiness fallback is correctly separated from runtime/inference retry.
- Reviewer/QA routes remain audit-only and do not expand autonomous-role authority.
- Manual trusted profile dispatch is explicitly separated from normal policy routing.
- Audit output is non-secret and sufficiently deterministic.
- Fail-closed cases and compatibility requirements are testable.
- The Design preserves exact Registry worker identity and lifecycle/Gate authority boundaries.

## DR-MAJOR-1 — credential source is not represented in trusted metadata

The Design resolves Requirement Review MINOR-1 by saying profile readiness is derived generically from Registry primary/alias credential identities, while system-provided credentials are handled by trusted workflow wiring before Python.

That is not yet a provider-neutral implementable contract.

Current Registry metadata identifies credential **names** (`credential`, `credential_aliases`) but does not identify credential **source semantics**. Current generated credential-presence code treats Registry credential identities as repository secrets. Copilot is different: its runtime credential is a trusted GitHub runtime token rather than an ordinary repository secret.

Without an explicit metadata capability, implementation would need a trusted special case equivalent to:

`if profile == "copilot": use github.token else use secrets.<credential>`

Even if placed in YAML rendering/workflow glue rather than the resolver itself, that recreates profile-name-specific trusted routing/readiness behavior and undermines the goal of a reusable routing layer.

### Required remediation

Architect must revise the Design to introduce a validated credential-source abstraction that remains metadata-driven. A suitable direction is a generic Registry/runtime credential-source field such as:

- `secret` — readiness derived from repository secret presence for primary/approved aliases;
- `github-token` — readiness derived from the trusted GitHub runtime token presence.

The exact field placement/name is architectural, but the revised Design must ensure:

1. source semantics are validated, bounded, and fail closed;
2. no profile/provider-name branch is required to choose the source;
3. aliases are only valid where the selected source semantics permit them;
4. generated workflow readiness surfaces derive source expressions from metadata/capability, not profile identity;
5. secret values still never enter Python;
6. existing eight-profile behavior remains backward compatible;
7. positive/negative tests prove `secret` and `github-token` sources and reject unsupported source values/combinations.

## Requirement Review notes disposition

- RR-MINOR-2 (manual trusted dispatch boundary) is satisfactorily resolved.
- RR-MINOR-1 (credential readiness semantics) is **not fully resolved** until DR-MAJOR-1 is addressed.

## Decision

Design Gate must remain PENDING. Record REWORK and create a bounded Architect remediation task for DR-MAJOR-1. Reviewer must not redesign or implement the fix directly.
