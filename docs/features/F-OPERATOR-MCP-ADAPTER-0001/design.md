# Design — F-OPERATOR-MCP-ADAPTER-0001

## 1. Objective and authority boundary

Implement one genuine supported MCP AI-client adapter over the existing `ai-sdlc.operator/v1` canonical API. MCP is transport only: canonical dispatch, trusted runtime context, backend availability, Feature Event/Persist, protected branches, independent roles, and Gate authority remain outside the MCP client/session.

This Feature is read-only. It does not implement the durable Operation Store, semantic write release slice, Decision/Notification durability, unattended orchestration, or v0.3 release publication.

Normative inputs are approved `requirement-v1`, `evidence-requirement-review-v1`, the frozen v0.3 Release Spec, `scripts/operator_api.py`, and `scripts/operator_conformance.py`.

## 2. Component model

```text
MCP host/client
    |
    | MCP stdio protocol
    v
operator_mcp.py MCP server
    |
    | fixed tool -> canonical capability translation
    v
shared canonical invocation helper
    |
    v
scripts/operator_api.py::dispatch
    |
    +--> server-owned TrustedContextProvider
    +--> injected trusted canonical backends
```

Planned implementation units:

- `scripts/operator_mcp.py`
  - supported MCP server builder and production stdio entrypoint;
  - fixed read-only MCP tool registry;
  - shared MCP-to-canonical translation helper;
  - stable adapter identity;
  - trusted-context provider boundary.
- `scripts/operator_mcp_conformance.py`
  - `CanonicalAdapter`-compatible test driver;
  - real MCP client/session over stdio;
  - deterministic mapping from canonical conformance requests to MCP calls.
- `tests/fixtures/operator_mcp_conformance_server.py` or equivalent test-only launcher
  - imports the same server builder/translation implementation;
  - enables the bounded conformance probe described below;
  - is never the production entrypoint.
- `scripts/validate_operator_mcp.py`
  - deterministic protocol/security/conformance validator.
- dependency declaration
  - official Python MCP SDK on the stable release line available at implementation time;
  - explicit bounded/pinned repository-appropriate version constraint;
  - no alpha/pre-release dependency without separate reviewed justification.

## 3. Transport and supported adapter identity

The supported transport is **MCP stdio**.

```text
adapter_id: ai-sdlc.mcp.stdio
transport_kind: mcp-stdio
```

Stdio is a real protocol boundary and can be exercised deterministically by launching the same server implementation as a local subprocess without network access or production credentials. HTTP/SSE/Streamable-HTTP deployment is out of scope for this Feature.

The adapter identity and transport kind are materially distinct from:

- `fixture.direct` / `in-process-object`;
- `fixture.json-roundtrip` / `json-round-trip`.

## 4. Production MCP tool surface

Normal production server construction registers exactly seven read-only tools:

```text
ai_sdlc_system_capabilities -> system.capabilities
ai_sdlc_project_inspect     -> project.inspect
ai_sdlc_feature_status      -> feature.status
ai_sdlc_operator_inbox      -> operator.inbox
ai_sdlc_operation_status    -> operation.status
ai_sdlc_decision_list       -> decision.list
ai_sdlc_notification_list   -> notification.list
```

It never registers tools for:

```text
operation.start
operation.resume
operation.cancel
decision.respond
notification.ack
```

It also never registers a generic production tool accepting an arbitrary canonical capability id, raw canonical envelope, Feature Event, Manifest patch, shell command, repository write, Gate mutation, workflow dispatch, or backend selector.

Unknown MCP tool names are MCP transport/tool-selection errors and never become semantic lifecycle operations.

## 5. Requirement Review MINOR resolution: discovery != invocation exposure

The Design freezes two distinct surfaces.

### 5.1 Canonical discovery

`system.capabilities` remains registry-complete. The canonical response preserves all 12 known capability identifiers and bounded `available` / `reason` semantics from `scripts/operator_api.py`.

Known write capabilities therefore remain discoverable as canonical capabilities even though this read-only MCP adapter does not expose write tools.

