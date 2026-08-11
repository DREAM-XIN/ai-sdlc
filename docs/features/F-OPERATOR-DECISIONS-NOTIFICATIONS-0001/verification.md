# Verification QA — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## Role and exact candidate

Role: independent Verification QA.

Feature: `F-OPERATOR-DECISIONS-NOTIFICATIONS-0001` / Issue #229 / PR #230.

QA lifecycle head at verification: `a9d3c73400e30c8b72561e5564a1ccce8915afe4`.

Validated functional remediation candidate: `72cc8cd0fef06923d34cfb3b3b566965ba544eef`.

The diff from the functional candidate to the QA head contains only Code Review/remediation evidence, Code Gate lifecycle materialization, Verification START, and Manifest updates. There are no runtime/schema/test changes after the validated functional candidate.

## Verdict

**PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR**

The Feature-level Requirement acceptance scenarios are covered by authoritative deterministic validation and the final candidate is regression-green. This QA PASS does not substitute for Issue #221 real-runtime fault injection or Product Acceptance.

## Independent verification coverage

QA verified that authoritative validation covers the required safety/product behavior, including:

- pending Decision survives restart/rebuild and appears through `decision.list` and `operator.inbox`;
- exact allowed choice resolves once; duplicate equivalent response converges; conflicting response fails closed;
- generic/fuzzy natural-language approval is rejected as an unbounded choice;
- wrong trusted responder/client/scope fails closed;
- stale Feature revision/ref/candidate binding fails closed;
- stale Operation generation is fenced, including resolved-Decision -> takeover -> authorization consumption returning `SUPERSEDED_GENERATION` before mutation;
- cancellation makes pending Decision non-current and late response returns `CANCELLED_OPERATION`;
- trusted policy epoch/digest/effective-scope drift invalidates stale Decision authority;
- control repository and target Feature repository are distinct: explicitly protected cross-repo target succeeds, unauthorized target fails, and legacy omitted target allowlist remains same-repo-only/fail-closed;
- Feature restrictions are tighten-only for choices/responders/TTL/warning policy;
- trusted clock rejects expired Decision response/authorization immediately;
- durable `decision.expired` is materialized by trusted reconcile and reducer rebuild remains independent of wall-clock time;
- required `decision.requested`, `operation.blocked`, `operation.completed`, and `authorization.expiring` Notifications are deterministic durable outbox facts;
- replayed semantic Notification production converges while preserving the first durable creation time;
- `notification.ack` is exact, idempotent and does not grant authorization or alter Feature lifecycle authority;
- unread Notifications and pending Decisions are rebuilt for new-session discovery;
- caller payload cannot broaden repository/Feature scope or choose protected policy/state-ref/trusted clock;
- CAS conflict handling re-reads/re-plans through the accepted protected Operator Store path;
- Decision resolution does not create launch authority, a new Effect Lineage reservation/key, or Persist authority; existing `dispatch.launch.authorized`, Effect Lineage, cancellation and `persist.linearized` fences remain separate;
- Reviewer/QA/Product authority boundaries remain unchanged and MCP remains read-only.

The new Decision/Notification tests are not orphan scripts: `validate_operator_decisions_notifications.py` is executed from the authoritative Operator API validation, while focused takeover/cancel/cross-repository remediation validation is wired directly into `scripts/validate.py`.

## Exact candidate CI

Functional candidate `72cc8cd0fef06923d34cfb3b3b566965ba544eef`:

- Validate AI-SDLC protocol run `31452391877` — SUCCESS;
- Validate Public Runtime Distribution run `31452391924` — SUCCESS;
- Required PR Gate run `31452391893` — SUCCESS.

The Protocol run also completed the repository's existing cross-repository control-plane validation successfully.

## Release boundary

This Verification PASS establishes Feature-level deterministic QA only. It does not prove real-runtime crash/lost-ACK external effect safety (#221), the second materially independent supported AI-client adapter, #218 release accounting, final dogfood, or overall v0.3 release readiness.

Next legal stage after trusted Verification Gate materialization: Product Acceptance. Under the frozen v0.3 standard policy, final Acceptance remains a human/Product decision and cannot be synthesized by QA.