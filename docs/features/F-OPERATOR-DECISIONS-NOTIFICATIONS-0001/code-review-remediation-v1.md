# Code Review Remediation v1 — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## Role and candidate

Role: Implementation Developer / Remediation Developer.

Source independent Code Review: PR Review `4902455599` — `REWORK — 0 BLOCKER / 1 MAJOR / 1 MINOR`.

Remediation functional candidate: `72cc8cd0fef06923d34cfb3b3b566965ba544eef`.

## MAJOR-1 closure — separate control policy source from target repository scope

`ProtectedDecisionPolicyVerifier` now keeps `repository` as the protected control/Store repository binding for policy source, state-ref and operation-profile authority. Target Feature repository is evaluated separately against trusted protected policy scope.

Current behavior:

- protected policy/source/state-ref remain bound to the configured control repository;
- optional `allowed_target_repositories` is a protected exact allowlist;
- if that field is absent, legacy same-repository installations authorize only the control repository itself, so omission never grants new cross-repository authority;
- cross-repository use requires an explicit protected allowlist entry;
- Feature restriction lookup receives the exact trusted target repository, Feature id and target ref;
- effective Decision policy digest includes exact target repository + Feature/ref + restriction/choice/responder/TTL material;
- unauthorized targets fail `POLICY_DENIED` before Decision authority is produced.

The authoritative `validate_operator_decision_takeover.py` now includes a control-repository != target-repository positive case, an unauthorized-target negative case, target-repository restriction-loader binding, and legacy omitted-allowlist fail-closed behavior.

## MINOR-1 closure — explicit stale-generation journal fence

The trusted Store `_append_event(...)` boundary now checks current Operation generation before constructing any non-bootstrap/generation-start journal mutation. `operation.started` and `operation.generation.started` remain the only intentional exceptions needed by the accepted creation/takeover protocol.

As a result, a resolved Decision from generation G cannot append `decision.authorization-consumed` after trusted takeover to G+1: the planner fails with `SUPERSEDED_GENERATION` before a mutation is constructed. This strengthens other journal callers against missed stale-generation checks while preserving the accepted two-step `operation.superseded(G) -> operation.generation.started(G+1)` takeover sequence.

The authoritative remediation validator proves resolved Decision -> takeover -> authorization consumption yields `SUPERSEDED_GENERATION` and does not mutate the input Store snapshot.

## Exact-head validation

Remediation candidate `72cc8cd0fef06923d34cfb3b3b566965ba544eef`:

- Validate AI-SDLC protocol run `31452391877` — SUCCESS;
- Validate Public Runtime Distribution run `31452391924` — SUCCESS;
- Required PR Gate run `31452391893` — SUCCESS.

Protocol validation also completed the existing cross-repository control-plane job successfully.

## Boundary

This evidence closes only Code Review `4902455599` findings. It is Developer remediation evidence, not a Code Gate PASS, QA verdict, Product Acceptance, #221 fault-injection evidence, second-adapter evidence, #218 release evidence, or v0.3 release-readiness claim.

Next legal authority: fresh independent Code Re-review bound to the resulting exact PR head.