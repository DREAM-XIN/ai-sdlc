# Verification Remediation — F-OPERATOR-CANONICAL-API-0001

## Scope

Developer remediation for `remediation-verification-v1` on Issue #208 / PR #209 only.

This evidence addresses **QA-MAJOR-1** from `docs/features/F-OPERATOR-CANONICAL-API-0001/verification.md`. It does not perform independent Verification, does not change `verification-gate` authority, and does not claim v0.3 release readiness.

## QA-MAJOR-1 root cause

The previous validator executed most semantic checks directly against canonical `dispatch(...)` and ended with two `CounterBackend` objects whose manually assigned identity/transport strings were merely compared as tuples. That did not establish a reusable AI-client adapter invocation boundary, did not execute the frozen six-capability conformance subset through two adapter implementations, and did not prove material independence beyond metadata strings.

## Bounded remediation

Only the conformance test architecture was changed.

### `scripts/operator_conformance.py`

A reusable transport-neutral conformance harness now defines a minimal adapter boundary:

```text
adapter.adapter_id
adapter.transport_kind
adapter.invoke(canonical_request) -> canonical_response
```

The harness owns the shared semantic assertions. Fixture adapters own only their transport conversion/boundary behavior.

Two explicitly test-only fixture implementations exercise the same suite:

1. `DirectFixtureAdapter`
   - adapter id: `fixture.direct`
   - transport kind: `in-process-object`
   - implementation boundary: canonical Python object invocation with defensive copying.
2. `JsonRoundTripFixtureAdapter`
   - adapter id: `fixture.json-roundtrip`
   - transport kind: `json-round-trip`
   - implementation boundary: request, trusted context, and response cross JSON serialization/deserialization boundaries.

Both consume the same canonical dispatcher and the same semantic fixture backend contract, but they are distinct adapter implementation classes with distinct transport boundaries.

The reusable suite executes the frozen conformance subset through `adapter.invoke(...)` for both fixtures:

- `system.capabilities`
- `feature.status`
- `operator.inbox`
- `operation.status`
- `decision.list`
- `notification.list`

The same suite also checks:

- unsupported API version -> `UNSUPPORTED_API_VERSION`;
- unknown capability -> `INVALID_REQUEST`;
- known unavailable capability -> `CAPABILITY_UNAVAILABLE`;
- client trusted-identity injection -> `INVALID_REQUEST`;
- client adapter identity propagation into the canonical backend boundary;
- trusted runtime / authorization-context propagation.

### Material-independence evidence

`adapter_evidence()` records:

- adapter id;
- transport kind;
- concrete implementation type;
- root implementation type after delegate unwrapping;
- wrapper depth.

`assert_materially_independent()` rejects evidence when adapters:

- are aliases/thin wrappers (`wrapper_depth > 0`);
- resolve to the same root implementation type;
- use the same transport boundary; or
- expose the same adapter id.

`AliasFixtureAdapter` is an intentional thin wrapper with explicit delegate lineage. The deterministic validator proves it is rejected both as independent evidence and as a standalone conformance adapter. Independence is therefore not inferred from two manually chosen unequal strings.

### `scripts/validate_operator_api.py`

The existing umbrella validation entrypoint now imports and runs the shared harness. It retains the existing schema/registry/version/identity/idempotency/revision/discovery/error-safety tests, then:

- executes the same reusable suite against both fixture adapter implementations;
- compares their semantic signatures;
- asserts both exercised exactly the frozen six-capability subset;
- validates material independence using implementation/transport evidence;
- proves an alias/thin wrapper is rejected.

No production Operator dispatcher, canonical schema, lifecycle authority, backend availability policy, Version, release manifest, or downstream durable-operation behavior was changed by this remediation.

## Exact remediation candidate

Exact code/test candidate head:

`0feb5d055dd352ba342a4889a4a28d2aceeba25d`

The only QA-MAJOR-1 code/test changes leading to that candidate are:

- new `scripts/operator_conformance.py`;
- updated `scripts/validate_operator_api.py`.

## Exact-head validation evidence

GitHub Actions associated with exact PR head `0feb5d055dd352ba342a4889a4a28d2aceeba25d` completed successfully:

- Required PR Gate — run `31347055620` — **SUCCESS**
  - `cross-repo-control-validation` job `93330814564` — **SUCCESS**
  - `protocol-validation` job `93330814573` — **SUCCESS**
  - `required-pr-gate` job `93330851490` — **SUCCESS**
- Validate AI-SDLC protocol — run `31347055614` — **SUCCESS**
- Validate Public Runtime Distribution — run `31347055591` — **SUCCESS**

The exact-head Required PR Gate protocol log contains:

```text
Operator API validation passed
- api_version: ai-sdlc.operator/v1
- capabilities: 12
- default_available: system.capabilities
- capability discovery: strict exact-vocabulary schema + bounded availability reasons
- conformance subset: 6 shared semantics through 2 fixture adapters
- adapter evidence: in-process-object != json-round-trip; alias/thin-wrapper rejected
AI-SDLC validation passed
```

The same protocol job continued through the repository's lifecycle, persistence, security, routing, cross-repository, gh-aw and v0.2 regression validators successfully.

## Release and authority boundary

These fixtures are **conformance test doubles only**. They are not the two supported v0.3 AI-client adapters and are not evidence that adapter dogfood or release conformance is complete.

Still unresolved downstream include supported production AI-client adapters, durable Operation Store/dispatch/recovery and concurrency semantics, Decision/Notification backing, unattended vertical-loop dogfood, security/publication work, VERSION/final release manifest, and final v0.3 release readiness.

This Developer remediation evidence does **not** turn the failed QA evidence into a PASS. `verification-gate` must remain non-passing until a fresh independent Verification role re-reads the remediated exact candidate and produces new Verification Evidence through the trusted lifecycle path.
