# Requirement Review — F-OPERATOR-CANONICAL-API-0001

## Verdict

PASS_WITH_NOTES

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1

The Requirement is sufficiently clear, bounded, security-preserving, and testable for `requirement-gate` to PASS and for Design to begin. The note below is a Design-level contract clarification and does not require Requirement rework.

## Authoritative baseline reviewed

- Feature: `F-OPERATOR-CANONICAL-API-0001`
- Feature Issue: `#208`
- Feature branch: `feature/F-OPERATOR-CANONICAL-API-0001`
- Manifest revision observed before review: `2`
- workflow.status: `ACTIVE`
- current_stage: `requirement-review`
- requirement: `DONE`
- requirement-review: `READY`
- requirement-gate: `PENDING`
- requirement-v1: `draft`
- immutable v0.3 Release Spec merge baseline: `c1980bba3205062495e49e685f9501a248df8365`
- approved Release Spec source head: `2e1fd261d4f1142b6b1d6fdf1b86e0027254f0c4`

`AGENTS.md` and `.ai-sdlc/project.yaml` were requested by the role guide but are not present on this Feature branch; no project-specific constraints were inferred from missing files.

## Material reviewed

- `state/features/F-OPERATOR-CANONICAL-API-0001.yaml`
- `state/bootstrap/F-OPERATOR-CANONICAL-API-0001.yaml`
- `docs/features/F-OPERATOR-CANONICAL-API-0001/requirement.md`
- Feature Issue `#208` and lifecycle handoff comments
- frozen `docs/v0.3-release-spec.md` at merge baseline `c1980bba3205062495e49e685f9501a248df8365`
- `release/v0.3.0-draft.yaml` planning boundary as carried by the frozen baseline
- `profiles/standard-feature.yaml`
- `gates/review-rubrics.yaml`
- `docs/role-guide.md`

The upstream PR #206 Release Spec Review was treated only as frozen normative context. It was not reused as this Feature's Requirement Review evidence.

## Requirement rubric assessment

### Problem and goal — PASS

The Requirement identifies the concrete v0.3 foundation gap: supported AI clients lack one transport-independent canonical Operator contract, creating risk of transport-specific semantics, incompatible errors/identity rules, and adapter escape hatches into arbitrary lifecycle or repository mutation.

The goal is appropriately bounded to the canonical `ai-sdlc.operator/v1` contract and deterministic conformance foundation before durable Operation Store, dispatch/recovery, Decision/Notification persistence, vertical-loop dogfood, or release publication work.

### Scope and non-goals — PASS

The Requirement preserves the frozen implementation ordering without absorbing later workstreams. It explicitly excludes durable Operation Journal/Store, generation fencing, semantic-effect reservation, launch/Persist linearization, external receipt recovery, full Developer → Reviewer → Remediation → Re-review → QA orchestration, Decision/Notification persistence, Project Takeover, dashboard work, version bump, final release manifest, and dogfood completion.

This is important because typed schemas for later capabilities must not be confused with implemented durable backing behavior.

### User scenarios and discovery behavior — PASS

The Requirement captures the user-facing scenarios required by the release slice at this layer:

- capability/version discovery;
- project/Feature inspection;
- new-session discovery through `operator.inbox` without a remembered `operation_id`;
- typed start/status/resume/cancel and Decision/Notification operations;
- honest unavailability until trusted backing workstreams exist.

`operator.inbox` is correctly specified to represent unfinished Operations, `NEEDS_USER`/`BLOCKED` Operations, pending Decisions, and unread Notifications without fabricating those records in this Feature.

### Business rules and authority invariants — PASS

The Requirement preserves the critical frozen authority rules:

- Operator is not Feature lifecycle authority;
- AI client identity alone is not human approval or Acceptance evidence;
- target/Feature-branch input cannot expand trusted authorization policy;
- adapters cannot expose arbitrary Manifest patches, executable Feature Events, Gate writes, unrestricted shell execution, or generic repository writes;
- client input cannot self-promote to trusted runtime/service identity;
- existing Feature Event + trusted Persist / Gate / release authority remains unchanged.

The Requirement also correctly requires expected Feature revision binding for lifecycle-sensitive writes and idempotency identity for semantic writes without pretending to implement the later distributed side-effect guarantees.

### Acceptance criteria and testability — PASS

The Acceptance Criteria are deterministic and falsifiable. They cover:

