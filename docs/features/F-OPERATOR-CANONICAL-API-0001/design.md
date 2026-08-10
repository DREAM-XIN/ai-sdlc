# Design — v0.3 canonical typed Operator API foundation

Feature: `F-OPERATOR-CANONICAL-API-0001`

Issue: `#208`

Immutable Release Spec baseline: `c1980bba3205062495e49e685f9501a248df8365`

Approved Requirement: `requirement-v1`

## 1. Design objective

Introduce one transport-independent `ai-sdlc.operator/v1` contract layer that can be consumed by multiple AI-client adapters without moving lifecycle authority out of the existing trusted AI-SDLC control plane.

This Feature deliberately implements the contract, validation, capability-discovery model and reusable conformance surface only. Durable Operation Store, dispatch/recovery, Decision/Notification persistence and release dogfood remain later workstreams.

## 2. Component boundaries

The implementation is split into four trusted components.

### 2.1 Canonical contract definitions

Add a dedicated Operator contract family under `spec/operator/`:

- `request-envelope.schema.json`
- `response-envelope.schema.json`
- `error.schema.json`
- `identity-context.schema.json`
- `capabilities.schema.json`
- per-capability request/response schemas under `spec/operator/capabilities/`

All schemas use JSON Schema 2020-12, follow existing repository schema conventions, reject additional properties by default, and bind the version to the constant `ai-sdlc.operator/v1`.

The public capability vocabulary is exactly the frozen twelve identifiers:

`system.capabilities`, `project.inspect`, `feature.status`, `operator.inbox`, `operation.start`, `operation.status`, `operation.resume`, `operation.cancel`, `decision.list`, `decision.respond`, `notification.list`, `notification.ack`.

No generic shell, repository mutation, Manifest patch, arbitrary Feature Event, Gate mutation, merge or release operation is present.

### 2.2 Canonical dispatcher/validator

Add a small transport-neutral trusted module, conceptually `scripts/operator_api.py`, that:

1. validates the outer request envelope and exact API version;
2. resolves the requested capability from a frozen trusted capability registry;
3. validates the capability-specific payload;
4. merges client-visible identity with trusted runtime identity without allowing trusted identity fields to be supplied by the client;
5. validates idempotency and expected-revision preconditions required by the capability metadata;
6. consults a bounded backend interface for availability/execution;
7. emits one canonical success or structured-error envelope.

The dispatcher must not call GitHub lifecycle mutation primitives directly merely because the contract exists. Backends are explicit trusted dependencies.

### 2.3 Capability registry and availability provider

Use one code-owned capability registry rather than adapter-local lists. Each capability descriptor contains at least:

- canonical identifier;
- request schema reference;
- response schema reference;
- read/write classification;
- whether idempotency is required;
- whether expected Feature revision is required;
- backend capability key;
- conformance-subset membership.

Availability is resolved by a trusted provider interface, not by Feature-branch request input. A schema being present never implies runtime availability.

For this Feature, capabilities without an implemented trusted backend return `CAPABILITY_UNAVAILABLE` and `system.capabilities` reports them as known but unavailable.

### 2.4 Reusable conformance harness

Add transport-neutral conformance fixtures/assertions under `scripts/validate_operator_api.py` plus fixture data under `tests`/`examples` according to repository convention.

The harness calls an adapter through a minimal test interface such as:

```text
adapter.invoke(canonical_request) -> canonical_response
adapter.identity -> stable adapter identity
adapter.transport_kind -> declared transport boundary
```

The harness owns semantic assertions; adapters own only transport conversion. This prevents each adapter from redefining error/version/identity semantics.

It must be possible to run the same suite against two materially independent adapters later. Adapter evidence records both adapter identity and transport kind so two aliases over one implementation cannot accidentally count as independent release evidence.

## 3. Canonical request envelope

The request envelope is intentionally bounded:

```yaml
api_version: ai-sdlc.operator/v1
request_id: <opaque bounded id>
capability: <one frozen capability id>
target:
  repository: <owner/repo when applicable>
  feature_id: <feature id when applicable>
context:
  operation_id: <optional>
  operation_generation: <optional integer>
  expected_feature_revision: <optional integer>
idempotency_key: <required for semantic writes>
client_identity:
  adapter_id: <authenticated/verified adapter-facing identity input>
  human_principal: <optional represented principal>
payload: <capability-specific object>
```

Trusted service/runtime identity and trusted authorization-policy context are not client-writable fields. They are supplied separately by the trusted invocation context and copied into the internal validated request context.

Unknown top-level fields are rejected.

## 4. Canonical response envelope

Success and failure share correlation fields:

