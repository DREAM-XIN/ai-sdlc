# Requirement — F-OPERATOR-MCP-ADAPTER-0001

## 1. Objective

Deliver a genuine supported MCP AI-client adapter for the frozen `ai-sdlc.operator/v1` canonical Operator API, bounded to the read-only inspect/status surface required by the v0.3 implementation order.

The adapter must prove that a real MCP transport can cross the canonical API boundary without reimplementing lifecycle authority, inventing a second Feature truth, or pretending that later durable Operation/Decision/Notification backing already exists.

## 2. Normative upstream

This Feature consumes, but must not silently redefine:

- frozen v0.3 Release Spec from PR #206;
- `F-OPERATOR-CANONICAL-API-0001` / Issue #208 / PR #209;
- canonical API identifier `ai-sdlc.operator/v1`;
- canonical capability registry, request/response envelopes, structured error taxonomy, trusted identity boundary, bounded availability vocabulary, and reusable transport-neutral conformance harness already shipped by the canonical API Feature.

Any incompatible canonical contract change is out of scope and requires separate review.

## 3. Required MCP adapter identity and transport boundary

The implementation SHALL expose one supported adapter with a stable adapter identity distinct from all test fixtures and a transport kind that represents a real MCP protocol boundary.

The adapter SHALL NOT count an alias, rename, subclass-only facade, or thin wrapper around `fixture.direct`, `fixture.json-roundtrip`, or another in-process fixture as release adapter evidence.

The adapter SHALL translate MCP tool/resource invocation into the existing canonical request envelope and translate canonical responses/errors back into MCP-compatible results without bypassing `scripts/operator_api.py` dispatch semantics.

## 4. Required read-only capability surface

The MCP adapter SHALL expose the following canonical capabilities when their trusted backing is available:

- `system.capabilities`
- `project.inspect`
- `feature.status`
- `operator.inbox`
- `operation.status`
- `decision.list`
- `notification.list`

The common frozen conformance subset remains:

- `system.capabilities`
- `feature.status`
- `operator.inbox`
- `operation.status`
- `decision.list`
- `notification.list`

`project.inspect` is required for the supported MCP read surface but is not added to the frozen common conformance subset by this Feature.

## 5. Honest availability semantics

The MCP adapter SHALL preserve the canonical distinction between:

- an unknown/unrecognized capability identifier → deterministic `INVALID_REQUEST`;
- a known canonical capability whose trusted backing is not implemented/configured/permitted → `CAPABILITY_UNAVAILABLE` with the bounded canonical availability reason vocabulary.

The adapter SHALL NOT advertise a capability as available merely because a request/response schema exists.

Until later Operator workstreams provide durable backing, `operator.inbox`, `operation.status`, `decision.list`, and `notification.list` may legitimately return `CAPABILITY_UNAVAILABLE`; if deterministic read-only fixtures/backends are used for conformance, those fixtures SHALL be clearly test-only and SHALL NOT be represented as production durable stores.

## 6. Version and structured error behavior

Every MCP-originated canonical request SHALL identify `ai-sdlc.operator/v1`.

Unsupported versions SHALL return canonical `UNSUPPORTED_API_VERSION` semantics without executing semantic work.

Malformed MCP-to-canonical payloads, unknown capabilities, and client attempts to inject trusted-only fields SHALL fail closed using the canonical structured error model. MCP-facing output SHALL preserve machine-readable error code semantics and SHALL NOT require parsing arbitrary human text to determine the error class.

## 7. Identity and authorization boundary

The MCP adapter SHALL provide an explicit adapter identity in canonical `client_identity` and SHALL keep client-supplied identity separate from trusted runtime/service identity.

Client-controlled MCP arguments MUST NOT be able to:

- self-assert `trusted_identity` or trusted authorization context;
- select a more privileged runtime/service identity;
- broaden trusted authorization policy;
- override repository/Feature target validation supplied by trusted runtime context;
- bypass canonical validation or backend availability checks.

Where a human principal is available from trusted client/session context, it may be represented explicitly, but MCP adapter identity itself MUST NOT imply human authorization.

## 8. Read-only safety boundary

