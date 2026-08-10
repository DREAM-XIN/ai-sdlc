# Design Review — F-OPERATOR-MCP-ADAPTER-0001

## Review context

- Role: independent Design Reviewer
- Feature: `F-OPERATOR-MCP-ADAPTER-0001`
- Issue: #210
- Reviewed Requirement: `requirement-v1` (approved)
- Reviewed Requirement Review: `evidence-requirement-review-v1`
- Reviewed Design: `design-v1` (draft)
- Authoritative review-start state: revision `7`, `design-review: WORKING`, `design-gate: PENDING`
- Normative upstream: frozen v0.3 Release Spec and existing `scripts/operator_api.py` / `scripts/operator_conformance.py` behavior

## Verdict

**REWORK**

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

The Design correctly resolves Requirement Review MINOR-1 and preserves the read-only authority boundary, but it does not yet provide an implementable proof path for several mandatory canonical conformance/error semantics through the same real MCP transport boundary. `design-gate` must remain PENDING.

## MAJOR-1 — mandatory negative canonical semantics can bypass the real MCP transport path

### Problem

The Design registers exactly seven fixed production MCP tools and hardcodes normal tool-handler canonical requests to `ai-sdlc.operator/v1`. That is a good production safety boundary.

However, the approved Requirement and reusable `run_conformance_suite()` require adapter-level evidence for at least:

- unsupported canonical version -> `UNSUPPORTED_API_VERSION`;
- unknown canonical capability -> `INVALID_REQUEST`;
- client trusted-identity injection -> canonical structured rejection;
- known unavailable capability -> `CAPABILITY_UNAVAILABLE`;
- canonical structured errors surviving MCP translation.

The existing conformance harness invokes these cases through `CanonicalAdapter.invoke(canonical_request)`. In particular it constructs an unsupported-version request and an unknown capability (`not.real`) and expects canonical error codes from the adapter boundary.

The proposed production MCP server, by contrast:

- selects capability from a fixed MCP tool name;
- does not expose an arbitrary capability-id argument;
- normally hardcodes the canonical API version;
- may reject unexpected trusted-only arguments at MCP schema/protocol validation before canonical dispatch.

Section 11 says unsupported-version proof may use "test injection at the adapter boundary rather than a production tool argument". Section 12 requires the same supported MCP server implementation to be exercised over stdio. The Design does not define how these two constraints compose.

As written, an implementation could satisfy the negative checks by calling `operator_api.dispatch` directly or by using a separate test-only facade that never crosses the MCP tool/stdio translation path. That would not prove the required adapter semantics through the real MCP boundary and would weaken the material-independence evidence.

### Required remediation

The Architect must define a bounded conformance ingress that satisfies all of the following simultaneously:

1. negative/version/injection cases cross the actual MCP protocol/stdio boundary and the same translation implementation used by the supported adapter;
2. production MCP mode still exposes only the seven approved read-only tools and no arbitrary semantic-write escape hatch;
3. no generic production tool accepts arbitrary canonical capability ids or raw canonical envelopes;
4. conformance-only behavior is impossible to activate accidentally in normal production startup, and is explicitly test-scoped/fail-closed;
5. the reusable `CanonicalAdapter` harness can map each mandatory negative case to deterministic MCP calls and recover the expected canonical machine-readable error code;
6. trusted-field injection is proven through the translation boundary rather than merely omitted from a high-level function signature and rejected before the adapter can demonstrate canonical handling;
7. the test path is the same shipped server/translation implementation, not a second fixture adapter.

One acceptable design direction is a server startup mode selected only by trusted test process configuration that registers an additional **conformance-only read/diagnostic ingress**. That ingress may accept a canonical-envelope-like test payload solely to exercise validation, but it must enforce a denylist/guard that prevents semantic write execution, must never be registered in normal startup, and must still route through the same canonical translation/dispatch code. This is only an example; the Architect may choose another bounded mechanism if it meets the seven conditions above.

### Why MAJOR

This is not a cosmetic test-detail issue. The Feature exists specifically to prove that a genuine MCP adapter independently exercises the canonical boundary. If the required negative/version/error evidence can bypass MCP, the main release-spec proof could be false-positive while production transport semantics differ. The Design therefore needs rework before implementation.

## Other review checks

### Requirement Review MINOR-1 — RESOLVED

The Design correctly separates complete canonical `system.capabilities` discovery from the narrower MCP tool registry. All 12 canonical capability identifiers remain discoverable with bounded availability, while only seven approved read-only MCP tools are invokable.

### Read-only authority boundary — PASS

The Design does not register write tools and forbids generic shell/repository/Event/Manifest/Gate/workflow mutation escape hatches.

### Trusted identity / authorization — PASS subject to MAJOR-1 evidence path

The server-owned `TrustedContextProvider` boundary is appropriate and keeps client identity separate from trusted runtime/service identity. The remaining issue is proving hostile input rejection through the actual MCP path.

### Honest backend availability — PASS

The Design does not create fake production durable stores and permits canonical `CAPABILITY_UNAVAILABLE` for reads whose trusted backing is absent.

### Transport material independence — PASS in architecture

Stdio MCP is materially distinct from the two canonical test fixtures, and the planned adapter identity/transport kind are distinct. MAJOR-1 concerns whether the conformance proof truly crosses that boundary for all required cases.

### Dependency/runtime choice — PASS

Using the official Python MCP SDK on a stable pinned/bounded release line and stdio avoids adding an unnecessary HTTP service to this Feature.

## Gate recommendation

`design-gate`: **DO NOT PASS**

Create a Design remediation task for the Architect. After remediation, a fresh independent Design Re-review must verify the exact conformance ingress and negative-case mapping before `design-v1` can be approved.
