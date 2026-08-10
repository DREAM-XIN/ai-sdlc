# Design Re-review — F-OPERATOR-MCP-ADAPTER-0001

## Review context

- Role: fresh independent Design Reviewer
- Feature: `F-OPERATOR-MCP-ADAPTER-0001`
- Issue: #210
- Authoritative state re-read before verdict: revision `10`, `design-review: WORKING`, `design-gate: PENDING`
- Approved Requirement: `requirement-v1`
- Requirement Review Evidence: `evidence-requirement-review-v1`
- Original Design Review: `evidence-design-review-v1` (REWORK, 1 MAJOR)
- Remediation Evidence: `evidence-design-remediation-v1`
- Re-reviewed Design: revised `design-v1`

## Verdict

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0

The revised Design closes the prior MAJOR without expanding the production MCP authority surface and is sufficiently bounded, implementable, testable, and aligned with the approved Requirement and frozen v0.3 Release Spec.

## Prior MAJOR-1 closure

### Required condition: negative/version cases cross actual MCP stdio — PASS

The revised Design now gives deterministic mappings from the existing `CanonicalAdapter` conformance semantics to actual MCP calls:

- supported read requests -> normal MCP production tools;
- unsupported version -> normal `ai_sdlc_feature_status` tool with an unsupported `api_version` value;
- known unavailable read -> normal production read tool with backend intentionally absent;
- unknown canonical capability -> closed conformance-only probe case;
- trusted-identity injection -> closed conformance-only probe case.

The conformance driver communicates with the server only through an MCP stdio client/session and does not call `operator_api.dispatch` directly.

### Required condition: same shipped translation/server implementation — PASS

Production and conformance execution share `operator_mcp.py` server builder, canonical invocation helper, response translator, adapter identity logic, trusted-context provider boundary, and stdio runtime. Conformance mode changes only registration of one reserved diagnostic tool.

This is not a second fixture adapter and does not delegate to `DirectFixtureAdapter` or `JsonRoundTripFixtureAdapter`.

### Required condition: no generic production escape hatch — PASS

Normal production construction exposes exactly seven fixed read-only tools. Capability ids are fixed by tool registration. Production input cannot replace the raw canonical envelope, select arbitrary capabilities/backends, inject trusted context, submit Feature Events/Manifest patches/Gate changes, invoke shell commands, or dispatch arbitrary workflows.

No canonical semantic write capability is registered as an MCP production tool.

### Required condition: conformance-only behavior cannot be accidentally enabled — PASS

The revised Design requires production `main()` to hardcode `enable_conformance_probe=False` and explicitly forbids CLI, environment-variable, config-file, MCP-argument, or runtime-request mechanisms that enable the probe.

Only repository test/conformance code may construct the same server builder with the probe enabled.

### Required condition: conformance probe remains bounded/read-only — PASS

The reserved probe accepts only a closed enum:

- `unknown_capability`
- `trusted_identity_injection`

It cannot accept arbitrary capability ids, write capabilities, caller-provided raw envelopes, arbitrary payload fragments, repository mutations, or lifecycle operations. Both cases are fixed negative validation requests routed through the same shared translation and structured-response path.

## Requirement Review MINOR-1 — remains resolved

The Design explicitly separates:

- complete canonical `system.capabilities` discovery of all 12 known capabilities with honest availability; and
- exactly seven invokable production MCP read tools.

Write capabilities may remain known/discoverable as unavailable canonical capabilities without being exposed as MCP write operations.

## Additional review checks

### Trusted identity and authorization — PASS

Trusted runtime/service identity and authorization context come from a server-owned provider. MCP input cannot choose or replace the provider, and client identity does not imply human authorization.

### Honest backend state — PASS

No test fixture is promoted into a production durable store. Missing trusted backing remains `CAPABILITY_UNAVAILABLE` with bounded canonical reasons.

### Structured errors and versioning — PASS

Machine-readable canonical `error.code` survives MCP response translation. Unsupported-version proof uses a normal production read tool and remains side-effect free.

### Material independence — PASS

`ai-sdlc.mcp.stdio` / `mcp-stdio` is materially distinct from both canonical fixture adapters, and the Design requires `wrapper_depth = 0` evidence.

### Dependency/runtime scope — PASS

The Design uses the official Python MCP SDK on a bounded stable release line, preserves Python 3.12 CI compatibility, and does not broaden this Feature into an HTTP service.

### Backward compatibility and release boundary — PASS

The Design preserves canonical API semantics, existing lifecycle/Persist/gh-aw behavior, Gate authority, and v0.2 compatibility. It does not change `VERSION`, create the final v0.3 release manifest, or claim Operation Store/write-slice/dogfood completion.

## Gate recommendation

`design-gate`: **PASS**

`design-v1` may be approved with `evidence-design-review-v2`. The next legal stage is Plan / Orchestrator.