```yaml
api_version: ai-sdlc.operator/v1
request_id: <same request id>
capability: <same capability>
ok: true|false
result: <typed capability result when ok>
error: <typed structured error when !ok>
```

Exactly one of `result` or `error` is present.

Human-readable `message` may be included inside the error object for diagnostics, but control flow is determined only by machine-readable `code` and bounded `details`.

## 5. Error taxonomy

The frozen error vocabulary is represented as an enum and preserved without transport-specific aliases.

Design resolution of Requirement Review MINOR-1:

- an unknown/unrecognized capability identifier is `INVALID_REQUEST`;
- a known canonical capability whose trusted backing implementation is unavailable is `CAPABILITY_UNAVAILABLE`.

This mapping is normative for every adapter and conformance fixture.

Other required codes remain exactly:

`UNSUPPORTED_API_VERSION`, `UNAUTHORIZED`, `POLICY_DENIED`, `STALE_REVISION`, `ALREADY_CLAIMED`, `ALREADY_APPLIED`, `SUPERSEDED_GENERATION`, `CANCELLED_OPERATION`, `EXTERNAL_WAIT`, `NEEDS_USER`, `BLOCKED`, `TRANSIENT_FAILURE`, `INTERNAL_FAILURE`.

Validation ordering is deterministic:

1. malformed outer envelope → `INVALID_REQUEST`;
2. unsupported API version → `UNSUPPORTED_API_VERSION` before semantic hooks;
3. unknown capability → `INVALID_REQUEST`;
4. malformed capability payload/preconditions → `INVALID_REQUEST`;
5. authorization/policy checks when a backend is available → canonical authorization error;
6. known but unavailable backend → `CAPABILITY_UNAVAILABLE`;
7. backend-domain result → mapped canonical result/error.

No raw exception, credential, token, environment value or unrestricted traceback is serialized into `details`.

## 6. Identity and trust boundary

Use two identity layers:

### Client-represented identity

May contain adapter identity and represented human principal metadata allowed by the adapter authentication boundary.

### Trusted invocation identity

Constructed only by trusted runtime/control code and contains service/runtime identity plus trusted authorization-policy reference/context.

The dispatcher creates an internal identity context from both sources but never lets client fields overwrite trusted invocation fields. Attempts to provide reserved trusted fields are rejected as `INVALID_REQUEST` before backend execution.

AI-client adapter identity is never interpreted as human approval, Acceptance evidence, Gate authority or repository authorization by itself.

## 7. Capability-specific contract strategy

Each capability has a dedicated request/response schema rather than one untyped `payload` union owned by adapters.

For capabilities backed by existing safe read primitives during implementation, a bounded backend may be supplied if doing so requires no lifecycle-authority change. Otherwise the capability stays unavailable.

In particular:

- `system.capabilities` must be implemented by this Feature because honest capability discovery is part of the foundation itself;
- `feature.status` may use an existing read-only validated Manifest reader if the implementation Plan confirms no authority expansion;
- `project.inspect` may use the existing validated Project Adapter reader if available and security-equivalent;
- Operation/Decision/Notification capabilities remain unavailable unless a later approved workstream provides the trusted backing interface.

`operator.inbox` must not fabricate empty durable state as proof that the inbox system exists; if no trusted Operation/Decision/Notification stores exist, it reports unavailable.

## 8. Idempotency and expected-revision metadata

The capability registry marks semantic writes as requiring an idempotency key. Contract validation rejects a missing/empty key before backend execution.

Lifecycle-sensitive write capability metadata marks `expected_feature_revision` required where the target Feature state participates in the semantic decision. The foundation validates presence/type only; later backing implementations remain responsible for atomic comparison and the `STALE_REVISION` decision.

The contract layer may provide an in-process deterministic request fingerprint helper for tests, but it must not claim persistent deduplication, semantic-effect reservation, dispatch identity or cross-process exactly-once behavior.

## 9. Backend interface

Define a narrow protocol/interface such as:

```text
availability(capability, trusted_context) -> available + bounded reason
invoke(validated_request, trusted_context) -> canonical domain result
```

Backends are registered only by trusted code/configuration. A target repository or Feature branch cannot provide a module name, function name, URL, shell command or provider selector that becomes a backend implementation.

A default unavailable backend is explicit and side-effect free.

## 10. `system.capabilities` semantics

The result includes:

- supported API versions;
- every known canonical capability;
- availability boolean/status per capability;
- bounded reason code such as `BACKEND_NOT_IMPLEMENTED`, `BACKEND_NOT_CONFIGURED`, or `POLICY_RESTRICTED` where exposing that distinction is safe;
- adapter/client-visible metadata required for negotiation.

It does not expose secret names/values, credentials, repository tokens, policy contents or internal exception details.