### 5.2 MCP invocation

Only the seven read-only tools in section 4 are invokable in production MCP mode.

Tests must independently prove:

1. `system.capabilities` returns all 12 canonical registry entries;
2. normal MCP tool listing contains exactly the seven approved read-only tools;
3. none of the five canonical semantic writes is registered as an MCP tool.

This fully resolves Requirement Review MINOR-1.

## 6. Production tool input and canonical request construction

Each production tool accepts only the bounded data necessary to build its fixed canonical capability request:

```text
api_version   # optional client-requested canonical version; defaults to ai-sdlc.operator/v1
target        # canonical target object constrained by schema
payload       # capability-specific canonical payload
```

The capability id is selected by the registered tool and cannot be overridden by client input.

The server constructs:

```text
api_version = supplied version or ai-sdlc.operator/v1
request_id = server-generated/correlated MCP request id
capability = fixed mapping for selected MCP tool
client_identity.adapter_id = ai-sdlc.mcp.stdio
target = validated client target input
payload = validated capability payload
trusted_context = obtained separately from TrustedContextProvider
```

Allowing `api_version` as a bounded, non-authoritative tool argument provides real adapter version negotiation: an unsupported value crosses MCP stdio and canonical dispatch and must return `UNSUPPORTED_API_VERSION` without backend semantic work.

Production tools do not accept `trusted_identity`, trusted authorization context, raw envelope replacement, or arbitrary capability override.

## 7. Shared translation implementation

All production tools and the conformance-only probe call one shared internal helper, conceptually:

```python
def invoke_canonical(*, capability, api_version, target, payload,
                     trusted_context, client_identity,
                     extra_envelope_fields=None):
    ...
    return operator_api.dispatch(canonical_request, ...)
```

Rules:

- normal production handlers pass a fixed read capability and no `extra_envelope_fields`;
- normal handlers cannot select write capabilities;
- the helper is the only MCP-to-canonical envelope construction path;
- response translation to MCP structured content is also shared;
- the conformance path described in section 11 uses this exact helper rather than calling `operator_api.dispatch` directly from the test driver.

## 8. Trusted context provider

Introduce a server-owned boundary conceptually equivalent to:

```python
class TrustedContextProvider(Protocol):
    def for_request(self, mcp_request_metadata, target) -> dict: ...
```

The provider is constructed by trusted server startup code. Tool arguments cannot choose or replace it.

Rules:

- returned `trusted_identity` must satisfy the existing canonical identity schema;
- MCP client/session metadata may contribute client-facing identity evidence but never becomes trusted runtime identity solely because the client supplied it;
- authorization policy context comes only from the trusted provider/runtime;
- repository/Feature target remains subject to trusted provider/backend authorization and canonical validation;
- invalid trusted provider output fails closed; there is no fallback to client-supplied trusted identity.

Deterministic tests may inject a fixed test-only provider. That provider is never represented as production authorization backing.

## 9. Backend boundary and honest availability

This Feature does not add durable production Operator stores.

Production/default construction delegates to the existing canonical backend registry semantics. If a read capability has no trusted backing, canonical dispatch returns `CAPABILITY_UNAVAILABLE` with the bounded canonical availability reason.

Conformance may inject deterministic trusted test backends for:

- `project.inspect`;
- `feature.status`;
- `operator.inbox`;
- `operation.status`;
- `decision.list`;
- `notification.list`.

Those backends are test-only and cannot be represented as production durable state.

## 10. MCP response/error translation

Every selected MCP tool returns the canonical response envelope as machine-readable MCP structured content.

Successful result:

```text
ok: true
api_version
request_id
capability
result
```

Canonical failure:

```text
ok: false
api_version
request_id
capability
error.code
error.message? / bounded details?
```

Human-readable text may accompany structured content, but error classification must never depend on parsing free-form text.

MCP protocol failures that happen before any adapter tool handler is selected remain MCP transport errors and are not mislabeled as canonical lifecycle errors.

## 11. Bounded conformance ingress — Design remediation v1

