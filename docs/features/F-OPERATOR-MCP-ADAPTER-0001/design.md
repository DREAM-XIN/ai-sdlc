# Design — F-OPERATOR-MCP-ADAPTER-0001

## 1. Design objective

Implement one genuine supported MCP AI-client adapter over the existing `ai-sdlc.operator/v1` canonical API without moving lifecycle authority, trusted identity, authorization, Feature mutation, or Gate authority into MCP.

This Feature provides a read-only MCP transport surface only. It does not implement the durable Operation Store, write-capable release slice, Decision/Notification durability, or unattended orchestration.

## 2. Normative inputs

- approved `requirement-v1`;
- `evidence-requirement-review-v1`;
- frozen v0.3 Release Spec;
- existing `scripts/operator_api.py` canonical dispatch boundary;
- existing `scripts/operator_conformance.py` reusable transport-neutral conformance harness.

No canonical capability, schema, error code, or authority rule is redefined here.

## 3. Architecture

Add a supported MCP adapter composed of four bounded layers:

```text
MCP host/client
    |
    | MCP stdio protocol
    v
MCP server / tool registry
    |
    | adapter-owned translation only
    v
Canonical request envelope
    |
    v
scripts/operator_api.py::dispatch
    |
    +--> trusted_context provider (server-owned)
    +--> canonical backends (trusted, injected)
```

Planned implementation units:

- `scripts/operator_mcp.py`
  - supported MCP server entrypoint;
  - read-only MCP tool registry;
  - canonical request/response translation;
  - stable adapter identity;
  - trusted-context provider boundary;
  - stdio run entrypoint.
- `scripts/operator_mcp_conformance.py` or an equivalent focused validator
  - launches/exercises the same supported MCP server implementation through a real MCP protocol/client boundary;
  - adapts the result to the reusable `CanonicalAdapter` conformance interface;
  - supplies deterministic trusted test backends only for test execution.
- `scripts/validate_operator_mcp.py`
  - deterministic adapter validation and negative tests.
- dependency declaration update
  - use the official Python MCP SDK stable release line available at implementation time;
  - pin it with an explicit bounded/exact repository-appropriate constraint rather than an unbounded dependency;
  - no alpha/pre-release dependency without separate justification.

## 4. Transport selection

The first supported MCP transport is **stdio**.

Rationale:

- it is a real MCP protocol transport rather than an in-process fixture call;
- it is suitable for local AI-client host integration;
- deterministic CI can launch the same server implementation as a subprocess without network access or external credentials;
- it avoids introducing HTTP listener/authentication concerns that are outside this read-only adapter Feature.

Stable adapter evidence:

```text
adapter_id: ai-sdlc.mcp.stdio
transport_kind: mcp-stdio
```

These identifiers are distinct from `fixture.direct` / `in-process-object` and `fixture.json-roundtrip` / `json-round-trip`.

## 5. MCP-visible tool surface

Register exactly seven read-only MCP tools mapping 1:1 to canonical capabilities:

```text
ai_sdlc_system_capabilities -> system.capabilities
ai_sdlc_project_inspect     -> project.inspect
ai_sdlc_feature_status      -> feature.status
ai_sdlc_operator_inbox      -> operator.inbox
ai_sdlc_operation_status    -> operation.status
ai_sdlc_decision_list       -> decision.list
ai_sdlc_notification_list   -> notification.list
```

Tool names are transport-level names only; canonical capability ids remain unchanged inside canonical envelopes.

Do **not** register MCP tools for:

```text
operation.start
operation.resume
operation.cancel
decision.respond
notification.ack
```

Unknown MCP tool names fail at the MCP protocol/tool layer and never reach canonical dispatch.

## 6. Requirement Review MINOR resolution: discovery versus invocation

The Design freezes the following distinction:

### Canonical discovery

`system.capabilities` remains registry-complete. Its canonical response must preserve the complete 12-capability registry and bounded `available` / `reason` semantics from `scripts/operator_api.py`.

Known write capabilities therefore remain discoverable as canonical capability identifiers. In this read-only adapter they are expected to be unavailable unless a trusted backend exists outside this Feature, and the MCP adapter must not rewrite/filter the canonical discovery result to pretend they do not exist.

