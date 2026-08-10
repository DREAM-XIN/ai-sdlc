# Code Review — F-OPERATOR-MCP-ADAPTER-0001

## Role and scope

Independent Code Reviewer review of PR #211 for the approved `requirement-v1`, `design-v1`, `plan-v1`, and `implementation-v1` of the MCP read-only Operator adapter.

Review candidate runtime code is the implementation tree validated at commit `856dab59e05884fe652ee9f45e7fc8850239e110`. The current lifecycle head contains only subsequent Feature documentation, Evidence, Feature Events, and trusted Manifest persistence changes; comparison from that validated implementation tree to the review lifecycle head showed no runtime/test/dependency changes.

## Decision

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0
- SUGGESTION: 0

## Findings

No blocking or non-blocking code defects were identified within the approved Feature scope.

## Independent checks

### Production MCP surface

`scripts/operator_mcp.py` registers exactly seven fixed read-only tools:

- `system.capabilities`
- `project.inspect`
- `feature.status`
- `operator.inbox`
- `operation.status`
- `decision.list`
- `notification.list`

No semantic write capability is registered. Client input cannot override the fixed capability mapping, choose a backend, inject a raw Feature Event/Manifest patch, select a Gate operation, or invoke shell/workflow/repository mutation behavior.

### Conformance probe isolation

The conformance probe is registered only when `build_server(..., enable_conformance_probe=True)` is called. Production `main()` hardcodes `enable_conformance_probe=False` and exposes no CLI/environment/config/MCP request switch that can enable it.

The test-only launcher under `tests/fixtures/operator_mcp_conformance_server.py` is the only reviewed caller enabling the probe. The probe accepts only the closed enum cases `unknown_capability` and `trusted_identity_injection`; it does not accept arbitrary capability ids or raw canonical envelopes.

### Trusted identity boundary

Production tool arguments do not accept `trusted_identity` or trusted authorization context. Trusted context is sourced from the server-owned `TrustedContextProvider`; MCP client input cannot replace it. The negative conformance probe deliberately injects a fixed invalid top-level `trusted_identity` only to prove canonical envelope rejection.

### Real transport conformance

`McpStdioConformanceAdapter` launches the shipped MCP server implementation as a subprocess and communicates through the official MCP stdio client/session. It does not call `operator_api.dispatch` directly and does not delegate to the existing direct or JSON-roundtrip fixtures.

The reusable canonical conformance suite therefore exercises a materially independent `mcp-stdio` adapter boundary.

### Discovery versus invocation

Canonical `system.capabilities` remains registry-complete with 12 known capabilities and bounded availability semantics while the production MCP tool list remains restricted to seven read-only tools. This preserves the Requirement Review clarification that discovery and MCP invocation exposure are distinct surfaces.

### Dependency and compatibility

The Feature pins the stable `mcp==2.0.0` dependency and the Python 3.12 PR validation installed it successfully. Existing protocol, lifecycle, cross-repository control, public-runtime distribution, gh-aw, and v0.2 release-readiness regressions remained green on the validated implementation tree.

## CI evidence considered

On implementation code commit `856dab59e05884fe652ee9f45e7fc8850239e110`:

- Validate AI-SDLC protocol run `31351622901`: SUCCESS
- Required PR Gate run `31351622883`: SUCCESS
- Validate Public Runtime Distribution run `31351622914`: SUCCESS

The protocol logs explicitly reported:

- `Operator MCP validation passed`
- adapter `ai-sdlc.mcp.stdio`
- transport `mcp-stdio`
- 7 production read-only tools
- 12 canonical registry capabilities
- 6 conformance-subset capabilities over real MCP stdio
- conformance probe absent from production tool list
- no semantic-write MCP registration

The later lifecycle-only commits were independently compared against this validated implementation tree and introduced no runtime, test, or dependency changes.

## Scope boundary

This PASS approves only `F-OPERATOR-MCP-ADAPTER-0001` implementation against its approved Requirement/Design. It does not claim durable Operation Store, semantic write support, a second supported AI-client adapter, unattended orchestration, final v0.3 dogfood, publication readiness, or v0.3 release readiness.
