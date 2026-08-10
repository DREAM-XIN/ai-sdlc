# Acceptance — F-OPERATOR-MCP-ADAPTER-0001

## Role

Independent Product / Acceptance.

## Decision

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0

## Accepted product outcome

The Feature delivers one genuine supported AI-client adapter over `ai-sdlc.operator/v1` using MCP stdio, bounded to the approved read-only Operator surface.

Accepted behavior:

- supported adapter identity: `ai-sdlc.mcp.stdio`;
- real transport: `mcp-stdio`;
- exactly seven production read-only MCP tools;
- no MCP tools for the five canonical semantic write capabilities;
- registry-complete canonical `system.capabilities` discovery with all 12 canonical capabilities and honest availability semantics;
- server-owned trusted identity/authorization boundary that cannot be replaced by MCP client input;
- structured canonical success/error envelopes preserved over MCP;
- reusable canonical conformance coverage exercised through real MCP stdio;
- bounded test-only conformance ingress that is absent from production startup and cannot be enabled by production CLI/environment/config/request input;
- stable pinned MCP SDK dependency compatible with the repository's Python 3.12 validation environment;
- no regression to existing AI-SDLC protocol, lifecycle, cross-repository control, public runtime distribution, gh-aw behavior, or v0.2 release baseline.

## Evidence considered

Product Acceptance considered the approved Requirement/Design, independent Code Review PASS, and independent Verification QA PASS.

The independently exercised runtime candidate `856dab59e05884fe652ee9f45e7fc8850239e110` passed:

- Validate AI-SDLC protocol run `31351622901`;
- Required PR Gate run `31351622883`;
- Validate Public Runtime Distribution run `31351622914`.

Subsequent lifecycle commits were compared against that runtime candidate and introduced no runtime/test/dependency changes.

## Requirement fit

The Requirement Review clarification is satisfied: the MCP invocation surface remains read-only while canonical discovery remains complete rather than filtering known write capabilities out of `system.capabilities`.

The Design Review remediation is satisfied: negative/version/security conformance cases cross the actual MCP stdio/shared translation implementation without exposing a generic production invocation escape hatch.

## Explicit non-claims

This Feature acceptance does **not** mean AI-SDLC v0.3 is release-ready.

Still outside this Feature are, among other downstream v0.3 workstreams:

- durable Operation Store / journal and recovery/concurrency behavior;
- semantic write capability release-slice backing;
- durable Decision/Notification persistence;
- additional supported AI-client adapter evidence where required by the frozen Release Spec;
- unattended vertical-loop dogfood;
- final security/publication/release readiness work;
- final v0.3 release decision and publication artifacts.

Feature-level `release-gate: PASS` therefore means only that `F-OPERATOR-MCP-ADAPTER-0001` is accepted within its approved scope.
