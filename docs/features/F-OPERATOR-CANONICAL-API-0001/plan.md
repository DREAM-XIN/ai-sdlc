# Implementation Plan — v0.3 canonical typed Operator API foundation

Feature: `F-OPERATOR-CANONICAL-API-0001`

Issue: `#208`

Approved Requirement: `requirement-v1`

Approved Design: `design-v1`

Immutable Release Spec baseline: `c1980bba3205062495e49e685f9501a248df8365`

## 1. Objective

Implement only the transport-independent `ai-sdlc.operator/v1` contract foundation approved by Requirement and Design: canonical schemas, trusted capability metadata, deterministic dispatcher validation/error behavior, bounded backend availability abstraction, and reusable conformance fixtures.

This Plan does not implement the durable Operation Store, dispatch/recovery, Decision/Notification persistence, real AI-client adapters, vertical-loop dogfood, publication, or release readiness.

## 2. Design Review MINOR resolution — frozen capability metadata matrix

The implementation MUST encode this matrix once in trusted code and assert it in deterministic tests. Adapters MUST NOT maintain their own interpretation.

| Capability | Class | Idempotency key | Expected Feature revision | Default backend in this Feature | Frozen two-adapter conformance subset |
| --- | --- | --- | --- | --- | --- |
| `system.capabilities` | read/discovery | no | no | AVAILABLE — foundation-owned discovery | yes |
| `project.inspect` | read/discovery | no | no | UNAVAILABLE by default | no |
| `feature.status` | read/discovery | no | no | UNAVAILABLE by default | yes |
| `operator.inbox` | read/discovery | no | no | UNAVAILABLE by default | yes |
| `operation.start` | semantic write | yes | yes | UNAVAILABLE by default | no |
| `operation.status` | read/discovery | no | no | UNAVAILABLE by default | yes |
| `operation.resume` | semantic write | yes | yes | UNAVAILABLE by default | no |
| `operation.cancel` | semantic write | yes | no | UNAVAILABLE by default | no |
| `decision.list` | read/discovery | no | no | UNAVAILABLE by default | yes |
| `decision.respond` | semantic write | yes | no | UNAVAILABLE by default | no |
| `notification.list` | read/discovery | no | no | UNAVAILABLE by default | yes |
| `notification.ack` | semantic write | yes | no | UNAVAILABLE by default | no |

Rationale for revision binding:

- `operation.start` and `operation.resume` are lifecycle-sensitive orchestration decisions and therefore require an explicit `expected_feature_revision` at the canonical contract boundary.
- `operation.cancel` must remain able to stop automatic progression even if Feature state has moved; cancellation fencing belongs to the later Operation Store workstream rather than a client-supplied Feature revision precondition.
- `decision.respond` is bound by future trusted Decision/Operation identity and authorization context; this foundation requires idempotency but does not invent an additional Feature revision rule absent durable Decision semantics.
- `notification.ack` is acknowledgement state only and is not Feature-lifecycle-sensitive.

A later approved Feature may add a trusted backend or tighten runtime policy, but must not silently change the public `ai-sdlc.operator/v1` matrix in adapter-local code.

## 3. Work units

### WU-1 — Canonical schema family

Create `spec/operator/` with JSON Schema 2020-12 definitions for:

- request envelope;
- response envelope;
- structured error;
- client/trusted identity context boundaries;
- capability discovery result;
- dedicated request and response schemas for all twelve capabilities.

Constraints:

- exact API identifier `ai-sdlc.operator/v1`;
- bounded identifiers/strings/arrays;
- `additionalProperties: false` by default;
- no arbitrary Manifest patch, Feature Event, Gate mutation, shell command, generic repository write, merge or release field;
- response must contain exactly one of typed `result` or typed `error`.

### WU-2 — Trusted capability registry

Implement a code-owned registry, preferably in `scripts/operator_api.py` or a narrowly imported companion module.

Each descriptor MUST contain:

- canonical capability id;
- request schema reference;
- response schema reference;
- class (`read` or `write`);
- `requires_idempotency`;
- `requires_expected_feature_revision`;
- backend key;
- conformance-subset membership.

The registry MUST match the matrix in section 2 exactly and reject duplicate/missing/unexpected capability descriptors.

### WU-3 — Canonical dispatcher and identity boundary

Implement transport-neutral request handling that performs deterministic validation in this order:

1. outer envelope/schema validation;
2. API version check;
3. capability lookup;
4. capability-specific payload and precondition validation;
5. trusted identity/context merge without allowing client overwrite;
6. backend availability decision;
7. backend invocation only when available;
8. canonical response/error serialization.

Required mappings:

- unsupported API version → `UNSUPPORTED_API_VERSION` before semantic hooks;
- unknown capability → `INVALID_REQUEST`;
- known but unavailable backend → `CAPABILITY_UNAVAILABLE`;
- malformed idempotency/revision/identity fields → `INVALID_REQUEST`;
- unexpected unclassified backend failure → bounded `INTERNAL_FAILURE` without raw secret/traceback material.

The dispatcher MUST NOT call Feature Persist, Gate, merge, release, shell, or provider-specific inference paths directly.

### WU-4 — Backend availability abstraction

Implement a bounded trusted backend interface with explicit `availability` and `invoke` operations.

Default production/foundation behavior for this Feature:

- `system.capabilities`: available through a foundation-owned backend;
- every other capability: known but unavailable unless an explicitly trusted test fixture backend is injected.

`system.capabilities` MUST list all twelve capabilities and distinguish contract support from runtime availability without exposing credentials, secret names/values, policy contents, or internal exception data.