- exact API identity/version semantics;
- all twelve frozen capability identifiers;
- honest `system.capabilities` availability;
- unsupported-version rejection before semantic write execution;
- machine-readable structured errors;
- identity separation and escalation rejection;
- idempotency and expected-revision bindings;
- absence of arbitrary mutation escape hatches;
- honest behavior for unimplemented backing workstreams;
- reusable transport-neutral conformance fixtures;
- negative schema/identity/version/idempotency/revision tests;
- relevant v0.2 regression checks;
- explicit unresolved downstream release blockers.

The evidence expectations are correspondingly concrete and do not permit this Feature to claim v0.3 release readiness on contract implementation alone.

### Canonical capability and error contract — PASS_WITH_NOTE

The capability vocabulary exactly matches the frozen Release Spec, including `operation.resume` and bounded `notification.ack`. The minimum two-adapter conformance subset also matches the frozen baseline, and the Requirement preserves the rule that two real materially independent supported AI client adapters are a release requirement even if their completion is split into later Features.

The structured error vocabulary includes the complete frozen minimum set and requires machine-readable bounded details without secret leakage.

One deterministic taxonomy choice should be made explicit in Design: distinguish an **unknown/unrecognized capability identifier** from a **known canonical capability whose trusted backing implementation is unavailable**. The latter is clearly `CAPABILITY_UNAVAILABLE`; the former should receive one deterministic canonical rejection (for example `INVALID_REQUEST`) rather than allowing adapters to choose between multiple meanings. This avoids cross-adapter divergence in the conformance harness.

Severity: MINOR. The Requirement already requires deterministic rejection and honest availability; choosing the exact known-vs-unknown mapping is an implementable Design contract detail, not an unresolved product decision.

### Identity, authorization and security — PASS

The Requirement has a clear trust boundary between represented human principal, AI client adapter identity, trusted runtime/service identity, requested capability, and trusted authorization-policy context. It forbids client-controlled trusted identity escalation and branch-controlled authorization expansion.

Schemas are bounded, errors/capability discovery cannot expose credentials or unrestricted exception data, and adapter convenience fields cannot bypass canonical validation.

### Idempotency and revision semantics — PASS

Every semantic write must require or deterministically derive an idempotency key. Lifecycle-sensitive writes bind exact expected Feature revision and must be able to fail as `STALE_REVISION` rather than silently rebasing intent.

The Requirement correctly limits this Feature to the canonical contract/idempotency boundary and does not claim the semantic-effect reservation, dispatch claim, launch linearization, receipt recovery, or Persist linearization guarantees owned by later workstreams.

### Compatibility and release-readiness boundary — PASS

The Requirement explicitly preserves existing v0.2 Manifest/Event/Persist/Gate/Safe Output/runtime/cross-repository semantics and existing clients that do not use the new API.

It forbids changing `VERSION`, creating `release/v0.3.0.yaml`, marking v0.3 release-candidate/release-ready, or resolving dogfood blockers without actual evidence. Completion evidence must enumerate unresolved adapters, Operation Store, concurrency/recovery, vertical-loop dogfood, Decision/Notification persistence, and publication blockers.

### Edge cases and open decisions — PASS

The Requirement includes deterministic negative coverage for malformed schemas, unsupported versions, unavailable capabilities, identity escalation, missing idempotency/revision bindings, duplicate-equivalent requests at the contract boundary, and secret-safe errors.

No unresolved product/business decision remains that would prevent Design from starting. The known-vs-unavailable capability taxonomy note is intentionally delegated to Design because the product-level semantics are already fixed: unknown input must fail deterministically and known-but-unbacked capability must never fabricate success.

## Review note for Design / Verification

### MINOR-1 — Freeze known-vs-unavailable capability error taxonomy

Design should specify one canonical mapping for:

- unknown/unrecognized capability identifier; and
- known `ai-sdlc.operator/v1` capability with unavailable trusted backing behavior.

`CAPABILITY_UNAVAILABLE` is reserved by the Requirement for the latter. The former should have one deterministic fail-closed code and the same rule must be exercised by every adapter conformance fixture.

## Gate recommendation

`requirement-gate`: PASS.

`requirement-v1` may be approved. `requirement-review` may complete and `design` may become READY through the trusted Feature Event/Persist path.

This review does not approve Design, Implementation, Code Review, Verification, dogfood, publication, or v0.3 release readiness.
