# Verification — F-OPERATOR-CANONICAL-API-0001

## Verdict

FAIL

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

Independent Verification confirms the canonical Operator schema/dispatcher suite is green on the exact reviewed functional candidate, but the required reusable two-adapter conformance harness is not actually implemented/tested to the approved Requirement/Design/Plan boundary. Verification Gate must remain non-passing until this is remediated and independently re-verified.

## Authoritative verification baseline

- Feature: `F-OPERATOR-CANONICAL-API-0001`
- Issue: `#208`
- PR: `#209`
- Feature branch: `feature/F-OPERATOR-CANONICAL-API-0001`
- Manifest revision after Verification start: `19`
- workflow.status: `ACTIVE`
- current_stage: `verification`
- verification: `WORKING`
- verification-gate: `PENDING`
- code-review: `DONE`
- code-gate: `PASS`
- exact reviewed functional candidate: `14aeba5509c6526a48e341b2421cdad65626c15e`
- immutable Release Spec baseline: `c1980bba3205062495e49e685f9501a248df8365`

Current PR head after lifecycle-only commits was `70aa6e3f1801b6764b743330eca58e8ff65d3c15`. GitHub comparison from `14aeba...` to that head shows only Feature lifecycle/evidence files changed after the functional candidate; no `scripts/operator_api.py`, `scripts/validate_operator_api.py`, `spec/operator/**`, runtime, workflow, or other implementation source changed. Therefore `14aeba...` remains the exact functional candidate under Verification.

## Independent execution evidence

QA independently re-ran the exact candidate's Required PR Gate `protocol-validation` job through GitHub Actions during Verification.

- workflow run: `31335897022`
- fresh QA-time protocol-validation job: `93329782611` — **SUCCESS**
- cross-repo-control-validation: **SUCCESS**
- required-pr-gate: **SUCCESS**

Fresh logs observed:

```text
Operator API validation passed
- api_version: ai-sdlc.operator/v1
- capabilities: 12
- default_available: system.capabilities
- capability discovery: strict exact-vocabulary schema + bounded availability reasons
- conformance fixture identities: 2 distinct; alias rejected as independent evidence
AI-SDLC validation passed
```

The broader protocol suite also passed lifecycle, persistence, security, routing, cross-repository and v0.2 regression validators.

## Verified passing boundaries

Independent inspection plus the fresh CI rerun supports these approved acceptance boundaries:

- one canonical `ai-sdlc.operator/v1` identity;
- exactly twelve frozen capability descriptors and dedicated request/response schemas;
- unsupported version fails before backend invocation;
- unknown capability maps to `INVALID_REQUEST`;
- known unavailable capability maps to `CAPABILITY_UNAVAILABLE`;
- semantic writes require idempotency according to the frozen matrix;
- `operation.start` and `operation.resume` require expected Feature revision while cancel/respond/ack do not incorrectly acquire that precondition;
- client trusted-identity injection and unknown fields fail closed before backend execution;
- `system.capabilities` uses the strict exact-vocabulary discovery contract and bounds availability reasons;
- secret-bearing availability/exception text is not serialized through the canonical error/discovery boundary;
- prohibited generic shell/Manifest/Event/Gate/repository-write/merge/release capability ids are absent;
- existing required protocol/control-plane regressions remain green on the functional candidate.

## QA-MAJOR-1 — reusable two-adapter conformance harness is not implemented

The approved Requirement makes a reusable transport-neutral conformance harness a required outcome and Acceptance Criterion 10. The approved Design requires a harness that can invoke an adapter through a boundary equivalent to `adapter.invoke(canonical_request) -> canonical_response`, with adapter identity and transport kind visible, and requires the same semantic assertions to run against two fixture adapter implementations. The approved Plan makes this concrete: one semantic assertion suite must run against two distinct fixture adapter implementations, including the frozen conformance subset (`system.capabilities`, `feature.status`, `operator.inbox`, `operation.status`, `decision.list`, `notification.list`), while an alias/thin wrapper must not count as independent evidence.

The exact candidate's `scripts/validate_operator_api.py` does not provide that harness boundary. Its semantic assertions call canonical `dispatch(...)` directly. At the end it creates two `CounterBackend` instances with different `identity`/`transport` string attributes and only asserts that those tuples differ; it creates an alias and only asserts the alias tuple is equal. Those fixture objects are backend test doubles, not AI-client adapter implementations, and their identity/transport values do not participate in a reusable adapter invocation/conformance execution path.

No second conformance helper/fixture implementation exists in the PR changed-file inventory. The validator also does not execute the common frozen read subset through two adapters; beyond `system.capabilities`, it exercises a typed success backend for `feature.status`, while the other required conformance-subset capabilities are not run as common adapter semantics.

Consequences:

1. A later ChatGPT/MCP/other adapter Feature cannot plug an adapter into a stable common harness and run the same semantic assertions without modifying/copying the current validator.
2. The current `2 distinct` log line proves only two metadata tuples differ, not that two adapter boundaries conform to identical canonical semantics.
3. Requirement Required Outcome 9 / Acceptance Criterion 10 and Design/Plan conformance-harness requirements are therefore not met.

Severity: **MAJOR** because this is a required Feature outcome and the foundation is specifically intended to prevent later adapters from duplicating or diverging semantic assertions. A passing test suite cannot substitute for a required harness architecture that is absent from the tested code.

## Required remediation

Developer remediation must remain bounded to the conformance-harness gap:

1. Introduce a reusable transport-neutral adapter test boundary (for example a small protocol/helper exposing stable adapter identity, transport kind, and `invoke(canonical_request)`).
2. Extract reusable semantic assertions so the same suite is executed against at least two materially distinct fixture adapter implementations, not merely two backend objects or metadata tuples.
3. Exercise the frozen conformance subset through that shared harness, including structured errors, version semantics, identity propagation and unavailable-capability behavior as applicable to the foundation.
4. Add deterministic alias/thin-wrapper rejection based on the adapter implementation/transport evidence required by the approved Design/Plan, not just manually chosen unequal strings.
5. Keep fixture adapters explicitly test doubles; do not claim they are the two supported v0.3 release adapters.
6. Re-run `python scripts/validate_operator_api.py` and required exact-head CI, then return to independent Verification.

This remediation must not broaden into supported production adapters, durable Operation Store/recovery, Decision/Notification persistence, dogfood, publication, VERSION changes, Gate/merge/release authority, or other downstream v0.3 workstreams.

## Release boundary

Verification FAIL is scoped to this Feature's conformance-harness acceptance boundary. Even after remediation, this Feature alone cannot establish v0.3 release readiness. Supported materially independent AI-client adapters, durable Operation Store/dispatch/recovery, Decision/Notification backing, unattended vertical-loop dogfood, recovery/concurrency fault injection, publication/security readiness, `VERSION`, and final `release/v0.3.0.yaml` remain unresolved downstream work.
