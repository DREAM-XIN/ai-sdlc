# Design Review — F-OPERATOR-CANONICAL-API-0001

## Verdict

PASS_WITH_NOTES

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1

The Design is implementable, security-preserving, compatible with the approved Requirement and frozen v0.3 Release Spec, and sufficiently bounded for `design-gate` to PASS. The remaining note is a Plan/implementation determinism requirement and does not require Design rework.

## Authoritative baseline reviewed

- Feature: `F-OPERATOR-CANONICAL-API-0001`
- Feature Issue: `#208`
- Feature branch: `feature/F-OPERATOR-CANONICAL-API-0001`
- Manifest revision observed after review start: `7`
- workflow.status: `ACTIVE`
- current_stage: `design-review`
- design-review: `WORKING`
- design-gate: `PENDING`
- approved Requirement: `requirement-v1`
- Design artifact: `design-v1` (`draft`)
- immutable v0.3 Release Spec merge baseline: `c1980bba3205062495e49e685f9501a248df8365`

The upstream PR #206 Release Spec Review was used only as frozen normative context and was not reused as Design Review evidence.

## Material reviewed

- `state/features/F-OPERATOR-CANONICAL-API-0001.yaml`
- `docs/features/F-OPERATOR-CANONICAL-API-0001/requirement.md`
- `docs/features/F-OPERATOR-CANONICAL-API-0001/requirement-review.md`
- `docs/features/F-OPERATOR-CANONICAL-API-0001/design.md`
- frozen `docs/v0.3-release-spec.md` at baseline `c1980bba3205062495e49e685f9501a248df8365`
- `gates/review-rubrics.yaml`
- `docs/role-guide.md`
- existing JSON Schema convention in `spec/feature-manifest.schema.json`
- Feature Issue `#208` and durable lifecycle handoffs

## Design rubric assessment

### Requirement coverage — PASS

The Design covers the approved Requirement's canonical `ai-sdlc.operator/v1` boundary, all twelve frozen capability identifiers, structured errors, identity separation, semantic-write idempotency/revision metadata, honest capability availability, reusable conformance harness, compatibility constraints, and explicit unresolved downstream release blockers.

It does not absorb durable Operation Store, dispatch/recovery, Decision/Notification persistence, full Developer→Reviewer→QA orchestration, Project Takeover, release publication, or dogfood evidence.

### Requirement Review MINOR-1 — RESOLVED

The prior Requirement Review required one deterministic mapping separating an unknown capability identifier from a known canonical capability whose trusted backend is unavailable.

The Design resolves this explicitly and normatively:

- unknown/unrecognized capability → `INVALID_REQUEST`;
- known canonical capability with unavailable trusted backing → `CAPABILITY_UNAVAILABLE`.

The same distinction is required in validation ordering and adapter conformance fixtures. No ambiguity remains for implementation.

### Component boundaries — PASS

The four component boundary is coherent:

1. canonical schema family under `spec/operator/`;
2. transport-neutral dispatcher/validator;
3. trusted code-owned capability registry plus availability provider;
4. reusable transport-neutral conformance harness.

The dispatcher cannot turn schema existence into lifecycle authority; backend execution remains an explicit trusted dependency. Target/Feature-controlled input cannot select backend code, URLs, commands or providers.

### Contracts and interfaces — PASS_WITH_NOTE

The request/response envelopes, identity split, backend interface, availability semantics, validation ordering and structured-error contract are explicit enough to implement safely.

Dedicated per-capability request/response schemas prevent adapter-specific payload weakening. The capability registry is also designed to carry `read/write`, idempotency, expected-revision, backend and conformance metadata.

MINOR-1: the Design intentionally leaves the exact per-capability values of `idempotency required` and `expected Feature revision required` to the concrete registry/Plan. Before implementation begins, the Plan must freeze a deterministic capability metadata matrix for all twelve identifiers and identify tests that assert it. This is non-blocking because the Requirement already defines the semantics, the Design defines the trusted metadata mechanism, and no high-impact lifecycle mutation is being implemented in this Feature; however, implementation must not infer the matrix ad hoc in adapter code.

### Data model — PASS

No durable Operator data model is introduced, which is correct for this Feature. Canonical request/response/error/identity/capability schemas are additive protocol data only. The Design does not fabricate Operation, Decision, Notification, reservation, claim or projection state.

### Failure handling — PASS

Validation ordering is deterministic and fail-closed. Unsupported API versions are rejected before semantic hooks. Unknown fields/capabilities fail deterministically. Known unavailable backends return `CAPABILITY_UNAVAILABLE`. Unexpected backend exceptions become bounded `INTERNAL_FAILURE`; transient classification is trusted-backend-owned.

Raw exceptions, tokens, credentials, environment values and unrestricted tracebacks are excluded from canonical error details.

### Security — PASS

The Design preserves the frozen authority model:

- client input cannot overwrite trusted service/runtime identity or policy context;
- Feature branches cannot register backends or expand authorization policy;
- no generic shell/repository/Manifest/Event/Gate mutation capability exists;
- adapters cannot bypass canonical validation;
- schema presence does not grant runtime authorization;
- Operator/API code cannot PASS/WAIVE Gates, directly modify authoritative Manifest state, merge or release.

This directly satisfies the approved Requirement's primary security boundary.

### Compatibility — PASS

The change is additive and leaves existing v0.2 Manifest/Event/Persist/Gate, Commander, Issue Comment, gh-aw, Runtime App and cross-repository transports intact. No `VERSION` change, final `release/v0.3.0.yaml`, migration, or release-ready claim is introduced.

### Observability — PASS

The proposed audit/log fields are bounded and diagnostic only. They exclude request secrets, credentials, tokens, unrestricted payloads and policy contents, and are explicitly not lifecycle truth.

### Migration — PASS

No durable Operator store is introduced, so no data migration is required. Existing clients remain unaffected.

### Testability — PASS

The Design contains a concrete deterministic validation strategy covering schema validity, version rejection before semantic callbacks, known-vs-unknown capability behavior, exact capability vocabulary, additional-property rejection, trusted-identity injection, semantic-write metadata, secret-safe errors, honest capability discovery, dual-adapter reusable assertions, independence evidence, and relevant v0.2 regressions.

The Plan must bind these to exact repository commands/CI checks before implementation.

### Risks and alternatives — PASS

The Design explicitly evaluates dedicated schemas vs generic payloads, bounded read backend reuse vs contract-only behavior, trusted code registry vs target-configurable registration, and validation-only idempotency vs premature durable deduplication. The chosen options preserve scope and authority.

## Findings

### MINOR-1 — Freeze capability metadata matrix in Plan

Before Developer implementation, Plan must define for each of the twelve canonical capabilities:

- read vs semantic write classification;
- idempotency-key requirement;
- expected Feature revision requirement;
- backend availability expectation in this Feature;
- conformance-subset membership.

The matrix must be represented in trusted code/tests, not adapter-local interpretation.

Severity: MINOR. No Design rework is required.

## Gate recommendation

`design-gate`: PASS.

`design-v1` may be approved. `design-review` may complete and `plan` may become READY through the trusted Feature Event/Persist path.

This review does not approve the Plan, Implementation, Code Review, Verification, adapter completion, Operation Store, dogfood, publication, or v0.3 release readiness.