This Feature SHALL NOT expose MCP tools/resources that perform canonical semantic writes, including:

- `operation.start`
- `operation.resume`
- `operation.cancel`
- `decision.respond`
- `notification.ack`

It SHALL NOT expose arbitrary shell execution, arbitrary repository writes, raw Feature Event mutation, direct Feature Manifest edits, Gate changes, merge/release actions, or unrestricted workflow dispatch as substitutes for canonical operations.

If a caller attempts a later write capability through this bounded adapter, the result SHALL be fail-closed and SHALL not cause a semantic side effect.

## 9. Lifecycle authority boundary

The MCP adapter is transport only. It MUST NOT become lifecycle authority.

Specifically, this Feature MUST NOT:

- directly edit authoritative Feature Manifests;
- PASS or waive Gates;
- construct arbitrary Feature Events from client input;
- create a second Feature lifecycle truth in MCP/session-local state;
- weaken expected-revision, candidate binding, Feature Event, Persist, protected-branch, or independent-role controls;
- claim durable Operation Journal/Store, dispatch/recovery, Decision persistence, Notification outbox, or unattended vertical-loop completion.

## 10. Conformance requirements

The implementation SHALL plug into the reusable `CanonicalAdapter` boundary in `scripts/operator_conformance.py` or an equivalent reviewed extension that preserves the same transport-neutral semantics.

Automated validation SHALL prove at minimum:

1. stable supported MCP adapter id and real MCP transport kind;
2. the six frozen common read capabilities pass the shared canonical conformance semantics when deterministic trusted test backends are supplied;
3. `project.inspect` is reachable through the MCP adapter when its trusted backend is supplied;
4. unsupported API version → `UNSUPPORTED_API_VERSION`;
5. unknown capability → `INVALID_REQUEST`;
6. known but unavailable capability → `CAPABILITY_UNAVAILABLE`;
7. adapter identity reaches the canonical boundary unchanged;
8. client attempts to inject trusted identity/authorization fields are rejected;
9. canonical structured errors survive MCP translation with stable machine-readable codes;
10. write capabilities are not advertised/exposed as supported MCP operations in this Feature;
11. the MCP implementation is not a thin wrapper/alias of either canonical test fixture;
12. no conformance fixture is misrepresented as production durable backing.

## 11. Determinism and dependency constraints

Tests SHALL be deterministic and MUST NOT require a live external MCP client service, production credentials, or network access to prove protocol semantics.

The implementation MAY use an in-process MCP protocol/client test harness to exercise real MCP message/tool boundaries, provided the tested path is the same adapter implementation shipped as the supported MCP adapter rather than a separate test-only transport facade.

Any new third-party MCP dependency SHALL be pinned/declared consistently with repository dependency and security policy and SHALL not silently introduce an unrestricted execution surface.

## 12. Backward compatibility

Existing canonical API fixture adapters and validation suites SHALL continue to pass unchanged unless an independently reviewed compatibility adjustment is strictly required.

Existing v0.2 lifecycle, gh-aw routing, Feature Event/Persist authority, and repository installation behavior SHALL remain unaffected.

The Feature SHALL not change `VERSION`, create `release/v0.3.0.yaml`, or mark v0.3 release-candidate.

## 13. Acceptance criteria

The Requirement is satisfied only when independent downstream review/verification evidence supports all of the following:

- a real supported MCP adapter exists over `ai-sdlc.operator/v1`;
- the bounded read-only MCP surface is implemented with honest backend availability;
- the shared canonical conformance subset passes through the MCP boundary;
- identity, structured errors, version negotiation, and trusted-context injection protections pass deterministic tests;
- MCP does not gain arbitrary lifecycle/repository mutation authority;
- write-capable Operator behavior and durable orchestration remain explicitly deferred;
- the implementation is materially distinct from the canonical test fixtures and can later serve as one of the two required supported v0.3 AI-client adapters.

Completion of this Feature alone does **not** satisfy the v0.3 requirement for two materially independent supported AI-client adapters, does not satisfy the minimum write-capable adapter requirement, and does not make v0.3.0 release-ready.
