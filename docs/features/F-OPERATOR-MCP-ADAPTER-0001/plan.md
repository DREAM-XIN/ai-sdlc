# Implementation Plan — F-OPERATOR-MCP-ADAPTER-0001

## 1. Inputs and scope

This Plan consumes:

- approved `requirement-v1`;
- approved revised `design-v1`;
- `evidence-requirement-review-v1`;
- failed `evidence-design-review-v1` plus completed `evidence-design-remediation-v1`;
- passing `evidence-design-review-v2`.

Implementation is limited to one genuine supported **read-only MCP stdio adapter** over `ai-sdlc.operator/v1`. It must not implement semantic write tools, durable Operation/Decision/Notification stores, HTTP transport, autonomous orchestration, Gate authority, or v0.3 publication.

## 2. Implementation strategy

Implement in dependency order so each layer can be validated before the next one depends on it:

```text
WU-1 dependency/runtime declaration
   ↓
WU-2 shared MCP server + seven production read tools
   ↓
WU-3 bounded conformance-only ingress
   ↓
WU-4 real stdio CanonicalAdapter driver + validator
   ↓
WU-5 regression/CI wiring + implementation evidence
```

The Developer may combine commits, but must preserve these logical boundaries in implementation evidence.

## 3. WU-1 — MCP SDK dependency and import boundary

### Changes

- add the official Python MCP SDK as an explicit bounded/pinned dependency consistent with repository policy;
- use a stable non-pre-release line compatible with Python 3.12;
- do not add an HTTP/web framework solely for this Feature;
- keep imports localized so existing non-MCP validation paths remain understandable and deterministic.

### Checks

- dependency installation succeeds on Python 3.12;
- no alpha/pre-release version is selected;
- existing canonical validation imports/tests still run after dependency change.

### Done when

The repository can import the official MCP client/server APIs needed for stdio without changing canonical API semantics.

## 4. WU-2 — supported production MCP stdio server

### Files

Primary target: `scripts/operator_mcp.py`.

### Required implementation

- stable adapter identity:
  - `adapter_id = ai-sdlc.mcp.stdio`
  - `transport_kind = mcp-stdio`;
- server builder with injected trusted-context provider and backend registry;
- production `main()` running MCP stdio;
- exactly seven production MCP tools:
  - `ai_sdlc_system_capabilities`
  - `ai_sdlc_project_inspect`
  - `ai_sdlc_feature_status`
  - `ai_sdlc_operator_inbox`
  - `ai_sdlc_operation_status`
  - `ai_sdlc_decision_list`
  - `ai_sdlc_notification_list`;
- each tool maps to one fixed canonical read capability;
- bounded tool inputs: `api_version`, canonical target, capability payload as appropriate;
- shared canonical invocation helper that constructs the envelope and calls `operator_api.dispatch`;
- shared structured MCP response translation preserving canonical `error.code`;
- server-owned `TrustedContextProvider` boundary.

### Explicit prohibitions

Do not expose:

- any of the five canonical semantic write capabilities as MCP tools;
- arbitrary capability ids;
- raw canonical envelope replacement;
- arbitrary shell/repository/workflow dispatch;
- Feature Event/Manifest/Gate mutation;
- MCP-controlled trusted identity, authorization context, backend selector, or server mode.

### Checks

- normal server tool listing is exactly seven approved tools;
- complete canonical `system.capabilities` response still contains all 12 registry entries;
- a read whose backend is absent returns canonical `CAPABILITY_UNAVAILABLE`;
- unsupported `api_version` through a normal read tool returns `UNSUPPORTED_API_VERSION` before backend invocation;
- adapter identity reaches deterministic backend unchanged.

## 5. WU-3 — bounded conformance-only ingress

### Required implementation

Add server-builder construction equivalent to:

```python
build_server(..., enable_conformance_probe=False)
```

Production `main()` must hardcode the probe disabled. There must be **no production CLI, environment, config, MCP, or runtime switch** that can enable it.

When explicitly enabled by repository test code only, register exactly one additional tool:

```text
__ai_sdlc_conformance_probe
```

Its schema accepts only the closed enum:

```text
unknown_capability
trusted_identity_injection
```

Case implementations must create fixed, non-side-effect requests through the same shared MCP-to-canonical invocation helper:

- unknown capability -> fixed `not.real` -> `INVALID_REQUEST`;
- trusted identity injection -> fixed illegal trusted-only envelope field on `feature.status` -> `INVALID_REQUEST` before backend invocation.

### Checks

- production tool list never includes the probe;
- test/conformance construction contains seven normal tools + exactly one probe;
- probe rejects any input outside the closed enum;
- probe cannot select write capabilities or caller-supplied raw envelope/payload overrides;
- both negative cases cross shared translation and structured-response code.

