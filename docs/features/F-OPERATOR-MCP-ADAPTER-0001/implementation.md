# Implementation — F-OPERATOR-MCP-ADAPTER-0001

## Outcome

Implemented the approved read-only MCP stdio adapter over the existing canonical `ai-sdlc.operator/v1` boundary.

This implementation does **not** add semantic write MCP tools, durable Operation/Decision/Notification stores, HTTP transport, lifecycle authority, Gate authority, or v0.3 release publication.

## Implemented runtime

### Dependency

`requirements-dev.txt` now pins:

```text
mcp==2.0.0
```

The implementation uses the official Python MCP SDK on Python 3.12 CI.

### Production MCP server

Added `scripts/operator_mcp.py` with stable evidence identity:

```text
adapter_id: ai-sdlc.mcp.stdio
transport_kind: mcp-stdio
```

Production startup registers exactly seven fixed read-only tools:

```text
ai_sdlc_system_capabilities -> system.capabilities
ai_sdlc_project_inspect     -> project.inspect
ai_sdlc_feature_status      -> feature.status
ai_sdlc_operator_inbox      -> operator.inbox
ai_sdlc_operation_status    -> operation.status
ai_sdlc_decision_list       -> decision.list
ai_sdlc_notification_list   -> notification.list
```

No MCP production tool is registered for:

```text
operation.start
operation.resume
operation.cancel
decision.respond
notification.ack
```

The production `main()` runs stdio and hardcodes the conformance probe disabled. There is no production CLI/environment/config/MCP argument that enables it.

### Canonical translation

All MCP tool handlers construct canonical request envelopes through the shared `invoke_canonical(...)` helper and call the existing `operator_api.dispatch` boundary.

Capability ids are selected by the fixed tool registration and cannot be replaced by client input. Client tool input is limited to the bounded canonical API version, target, and capability payload fields needed by the fixed read tool.

Trusted runtime/service identity comes from a server-owned `TrustedContextProvider`; MCP arguments cannot choose the provider or supply trusted authorization context.

Canonical success/error envelopes are returned as MCP structured content so machine-readable `error.code` values survive the transport.

### Canonical discovery

The MCP `system.capabilities` tool delegates to the canonical registry rather than filtering it to the MCP tool list. Canonical discovery therefore remains complete at 12 known capabilities with bounded availability reasons while the MCP invokable surface remains seven read-only tools.

## Conformance implementation

### Test-only stdio launcher

Added `tests/fixtures/operator_mcp_conformance_server.py`.

It imports the exact supported `operator_mcp.py` server builder and injects deterministic trusted test backends/provider. These fixtures are test-only and do not constitute production Operator durability.

### Bounded conformance probe

The same server builder accepts `enable_conformance_probe=True` only when explicitly constructed by test code. In that mode it registers one reserved tool:

```text
__ai_sdlc_conformance_probe
```

The probe accepts only two closed cases:

```text
unknown_capability
trusted_identity_injection
```

It does not accept arbitrary capability ids, raw canonical envelopes, semantic write selection, repository mutation, Feature Events, Manifest patches, Gate changes, or workflow dispatch.

Unsupported-version and known-unavailable cases use the normal production read tools rather than this probe.

### Real stdio CanonicalAdapter driver

Added `scripts/operator_mcp_conformance.py`.

`McpStdioConformanceAdapter`:

```text
adapter_id: ai-sdlc.mcp.stdio
transport_kind: mcp-stdio
wrapper_depth: 0
```

It launches the same MCP server implementation and communicates through the official MCP stdio client/session. It never delegates to `DirectFixtureAdapter` or `JsonRoundTripFixtureAdapter` and does not directly invoke `operator_api.dispatch` from the driver.

The existing reusable `run_conformance_suite()` therefore exercises the six frozen shared canonical semantics through a genuine MCP stdio transport boundary.

## Deterministic validator

Added `scripts/validate_operator_mcp.py` and wired it into `scripts/validate.py` immediately after the existing canonical Operator API validator.

The validator proves:

- production tool list = exactly seven read-only tools;
- reserved conformance probe is absent from production tool listing;
- conformance-mode listing = seven production tools + exactly one reserved probe;
- no semantic write capability is mapped to an MCP production tool;
- canonical discovery contains all 12 capability ids;
- shared six-capability conformance suite passes over real MCP stdio;
- `project.inspect` succeeds over stdio with a deterministic test backend;
- unsupported version, unknown capability, trusted-field injection, and known-unavailable semantics preserve canonical error handling through MCP;
- MCP adapter identity is preserved at the trusted backend boundary;
- MCP adapter evidence is materially distinct from the direct fixture adapter.

## Files changed for implementation

Implementation/runtime files:

- `requirements-dev.txt`
- `scripts/operator_mcp.py`
- `scripts/operator_mcp_conformance.py`
- `scripts/validate_operator_mcp.py`
- `scripts/validate.py`
- `tests/fixtures/operator_mcp_conformance_server.py`

Lifecycle Requirement/Design/Plan/review/evidence files are separate durable AI-SDLC artifacts and are not additional runtime authority.

## Verified implementation code candidate

The implementation code candidate tested by the full PR validation suite was:

```text
856dab59e05884fe652ee9f45e7fc8850239e110
```

GitHub's PR merge test commit for that candidate was:

```text
4748fc3f58f5a2603842b603271ffd7b118cb87e
```

The tested candidate included the MCP runtime, conformance driver/fixture, validator, dependency pin, and `scripts/validate.py` integration.

## CI results

### Validate AI-SDLC protocol

Run:

```text
31351622901
```

Result: SUCCESS.

The `validate` job installed `mcp==2.0.0` on Python 3.12.13 and completed the full repository validation sequence successfully.

The MCP validator printed:

```text
Operator MCP validation passed
- adapter_id: ai-sdlc.mcp.stdio
- transport_kind: mcp-stdio
- production_tools: 7 read-only
- canonical_registry: 12 capabilities
- conformance_subset: 6 over real MCP stdio
- conformance probe: test-only, absent from production tool list
- semantic writes: no MCP tool registration
```

The same run also retained the canonical Operator validator result with 12 capabilities, six shared semantics across the two original fixture adapters, and alias/thin-wrapper rejection.

### Required PR Gate

Run:

```text
31351622883
```

Result: SUCCESS.

Jobs:

- `protocol-validation`: SUCCESS
- `cross-repo-control-validation`: SUCCESS
- `required-pr-gate`: SUCCESS

### Validate Public Runtime Distribution

Run:

```text
31351622914
```

Result: SUCCESS.

## Known deferred behavior

This Feature does not implement the later v0.3 durable backing workstreams. In normal/default server construction, read capabilities without a trusted backend may correctly return canonical `CAPABILITY_UNAVAILABLE`; only canonical `system.capabilities` is inherently available from the canonical registry foundation.

This is an intentional release boundary, not an implementation defect for this Feature.

## Implementation completion claim

Developer implementation is complete against approved Requirement/Design/Plan and is ready for independent Code Review after the lifecycle `IMPL-DONE` Event is trusted-persisted.

This document and Developer CI evidence are **not** Code Review approval, QA approval, Product Acceptance, or overall v0.3 release-readiness evidence.