This section resolves `evidence-design-review-v1` MAJOR-1.

### 11.1 Two server construction modes, one implementation

`operator_mcp.py` exposes an internal/testable builder conceptually:

```python
build_server(*, trusted_context_provider, backends,
             enable_conformance_probe: bool = False)
```

The **production `main()` hardcodes `enable_conformance_probe=False`**. There is no production CLI flag, MCP argument, environment-variable switch, config file field, or runtime request that can turn it on.

The only code allowed to call `build_server(..., enable_conformance_probe=True)` is the test/conformance launcher under the repository test boundary. That launcher imports the same `operator_mcp.py` server builder, tool handlers, canonical invocation helper, response translator, identity logic, and stdio runtime.

Therefore:

- normal startup can never accidentally expose the conformance tool;
- conformance still crosses the same MCP stdio implementation and translation code;
- there is no second fixture adapter implementation.

### 11.2 Conformance-only diagnostic tool

When and only when `enable_conformance_probe=True`, register one additional reserved tool:

```text
__ai_sdlc_conformance_probe
```

Its input is **not** a raw canonical envelope and does **not** accept arbitrary capability ids. It accepts exactly one closed enum:

```text
case = unknown_capability | trusted_identity_injection
```

The two cases construct fixed, read-only/non-side-effect canonical requests through the shared `invoke_canonical` helper:

- `unknown_capability`
  - fixed capability: `not.real`;
  - fixed empty payload/fixture target;
  - expected result: `INVALID_REQUEST`.
- `trusted_identity_injection`
  - fixed base capability: `feature.status`;
  - injects a fixed client-controlled `trusted_identity` field into the canonical envelope through `extra_envelope_fields`;
  - expected result: canonical envelope validation rejects it as `INVALID_REQUEST` before backend invocation.

The probe cannot select a known write capability, cannot carry caller-provided payload/envelope fragments, cannot access repository mutation APIs, and cannot dispatch semantic work.

### 11.3 Unsupported version through the production tool path

Unsupported-version evidence does **not** use the diagnostic probe. The conformance client calls the normal production `ai_sdlc_feature_status` tool over stdio with:

```text
api_version = ai-sdlc.operator/v999
```

The fixed capability still remains `feature.status`; canonical dispatch returns `UNSUPPORTED_API_VERSION` before backend work.

This proves real version negotiation through the actual production MCP tool translation path without a generic capability escape hatch.

### 11.4 Known unavailable capability through the production tool path

Known-unavailable evidence calls a normal read tool (for example `ai_sdlc_project_inspect`) with its trusted backend intentionally absent. The result must be canonical `CAPABILITY_UNAVAILABLE` with a bounded reason.

### 11.5 Structured error preservation

All negative responses — including those reached through the conformance-only diagnostic ingress — return through the exact same MCP structured-response translator as normal tools. The conformance client must recover and assert `error.code` from structured content.

## 12. Mapping the reusable CanonicalAdapter harness to MCP

`McpStdioConformanceAdapter` implements the existing `CanonicalAdapter` protocol and has:

```text
adapter_id = ai-sdlc.mcp.stdio
transport_kind = mcp-stdio
wrapper_depth = 0
```

It launches the test-only conformance server launcher as a subprocess and communicates only through an official MCP stdio client/session.

Deterministic request mapping:

- supported six-capability read subset -> corresponding normal MCP production tool;
- `project.inspect` -> normal production MCP tool;
- unsupported version for `feature.status` -> normal `ai_sdlc_feature_status` with supplied unsupported `api_version`;
- canonical unknown capability `not.real` -> `__ai_sdlc_conformance_probe(case="unknown_capability")`;
- trusted-identity injection -> `__ai_sdlc_conformance_probe(case="trusted_identity_injection")`;
- known unavailable capability -> corresponding normal production MCP tool with backend omitted.

The driver never calls `operator_api.dispatch` directly and never delegates to `DirectFixtureAdapter` or `JsonRoundTripFixtureAdapter`.

