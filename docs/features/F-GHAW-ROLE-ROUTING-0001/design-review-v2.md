# Design Review v2 — F-GHAW-ROLE-ROUTING-0001

Role: independent Design Reviewer

Verdict: **PASS**

Severity summary: 0 BLOCKER / 0 MAJOR / 0 MINOR

## Re-review scope

Re-reviewed Design v2 after `F-GHAW-ROLE-ROUTING-0001-DESIGN-REMEDIATION-1` against the approved Requirement, prior Requirement Review notes, DR-MAJOR-1, Provider Registry fail-closed boundaries, generated readiness surfaces, dispatch authority, and validation strategy.

## DR-MAJOR-1 disposition

Resolved.

The revised Design introduces explicit validated `credential_source` capability metadata with bounded v1 values `secret` and `github-token`. Readiness generation branches on capability rather than profile/provider identity. The Design also constrains alias compatibility (`github-token` forbids aliases in v1), requires unknown sources to fail closed, and adds positive/negative fixtures for both source types.

This removes the previous need for a Copilot-specific trusted readiness branch.

## Requirement Review notes

- RR-MINOR-1: resolved by metadata-driven credential source plus profile-level readiness booleans.
- RR-MINOR-2: resolved by explicit separation of automatic `selection_mode: policy` from `selection_mode: manual-trusted-profile` diagnostics.

## Security / authority assessment

- Target repositories cannot select profile/provider/model/credential/worker/policy/candidate order/experimental opt-in.
- Secret values remain outside Python resolver arguments and audit output.
- Routing is static and non-invasive; it does not claim entitlement.
- Experimental profiles remain excluded from default policy.
- Reviewer/QA routes remain audit-only and do not become autonomous.
- Exact compiled worker allowlisting remains Registry-derived.
- Feature Manifest/Event/Gate, Safe Output, independent review/QA, merge and release authority remain unchanged.

## Testability

The Design specifies deterministic positive and negative coverage for Registry source metadata, policy validation, preferred/fallback resolution, no-ready failure, alias/system-token readiness, target selector boundaries, eight-profile compatibility, and final CI.

## Decision

Design supports the approved Requirement and may PASS `design-gate`.
