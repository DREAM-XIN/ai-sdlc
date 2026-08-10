# Design Remediation — F-OPERATOR-MCP-ADAPTER-0001

## Remediation task

`F-OPERATOR-MCP-ADAPTER-0001-DESIGN-REMEDIATION-1`

Source: `evidence-design-review-v1` MAJOR-1.

## Problem addressed

The original Design did not prove how unsupported-version, unknown-capability, trusted-identity-injection, unavailable-capability, and structured-error cases would cross the real MCP stdio/translation boundary without either bypassing MCP or exposing a generic production capability escape hatch.

## Remediation

`design-v1` has been revised to define:

1. a shared `operator_mcp.py` server builder and canonical invocation/response translation implementation used by both production and conformance execution;
2. production startup with exactly seven fixed read-only tools and no generic capability/raw-envelope tool;
3. optional `api_version` on normal read tools so unsupported-version negotiation is proven through the real production MCP path;
4. a conformance-only reserved diagnostic tool registered only when the server builder is explicitly called by test code with `enable_conformance_probe=True`;
5. production `main()` hardcoded to `enable_conformance_probe=False`, with no CLI, environment, config, MCP, or runtime switch capable of enabling the probe;
6. a closed two-case probe (`unknown_capability`, `trusted_identity_injection`) that cannot accept arbitrary capability ids, raw envelopes, payload overrides, or write capabilities;
7. fixed negative requests routed through the same shared MCP-to-canonical invocation helper and the same structured MCP response translator as production tools;
8. `McpStdioConformanceAdapter` mapping the existing canonical conformance suite to actual MCP stdio calls, never direct `operator_api.dispatch` calls and never fixture delegation;
9. deterministic tests proving production tool-list isolation, conformance-probe isolation, zero backend invocation for rejected version/injection cases, complete canonical discovery, and material adapter independence.

## Scope preservation

The remediation does not add semantic writes, production durable stores, HTTP transport, generic canonical invocation, lifecycle mutation, Gate authority, or release publication scope.

## Remediation result

The MAJOR is addressed at Design level. A fresh independent Design Re-review must verify the revised Design before `design-gate` can PASS.