### MCP invocation exposure

The MCP tool registry is intentionally narrower: only the seven read-only tools in section 5 are invokable.

A write capability being present in canonical discovery does **not** imply that this MCP adapter exposes a write tool for it.

Tests must independently prove:

1. `system.capabilities` returns the complete canonical registry;
2. the MCP tool list contains only the seven approved read-only tools;
3. no write-capability MCP tool can be invoked because none is registered.

This resolves Requirement Review MINOR-1 without changing the Requirement.

## 7. Canonical request construction

Each MCP tool handler constructs a canonical request envelope and calls `operator_api.dispatch`.

Server-owned fields:

- `api_version = ai-sdlc.operator/v1`;
- `client_identity.adapter_id = ai-sdlc.mcp.stdio`;
- canonical capability id selected by the registered tool mapping;
- request id derived/generated by adapter runtime, never accepted as trusted authority;
- trusted invocation context supplied separately from MCP arguments.

Client/tool arguments may provide only the target identifiers and capability payload fields permitted by the canonical schemas.

The adapter must not accept MCP arguments named or shaped as trusted runtime/service identity, authorization context, raw canonical envelope replacement, Feature Event, Manifest patch, Gate mutation, shell command, workflow dispatch, or arbitrary backend selector.

## 8. Trusted context boundary

Introduce a narrow server-owned provider abstraction conceptually equivalent to:

```python
class TrustedContextProvider(Protocol):
    def for_request(self, mcp_request_metadata, target) -> dict: ...
```

Rules:

- the provider is constructed by trusted server startup code, not from tool arguments;
- returned `trusted_identity` must satisfy the existing canonical identity schema;
- MCP `client_id` / human/session metadata may be recorded as client-facing identity evidence, but cannot become `trusted_identity` merely because the client supplied it;
- authorization policy context comes only from the trusted provider/runtime;
- requested repository/Feature target remains subject to trusted provider/backend authorization and canonical validation;
- invalid trusted provider output fails closed as canonical/internal failure rather than falling back to client identity.

For deterministic tests, a fixed test-only provider may be injected. It must be visibly test-only and cannot be represented as production authorization backing.

## 9. Backend boundary and honest availability

The MCP adapter does not implement new durable Operator backends.

Production/default construction delegates to the existing canonical backend registry semantics. If a read capability has no trusted backing, canonical dispatch returns `CAPABILITY_UNAVAILABLE` with the bounded availability reason.

Conformance tests may inject deterministic semantic test backends for:

- `project.inspect`;
- `feature.status`;
- `operator.inbox`;
- `operation.status`;
- `decision.list`;
- `notification.list`.

Those backends live in test/validator scope and are never registered as production durable stores.

## 10. MCP response and error translation

Tool results preserve the canonical response envelope as machine-readable structured content.

Success path:

```text
MCP tool result
  structured canonical envelope
    ok: true
    api_version
    request_id
    capability
    result
```

Canonical error path:

```text
MCP tool result
  structured canonical envelope
    ok: false
    api_version
    request_id
    capability
    error.code
    error.message? / bounded details?
```

The adapter does not collapse canonical failures into free-form text-only errors. Human-readable MCP text may accompany the structured result, but clients must be able to classify the failure by canonical `error.code` alone.

MCP protocol failures that occur before a canonical tool handler is selected are transport errors and must not be mislabeled as canonical lifecycle errors.

## 11. Version behavior

The supported MCP tool handlers always construct canonical `ai-sdlc.operator/v1` requests in normal operation.

The conformance adapter/test harness must also provide a controlled way to exercise unsupported canonical version input through the same MCP translation path, without exposing arbitrary canonical envelope override to production MCP callers. This may be implemented as test injection at the adapter boundary rather than a production tool argument.

Expected semantic result: `UNSUPPORTED_API_VERSION` and no backend invocation.

## 12. Conformance strategy

Provide a `CanonicalAdapter`-compatible test driver that crosses the actual MCP protocol boundary.

Conceptual flow:

```text
run_conformance_suite(McpStdioConformanceAdapter)
    -> official MCP client/session
    -> launch same operator_mcp.py server over stdio
    -> MCP tool call
    -> canonical dispatch
    -> deterministic trusted test backend
    -> MCP structured result
    -> canonical response returned to harness
```