## 6. WU-4 — real MCP stdio conformance driver

### Files

- `scripts/operator_mcp_conformance.py`;
- test-only launcher such as `tests/fixtures/operator_mcp_conformance_server.py`;
- `scripts/validate_operator_mcp.py`.

### Required implementation

Create `McpStdioConformanceAdapter` implementing the existing `CanonicalAdapter` protocol:

```text
adapter_id = ai-sdlc.mcp.stdio
transport_kind = mcp-stdio
wrapper_depth = 0
```

It must:

- launch the same `operator_mcp.py` server builder through a fixed test-only launcher;
- communicate through the official MCP stdio client/session only;
- never call `operator_api.dispatch` directly from the driver;
- never delegate to `DirectFixtureAdapter` or `JsonRoundTripFixtureAdapter`;
- inject deterministic trusted test backends/provider only at trusted server construction.

Map the reusable canonical harness requests as follows:

- supported common read subset -> normal production MCP tools;
- `project.inspect` -> normal production MCP tool;
- unsupported version -> normal feature-status tool with unsupported `api_version`;
- `not.real` -> closed conformance probe `unknown_capability`;
- trusted-only field injection -> closed conformance probe `trusted_identity_injection`;
- known unavailable -> normal production read tool with backend absent.

### Required validator assertions

At minimum prove:

1. six frozen common capabilities pass shared canonical semantics over MCP stdio;
2. `project.inspect` succeeds when its deterministic backend exists;
3. unsupported version -> `UNSUPPORTED_API_VERSION` and zero semantic backend invocation;
4. unknown capability -> `INVALID_REQUEST`;
5. trusted identity injection -> `INVALID_REQUEST` before backend invocation;
6. known unavailable -> `CAPABILITY_UNAVAILABLE` with bounded reason;
7. canonical structured error code survives MCP result translation;
8. stable MCP adapter identity reaches backend;
9. production tool surface remains read-only;
10. conformance adapter is materially distinct from both canonical fixture adapters;
11. alias/thin-wrapper negative proof continues to reject aliases;
12. test backends are clearly non-production.

## 7. WU-5 — regression integration and durable implementation evidence

### Regression commands

The Developer must run at least the repository-equivalent of:

```text
python scripts/validate_operator_api.py
python scripts/validate_operator_mcp.py
python scripts/validate_protocol.py   # or the repository's full protocol validation entrypoint
```

If the repository's canonical CI command differs, use the actual current command discovered from `.github/workflows` and record it exactly.

Also ensure the existing PR required gate runs on the Feature candidate and remains green.

### Durable artifacts

Create:

- `docs/features/F-OPERATOR-MCP-ADAPTER-0001/implementation.md`;
- implementation verification/evidence artifact if required by current repository conventions.

Evidence must state:

- exact candidate SHA tested;
- MCP SDK version/constraint actually selected;
- exact commands and outcomes;
- production tool list;
- canonical discovery count = 12;
- conformance adapter identity/transport/wrapper depth;
- negative-case results;
- proof that the conformance probe cannot be enabled through production startup;
- proof that no semantic write tool was introduced;
- known deferred backends/capabilities and why `CAPABILITY_UNAVAILABLE` is expected.

## 8. Implementation sequencing and stop conditions

The Developer must stop and return to Design rather than silently broaden scope if any of these become necessary:

- changing canonical `ai-sdlc.operator/v1` schemas/error semantics;
- adding a generic production canonical invocation tool;
- adding semantic write MCP tools;
- adding HTTP/authentication product surface;
- creating durable Operator/Decision/Notification stores;
- changing Feature Event/Persist or Gate authority.

Dependency/API friction in the chosen MCP SDK may be adapted within the approved component boundaries, but security/authority changes require lifecycle rework.

## 9. Code Review focus

Independent Code Review must inspect the actual candidate diff for:

- exactly seven production tools;
- complete canonical discovery preservation;
- fixed capability mapping and absence of generic/write escape hatch;
- production hard-disable of conformance probe;
- same shared server/translation path for conformance;
- trusted-context separation;
- structured error preservation;
- deterministic tests and materially independent MCP transport evidence;
- no hidden canonical/lifecycle authority expansion.

## 10. Verification focus

Independent QA must run fresh checks against the exact reviewed candidate head, not rely only on Developer evidence. QA should independently validate real stdio protocol crossing, tool isolation, canonical semantics, negative cases, and regression compatibility.

## 11. Definition of Done for implementation stage

Implementation may transition to Code Review only when:

- WU-1 through WU-5 are complete;
- deterministic MCP validator passes;
- existing canonical/protocol regression validation passes;
- durable implementation evidence is committed;
- implementation remains within approved read-only scope;
- no Gate has been self-approved by the Developer.