Do not add durable Operator state merely to make unavailable capabilities return success.

### WU-5 — Reusable conformance harness and deterministic negative coverage

Add `scripts/validate_operator_api.py` as the single deterministic validation entrypoint for this Feature. It may import reusable fixture helpers from `tests/` or `scripts/`, but the command below must exercise the complete Feature-specific suite.

The suite MUST prove at least:

1. all Operator schemas are valid JSON Schema 2020-12;
2. exact supported version request/response behavior;
3. unsupported version rejects before a semantic/backend callback counter changes;
4. all twelve capability descriptors exist exactly once;
5. matrix metadata exactly matches section 2;
6. unknown capability → `INVALID_REQUEST`;
7. known unavailable capability → `CAPABILITY_UNAVAILABLE`;
8. additional/unknown request fields fail closed;
9. client attempt to supply trusted runtime/service/policy identity fails before backend invocation;
10. every semantic write requires a nonempty idempotency key;
11. `operation.start` and `operation.resume` require expected Feature revision;
12. `operation.cancel`, `decision.respond`, and `notification.ack` do not incorrectly acquire a Feature-revision precondition;
13. `system.capabilities` reports known unavailable capabilities honestly;
14. no prohibited generic mutation capability exists;
15. backend exception details are secret-safe and bounded;
16. one semantic assertion suite can run against two fixture adapter implementations with distinct adapter id and transport kind;
17. an alias/thin wrapper over the same adapter identity/transport implementation cannot be counted as materially independent release evidence;
18. fixture backends can exercise typed success semantics without becoming default production availability.

The fixture adapters are conformance test doubles only. They MUST NOT be described as the two supported release adapters.

### WU-6 — Implementation evidence and documentation

Create `docs/features/F-OPERATOR-CANONICAL-API-0001/implementation.md` and implementation verification evidence recording:

- files/components implemented;
- exact capability matrix actually encoded;
- which capabilities are truly available by default (`system.capabilities` only unless separately justified);
- deterministic command results;
- candidate commit/PR identity when available;
- explicit unresolved v0.3 blockers: supported adapters, durable Operation Store, concurrency/recovery, Decision/Notification backing, vertical-loop dogfood, security/release publication work.

Do not modify `VERSION`, create `release/v0.3.0.yaml`, or mark release/dogfood blockers resolved.

## 4. Required deterministic commands

Developer MUST run and record at least:

```bash
python scripts/validate_operator_api.py
python scripts/validate_feature_manifest.py state/features/F-OPERATOR-CANONICAL-API-0001.yaml
```

In addition, run the repository's existing required PR checks triggered by the changed `spec/**`, `scripts/**`, test/docs paths on the exact candidate head. Code Review/Verification must treat any failing required check as non-passing evidence rather than waiving it locally.

If implementation introduces an additional dedicated unit-test command, `validate_operator_api.py` must still remain the deterministic umbrella entrypoint so later adapter Features have one canonical conformance command to reuse.

## 5. Change boundaries

Expected implementation ownership:

- `spec/operator/**`;
- `scripts/operator_api.py` and/or narrowly scoped trusted helper modules;
- `scripts/validate_operator_api.py`;
- bounded test/fixture files needed by that validator;
- Feature implementation/evidence documentation.

Do not change existing Feature Manifest/Event/Persist/Gate semantics, gh-aw authority, Runtime App authorization, provider routing, protected merge/release behavior, `VERSION`, or final release manifest.

Any discovered need to change those boundaries is scope escalation and requires separate approved lifecycle work rather than being absorbed by Developer.

## 6. Implementation sequence

1. Materialize schema family and schema-loading helpers.
2. Encode and self-validate the frozen capability metadata matrix.
3. Implement request validation and canonical error envelope generation.
4. Implement identity/trusted-context separation.
5. Implement default availability backend and foundation-owned `system.capabilities`.
6. Add fixture backend/adapter interfaces solely for conformance testing.
7. Build the deterministic validator suite, including negative callback counters and independence checks.
8. Run the Feature-specific validator and current Manifest validator.
9. Produce implementation/evidence docs with unresolved-release-blocker statement.
10. Open/update the Feature implementation PR as appropriate and require exact-head CI before handing to independent Code Review.

## 7. Definition of Done for Implementation handoff

Implementation may move to independent Code Review only when:

- all twelve typed capability contracts exist;
- trusted registry metadata equals the matrix in section 2;
- `system.capabilities` is the only default available backend unless separately justified without authority expansion;
- unavailable capabilities return `CAPABILITY_UNAVAILABLE`, not fabricated success;
- unsupported version and identity-escalation tests prove zero semantic callback execution;
- idempotency/revision preconditions match the frozen matrix;
- conformance fixtures prove semantic reuse without being misrepresented as supported release adapters;
- `python scripts/validate_operator_api.py` passes;
- current Feature Manifest validation passes;
- required PR checks on the candidate head are green;
- implementation/evidence docs explicitly enumerate unresolved downstream v0.3 release blockers;
- no lifecycle/Gate/merge/release authority has moved into the Operator API.

## 8. Evidence expected downstream

Independent Code Reviewer should inspect the exact implementation diff/PR and verify Requirement/Design/Plan compliance, especially the matrix, validation ordering, client/trusted identity split, unavailable-backend honesty, secret-safe errors, and absence of generic mutation escape hatches.

Independent QA should rerun the deterministic validator against the exact reviewed candidate and verify acceptance criteria/negative paths independently rather than relying only on Developer-reported output.

This Plan does not itself approve Implementation, Code Review, Verification, Acceptance, dogfood, publication, or v0.3 release readiness.