The server process under test is built from the same supported `operator_mcp.py` implementation; conformance mode changes only registration of the single closed diagnostic tool.

## 13. Deterministic validation matrix

Automated validation must prove at least:

1. normal production server lists exactly seven read-only tools;
2. normal production server does not list `__ai_sdlc_conformance_probe`;
3. conformance test construction lists the seven normal tools plus exactly the one reserved diagnostic tool;
4. no canonical semantic write capability has an MCP production tool registration;
5. `system.capabilities` returns all 12 canonical registry entries and bounded availability reasons;
6. shared six-capability conformance subset passes through real MCP stdio with deterministic trusted test backends;
7. `project.inspect` succeeds through normal MCP when its deterministic backend is supplied;
8. unsupported version crosses a normal production tool and returns `UNSUPPORTED_API_VERSION` with zero backend invocation;
9. unknown canonical capability crosses MCP via the fixed conformance probe and returns `INVALID_REQUEST`;
10. fixed trusted-identity injection crosses MCP via the probe and returns `INVALID_REQUEST` before backend invocation;
11. known unavailable read capability crosses a normal production tool and returns `CAPABILITY_UNAVAILABLE`;
12. canonical structured error codes survive MCP response translation unchanged;
13. adapter identity arrives at canonical backend unchanged;
14. normal production MCP input has no raw envelope/capability/backend/Event/Manifest/Gate/shell/workflow escape hatch;
15. same `operator_mcp.py` server builder/translation code is used by normal and conformance server construction;
16. conformance adapter has no fixture delegate and passes material-independence evidence;
17. existing `validate_operator_api.py` and fixture conformance tests continue to pass.

## 14. Security invariants

The MCP adapter must not:

- spawn arbitrary commands from MCP input;
- write repository files;
- dispatch arbitrary workflows;
- accept raw Feature Events or Manifest patches;
- approve/pass Gates;
- expose a generic production canonical invocation tool;
- expose any write-capability MCP tool;
- allow MCP input to select trusted identity, authorization context, backend implementation, or server mode;
- allow production startup to enable the conformance probe.

The conformance launcher may spawn only the fixed supported MCP server implementation for local stdio testing.

## 15. Dependency/runtime packaging

Use the official Python MCP SDK rather than implementing the wire protocol manually.

Implementation must:

- declare a bounded/pinned stable SDK dependency consistent with repository policy;
- preserve Python 3.12 CI compatibility;
- avoid alpha/pre-release SDKs unless separately reviewed;
- avoid adding HTTP/web-server dependencies solely for this Feature;
- keep conformance/test-only dependencies and launchers visibly separated from production runtime entrypoints.

## 16. Backward compatibility and scope

Existing canonical API schemas, fixture adapter identities, v0.2 lifecycle behavior, Feature Event/Persist authority, gh-aw routing, protected-branch controls, and Gate authority remain unchanged.

No change to:

- `VERSION`;
- final `release/v0.3.0.yaml`;
- Operation Store layout;
- semantic write capability behavior;
- durable Decision/Notification backing;
- autonomous vertical-loop orchestration.

Allowed implementation changes are limited to MCP adapter/server code, focused conformance/validator code, bounded dependency declaration, minimal validation-suite wiring, and Feature documentation/evidence.

If implementation discovers a required incompatible canonical API change, it must return to reviewed design/requirement rather than silently modifying upstream semantics.

## 17. Design completion criteria

The Design is ready for implementation only when independent re-review verifies that:

- stdio is a real MCP protocol boundary;
- production MCP exposes exactly seven read-only tools;
- canonical discovery still reports the complete registry;
- Requirement Review MINOR-1 remains resolved;
- every mandatory negative/version/error case crosses MCP stdio and the same shared translation code;
- the conformance-only probe is closed, read-only, test-scoped, and impossible to enable from normal production startup;
- trusted identity cannot be forged from MCP input;
- structured canonical errors remain machine-readable;
- no semantic write/lifecycle mutation surface is introduced;
- conformance uses the same supported server implementation rather than a fixture facade.