The conformance driver is test glue; the server under test is the exact supported MCP server implementation shipped by this Feature.

The driver must report:

```text
adapter_id = ai-sdlc.mcp.stdio
transport_kind = mcp-stdio
wrapper_depth = 0
```

and must not delegate to `DirectFixtureAdapter` or `JsonRoundTripFixtureAdapter`.

## 13. Deterministic validation matrix

Automated tests/validator must cover at least:

1. MCP server initializes and lists exactly seven read-only tools;
2. no canonical write capability has an MCP tool registration;
3. `system.capabilities` returns all 12 canonical registry entries with bounded availability reasons;
4. shared six-capability conformance subset passes through MCP when deterministic test backends are injected;
5. `project.inspect` succeeds through MCP with deterministic test backend;
6. unsupported canonical version -> `UNSUPPORTED_API_VERSION`, no backend call;
7. unknown canonical capability path in controlled conformance injection -> `INVALID_REQUEST`;
8. known unavailable read capability -> `CAPABILITY_UNAVAILABLE`;
9. adapter identity arrives at canonical backend unchanged;
10. client attempts to supply `trusted_identity`, authorization context, backend selectors, Event/Manifest/Gate/shell/workflow fields are rejected or absent from tool schemas;
11. structured canonical errors survive MCP transport with stable machine-readable codes;
12. same supported server implementation is exercised over stdio, not a separate fixture facade;
13. alias/thin-wrapper fixture remains rejected as independent adapter evidence;
14. existing `validate_operator_api.py` and canonical fixture conformance tests continue to pass.

## 14. Security boundary

The MCP server is read-only at the transport surface for this Feature.

It must not:

- spawn arbitrary commands from MCP arguments;
- write repository files;
- dispatch arbitrary workflows;
- accept raw Feature Events;
- update Feature Manifests;
- approve/pass Gates;
- expose generic canonical capability invocation by arbitrary capability id;
- expose a generic shell/HTTP/repository escape hatch.

The only subprocess behavior allowed by deterministic tests is the test harness launching the fixed supported MCP server entrypoint through stdio.

## 15. Dependency and runtime packaging

Use the official Python MCP SDK rather than implementing the wire protocol manually.

Implementation must:

- add a bounded/pinned dependency consistent with repository dependency policy;
- stay on the current stable SDK release line, not alpha/pre-release, unless a separately reviewed compatibility reason exists;
- keep runtime/test dependency scope explicit;
- preserve Python 3.12 CI compatibility used by the repository;
- avoid adding a web framework or HTTP server solely for this Feature because stdio is the selected transport.

## 16. Backward compatibility

Existing canonical API files, schemas, fixture adapter identities, Feature Event/Persist behavior, gh-aw routing, and v0.2 lifecycle paths remain unchanged.

No change to:

- `VERSION`;
- final `release/v0.3.0.yaml`;
- Operation Store layout;
- write-capability semantics;
- existing Gate authority.

If implementation discovers that canonical API changes are required, that is a scope boundary and must return to reviewed design/requirement rather than silently changing the upstream contract.

## 17. Implementation boundaries

Allowed implementation changes are limited to:

- new MCP adapter/server files;
- focused tests/validator/conformance glue;
- bounded dependency declaration;
- Feature documentation/evidence;
- minimal validation-suite wiring needed to run MCP tests.

Out of scope:

- production durable read/write backends beyond what already exists;
- any canonical semantic write tool;
- HTTP/SSE/Streamable-HTTP deployment;
- authentication product surface;
- Operation Store/dispatch/recovery;
- Decision/Notification persistence;
- autonomous vertical-loop orchestration;
- v0.3 release publication.

## 18. Design completion criteria

The Design is implementable when an independent Design Reviewer can verify that:

- MCP crosses a real stdio protocol boundary;
- canonical API remains the semantic authority boundary;
- complete canonical discovery and bounded MCP invocation exposure are both preserved;
- trusted identity cannot be forged from MCP input;
- structured errors remain machine-readable;
- no write/lifecycle mutation surface is exposed;
- deterministic conformance uses the same supported server implementation;
- dependency/runtime choices do not broaden scope into an HTTP service or arbitrary execution surface.