Known-but-unavailable capabilities remain listed, which lets clients distinguish contract support from runtime readiness.

## 11. Compatibility and migration

This is additive:

- existing v0.2 Feature Manifest/Event/Persist/Gate schemas are not replaced;
- existing Commander, Issue Comment, gh-aw, Runtime App and cross-repository transports keep working unchanged;
- no existing client is forced through the Operator API;
- no `VERSION` change is made;
- `release/v0.3.0.yaml` is not created;
- the frozen planning manifest remains unresolved for downstream implementation/dogfood blockers.

There is no data migration in this Feature because no durable Operator state store is introduced.

## 12. Security model

Security invariants are enforced at both schema and dispatcher boundaries:

- `additionalProperties: false` on canonical schemas unless a bounded extension object is explicitly required;
- exact capability allowlist from trusted code;
- reserved trusted identity/policy fields are not client writable;
- unsupported API versions and malformed requests fail before semantic hooks;
- no generic code execution/repository mutation capability;
- bounded structured errors with secret-safe details;
- backend registration is trusted-code-owned;
- read backends continue using existing repository/install authorization boundaries.

No canonical operation can directly PASS/WAIVE Gates, modify authoritative Manifest state, merge or release.

## 13. Observability

The contract layer may emit deterministic audit records/log fields containing:

- request id;
- API version;
- capability;
- adapter identity;
- target repository/Feature identifiers when authorized;
- outcome/error code;
- backend availability reason;
- latency/timing if existing logging convention supports it.

It must not log request secrets, credentials, authorization tokens, unrestricted payload bodies or policy contents.

Observability is diagnostic only and never lifecycle truth.

## 14. Failure handling

Fail closed for:

- invalid JSON/schema;
- unsupported version;
- unknown capability;
- missing required idempotency/revision binding;
- client attempt to assert trusted identity/policy context;
- unavailable backend;
- backend exception without a safe canonical mapping.

Unexpected backend exceptions become `INTERNAL_FAILURE` with bounded diagnostic identity, not raw exception data.

Transient errors may become `TRANSIENT_FAILURE` only when the trusted backend explicitly classifies them as transient.

## 15. Deterministic validation strategy

Add tests/validators for at least:

1. every schema is valid JSON Schema 2020-12;
2. exact `ai-sdlc.operator/v1` request/response acceptance;
3. unknown version rejected before a semantic callback counter changes;
4. unknown capability → `INVALID_REQUEST`;
5. known unavailable capability → `CAPABILITY_UNAVAILABLE`;
6. all twelve capability descriptors exist exactly once;
7. no prohibited generic mutation capability exists;
8. additional/unknown request fields rejected;
9. client trusted-identity/policy injection rejected;
10. every semantic write descriptor requires idempotency;
11. lifecycle-sensitive write fixtures require expected revision;
12. secret-like backend exception data is not copied into canonical details;
13. `system.capabilities` lists known-but-unavailable capabilities honestly;
14. the same conformance assertions can run against two fixture adapter implementations with distinct adapter/transport identities;
15. a thin alias over one adapter cannot be counted as materially independent evidence by the release-evidence helper;
16. existing protocol/security validators relevant to changed files remain green.

The final implementation Plan must identify the repository's exact validation commands and any CI workflows that must be green on the candidate head.

## 16. Risks and tradeoffs

### Many schemas vs one generic payload schema

Dedicated capability schemas create more files but prevent adapter-specific weakening and make conformance deterministic. Chosen: dedicated schemas.

### Implement read backends now vs contract-only

Reusing safe existing read primitives may provide useful early behavior, but broadening this Feature into durable Operator state would violate the frozen order. Chosen: implement only foundation-owned capability discovery plus bounded existing read adapters if Design Review/Plan confirms they are authority-neutral; everything else remains explicitly unavailable.

### One registry in code vs manifest-configured capabilities

Target-configurable capability registration would create an authorization/escape surface. Chosen: trusted code-owned frozen registry for v1.

### Persisted idempotency store now vs validation-only

A durable idempotency store belongs with later Operation Store semantics. Chosen: validate idempotency identity now and defer persistent deduplication guarantees.

## 17. Design completion boundary

This Design is complete when it gives Implementation an unambiguous contract for schemas, dispatcher validation order, error taxonomy, identity separation, capability registry/availability, backend interface, conformance harness and negative tests.

It does **not** approve implementation, adapters, durable Operation Store, concurrency/recovery, Decision/Notification persistence, dogfood, publication or v0.3 release readiness. `design-gate` remains pending until a fresh independent Design Reviewer evaluates this artifact against the approved Requirement and frozen Release Spec.
