# Requirement — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## 1. Purpose

Complete the frozen v0.3 AI-client adapter release contract by adding a second materially independent supported AI-client adapter over `ai-sdlc.operator/v1` and by proving at least one supported adapter executes the required semantic write slice end-to-end.

This Feature defines the second adapter as an OpenAI Responses function-tool adapter with a stable supported adapter identity, conceptually:

```text
adapter_id: ai-sdlc.openai.responses
protocol_kind: openai-responses-function-tools
```

The adapter is a client/protocol boundary only. It does not become lifecycle, authorization, Worker, Gate, candidate, Effect Lineage, launch, Persist, or Human/Product authority.

## 2. Normative upstream

The Feature shall conform to the current protected `main` versions of:

- `docs/v0.3-release-spec.md`;
- `release/v0.3.0-draft.yaml`;
- canonical `ai-sdlc.operator/v1` request/response schemas and capability registry;
- accepted `F-OPERATOR-MCP-ADAPTER-0001` behavior and its read-only production boundary;
- accepted Operation Store, Vertical Loop, Effect Lineage, and Decisions/Notifications semantics;
- existing identity, idempotency, authorization, Feature Event/Persist, candidate, generation, cancellation, launch, and Persist linearization contracts.

The adapter shall also pin and deterministically validate only the OpenAI Responses function-tool protocol assumptions it actually uses. At minimum those assumptions are:

- a developer-defined tool has function identity plus a bounded JSON-schema parameter contract;
- strict schema-constrained function tools are supported;
- a model tool invocation is represented as a function-call item containing an exact function name, serialized arguments, and a stable call correlation identity (`call_id`);
- tool execution returns a function-call output correlated to that exact call identity.

A future OpenAI protocol change must not silently alter canonical AI-SDLC semantics. If the adapter cannot map a received protocol item to the pinned supported contract, it shall fail closed rather than infer authority from prose or unknown fields.

## 3. Product outcome

After Feature Acceptance, AI-SDLC v0.3 shall have at least two genuinely supported materially independent AI-client adapters:

1. existing `ai-sdlc.mcp.stdio`, preserving its accepted read-only MCP stdio surface;
2. the new OpenAI Responses function-tool adapter, independently exercising the canonical API and providing the required write-capable release slice.

The new adapter shall let a supported OpenAI Responses-style AI client discover and invoke bounded AI-SDLC Operator tools while all trusted execution decisions remain server/runtime-owned.

## 4. Material independence

### 4.1 Required independence

The OpenAI Responses adapter shall be materially independent from MCP at the AI-client protocol boundary.

Independent evidence must establish that it has its own:

- supported adapter id;
- protocol item/tool-definition representation;
- tool-call parser/validation path;
- call-correlation semantics;
- function-call output translation;
- conformance driver that exercises that protocol path.

Sharing the transport-independent canonical `operator_api.dispatch` contract, canonical schemas, trusted backends, and common conformance assertions is required and does not violate material independence.

### 4.2 Prohibited fake independence

The following do not count as a second supported adapter:

- renaming `ai-sdlc.mcp.stdio`;
- wrapping the MCP server/session with a new class or command;
- reusing MCP tool registration and only changing labels;
- `fixture.direct`, `fixture.json-roundtrip`, or another in-process/testing fixture promoted as production support;
- a generic JSON passthrough that accepts arbitrary canonical capability ids and therefore does not exercise a distinct supported AI-client tool protocol;
- a conformance shim that bypasses the production OpenAI Responses translation path and calls canonical dispatch directly.

## 5. Supported production tool surface

### 5.1 Frozen common conformance tools

The OpenAI Responses adapter shall provide fixed, bounded tools mapping to the frozen common adapter conformance subset:

```text
system.capabilities
feature.status
operator.inbox
operation.status
decision.list
notification.list
```

### 5.2 Required write-capable release slice

The same supported adapter shall additionally expose and execute the required v0.3 write slice through fixed bounded tools:

```text
operation.start
operation.cancel
decision.respond
notification.ack
```

These four semantic writes are required production capabilities for this adapter, subject to honest backend availability and trusted policy.

### 5.3 Additional canonical capabilities

The Feature is not required to expose `project.inspect` or `operation.resume` as OpenAI function tools merely because they exist in the canonical registry. If Design chooses to expose either, it must remain a fixed reviewed tool with canonical schemas and the same authority rules; such additions must not be necessary to satisfy the release blocker.

`system.capabilities` remains registry-complete and truthful for the canonical API even when a known canonical capability is not exposed as a production OpenAI function tool or lacks trusted production backing.

### 5.4 No generic escape hatch

Production OpenAI tool registration shall not include a tool that accepts any of the following as caller/model-selected authority:

- arbitrary canonical capability id;
- raw canonical envelope replacement;
- raw Feature Event;
- Feature Manifest patch;
- Gate/revision mutation;
- arbitrary repository path write;
- shell/command execution;
- trusted policy ref or policy object;
- protected state ref;
- trusted runtime/backend selector;
- credentials/token scope;
- Worker/provider/profile selection outside already trusted bounded orchestration.

Each production tool name maps to exactly one reviewed canonical capability.

## 6. Function-tool definitions and arguments

Every production tool shall publish a bounded JSON-schema parameter contract compatible with the pinned OpenAI Responses function-tool assumptions. The tool definition shall use strict schema behavior where supported by the pinned contract.

Tool arguments may contain only the non-authoritative client request data required by the corresponding canonical capability, such as:

- requested canonical API version where version negotiation is intentionally exposed;
- canonical target fields permitted by the capability request schema;
- capability-specific payload fields;
- client-side request/correlation data that the canonical envelope permits.

Tool arguments shall not be trusted merely because the OpenAI tool schema validated them. Canonical validation and trusted context/policy checks remain mandatory.

Malformed serialized arguments, schema-invalid arguments, unknown fields where the schema is closed, or wrong tool names shall fail before semantic writes.

## 7. Call correlation and replay safety

### 7.1 `call_id` is correlation, not authority

The adapter shall bind each supported function invocation to the exact received OpenAI Responses call correlation identity (`call_id`) or an equivalent pinned protocol identity.

That call identity may participate in request correlation/idempotency derivation where Design proves the mapping is deterministic, but it shall never imply:

- authenticated human identity;
- trusted service identity;
- authorization approval;
- current Feature revision;
- Operation generation ownership;
- candidate validity;
- permission to launch or Persist.

### 7.2 Function-call output

The adapter shall return one bounded machine-readable function-call output correlated to the exact invocation. The output must preserve the canonical success/error meaning and shall not require a model to parse free-form prose to determine whether a semantic write succeeded.

### 7.3 Duplicate/replayed tool calls

Repeated delivery of the same OpenAI call or equivalent canonical request must preserve the underlying canonical idempotency semantics. Adapter-layer retry/replay shall not manufacture a new semantic write identity merely because a new process/session handles the request.

A conflicting reuse of a correlation/idempotency identity shall fail according to canonical bounded error behavior rather than silently execute a second semantic write.

## 8. Trusted identity and authorization boundary

### 8.1 Server/host-owned trusted context

The adapter shall receive trusted invocation context only from a server/host/runtime-owned provider or equivalent protected composition boundary.

The AI client/model may assert its supported adapter identity through the fixed adapter implementation, but model-generated arguments or text shall not choose or replace:

- human principal identity;
- trusted service/runtime identity;
- protected authorization policy;
- installation/control-repository authority;
- state ref;
- repository credential scope;
- trusted clock;
- Operation Store writer authority.

### 8.2 AI client identity is not human authority

`ai-sdlc.openai.responses` identifies the AI-client adapter. It does not itself imply Human/Product authority or permission to answer an authorization Decision.

Where canonical semantics distinguish human principal, AI-client adapter, and trusted runtime/service identity, the adapter must preserve all three distinctions.

A caller/model may not set `responded_by_user`, trusted responder roles, or equivalent protected identity facts through ordinary tool arguments.

### 8.3 Generic model approval is insufficient

A model output such as `yes`, `approve`, `continue`, or similar prose is not a trusted authorization by itself.

`decision.respond` shall accept only the exact current bounded response identifier defined by the durable Decision and only when the trusted invocation context represents an authorized responder for that Decision under current protected policy.

The OpenAI adapter must not add fuzzy natural-language interpretation that converts free-form model text into a broader Decision choice or Human/Product Acceptance.

## 9. Canonical API compatibility

The adapter shall preserve `ai-sdlc.operator/v1` semantics rather than define a parallel Operator protocol.

At minimum it shall preserve:

- exact API-version negotiation;
- canonical request validation;
- canonical result/error envelopes or a lossless bounded function-output representation of them;
- capability-discovery truthfulness;
- structured error codes;
- trusted/client identity separation;
- request/idempotency behavior;
- expected revision semantics where applicable;
- honest `CAPABILITY_UNAVAILABLE` / `POLICY_DENIED` / `UNAUTHORIZED` behavior.

Unsupported canonical versions shall return `UNSUPPORTED_API_VERSION` without invoking semantic-write backends.

Adapter translation must not collapse a canonical structured error into an apparently successful free-form tool result.

## 10. Write capability safety requirements

### 10.1 `operation.start`

The adapter shall invoke only the accepted canonical `operation.start` backend.

It shall preserve:

- trusted target scope;
- idempotent/equivalent-start convergence;
- one active Operation generation per Feature;
- expected Feature revision/current state validation;
- protected Operator Store/CAS semantics.

The adapter cannot directly create or edit an Operation Store file as a substitute for the backend.

### 10.2 `operation.cancel`

The adapter shall invoke only the accepted canonical `operation.cancel` backend and preserve the established distinction between Operation cancellation and Feature cancellation.

Cancellation through this adapter shall not retroactively revoke an external effect that already crossed `dispatch.launch.authorized`, shall forbid later unlinearized work, and shall preserve the separate Persist linearization rules.

The adapter must not claim that a successful cancel guarantees an already launch-linearized external side effect did not execute.

### 10.3 `decision.respond`

The adapter shall invoke the accepted canonical Decision backend and preserve exact Decision identity, choice, revision/ref/candidate/generation/policy/identity/expiry checks.

It shall not:

- synthesize a Decision;
- expand allowed choices;
- reinterpret arbitrary prose as an allowed choice;
- bypass cancellation/generation fences;
- treat Decision resolution as `dispatch.launch.authorized`;
- treat Decision resolution as `persist.linearized`;
- synthesize Human/Product Acceptance evidence.

### 10.4 `notification.ack`

The adapter shall invoke only the accepted canonical notification acknowledgement backend.

Acknowledgement shall remain exact-item and idempotent, and shall not:

- mutate Feature lifecycle state;
- grant authorization;
- acknowledge another/future notification;
- depend on a previous chat/session.

## 11. Existing authority and safety invariants remain mandatory

No OpenAI Responses adapter path may bypass or replace:

- authoritative Feature Manifest + trusted Feature Event/Persist lifecycle authority;
- optimistic expected revision checks;
- exact target ref and candidate-head checks;
- Operation generation fencing;
- Operation cancellation/supersession semantics;
- semantic effect reservation and Effect Lineage;
- generation-independent `external_dispatch_key` rules;
- `dispatch.launch.authorized` launch linearization;
- trusted external receipt lookup and UNKNOWN fail-closed behavior;
- Persist request/linearization/receipt semantics;
- independent Developer / Reviewer / QA role identities;
- Human/Product Acceptance boundary.

The adapter does not receive credentials allowing it to mutate protected lifecycle state outside those trusted paths.

## 12. MCP compatibility and support matrix

The accepted MCP adapter remains unchanged in product authority:

```text
adapter_id: ai-sdlc.mcp.stdio
production invocation surface: read-only
```

This Feature must not add the write capabilities to MCP merely to satisfy the release write requirement.

After this Feature, the release evidence must be able to state truthfully:

- supported materially independent adapter count >= 2;
- both adapters pass the frozen common conformance subset;
- at least one supported adapter is write-capable for the four required release writes;
- the write-capable adapter is `ai-sdlc.openai.responses` unless an independently reviewed Design change gives an equally bounded identity.

## 13. Production support versus deterministic tests

The Feature must implement a genuine supported production adapter surface, not only test fixtures.

Deterministic validation may emulate OpenAI Responses protocol objects locally and does not need to call a billable/external model for every CI run, provided that:

- the exact same production tool definitions/parser/translation/output code is exercised;
- protocol-shaped function-call input includes the pinned call identity/name/serialized-arguments semantics;
- the conformance driver cannot bypass the production translation path;
- tests prove malformed/unknown protocol items fail closed;
- support documentation explains how a real OpenAI Responses host/client integrates with the adapter.

Whether release dogfood also uses a live OpenAI-hosted model is a release-evidence decision outside Feature-level deterministic conformance. Feature Acceptance alone shall not overclaim external model-service dogfood.

## 14. Public Runtime and packaging

If the adapter is part of the supported public runtime surface, all runtime files, schemas, entrypoints, dependency declarations, and documentation required for a user to invoke it shall be included in the Public Runtime distribution and protected by distribution validation.

The implementation must not depend on repository-only test modules that are absent from the published runtime.

Secrets/API keys, if any optional host integration needs them, shall be supplied only through existing trusted runtime/secret mechanisms and never written into tool definitions, canonical responses, Feature state, Decision/Notification records, or committed fixtures.

The core protocol translation should remain testable without requiring a live secret unless Design Review establishes an unavoidable production-contract reason otherwise.

## 15. Deterministic acceptance scenarios

Implementation and independent QA shall cover at least:

1. OpenAI adapter is registered with a stable identity distinct from `ai-sdlc.mcp.stdio`.
2. Tool definitions for the frozen common six capabilities are fixed and schema-bounded.
3. Fixed tools exist for `operation.start`, `operation.cancel`, `decision.respond`, and `notification.ack`.
4. No generic arbitrary-capability / Event / Manifest / Gate / shell / repository-write escape tool exists.
5. The OpenAI adapter and MCP independently pass the same canonical common conformance subset.
6. A direct/JSON fixture cannot be counted as the second supported adapter.
7. Supported API-version negotiation crosses the OpenAI protocol path.
8. Unsupported API version returns `UNSUPPORTED_API_VERSION` without semantic backend execution.
9. Structured canonical errors survive OpenAI function-call-output translation exactly enough for machine handling.
10. Trusted identity cannot be replaced by model arguments.
11. Adapter identity alone does not authorize a human-bound Decision.
12. Unknown function name fails closed.
13. Malformed JSON arguments fail closed.
14. Schema-invalid arguments fail closed.
15. Missing/wrong call correlation identity fails closed where the pinned protocol requires it.
16. Exact equivalent function-call replay converges according to canonical idempotency rules.
17. Conflicting replay cannot execute a second incompatible semantic write.
18. `operation.start` executes against the real trusted canonical backend and equivalent duplicate start converges.
19. `operation.cancel` preserves cancel-before/after launch/Persist linearization boundaries and reports bounded state honestly.
20. `decision.respond` accepts one exact current allowed choice only under authorized trusted responder context.
21. Generic/fuzzy model approval does not authorize `decision.respond`.
22. Stale revision/ref/candidate/generation/policy/expiry/cancel Decision response remains rejected through the adapter.
23. `notification.ack` is exact/idempotent and does not mutate Feature lifecycle or grant authorization.
24. Capability unavailable/policy denied/unauthorized cases remain honest structured failures.
25. OpenAI adapter cannot select protected policy, state ref, credentials, trusted backend, Worker role, or broader repository scope.
26. Existing MCP production tool list remains read-only.
27. Existing Operation Store / Vertical Loop / Effect Lineage / Decisions & Notifications / lifecycle / security / cross-repository suites remain green.
28. Public Runtime distribution contains every required supported-adapter runtime artifact and excludes test-only authority shortcuts.
29. Authoritative protocol validation runs the new adapter conformance tests; they are not optional/orphan scripts.
30. Final functional candidate passes Validate AI-SDLC protocol, Validate Public Runtime Distribution, and Required PR Gate.

## 16. Feature acceptance criteria

This Feature may be accepted only when independent evidence proves:

- a second genuine supported AI-client adapter exists and is materially independent from MCP at the client protocol boundary;
- both supported adapters satisfy the frozen common canonical conformance subset;
- the OpenAI Responses adapter executes all four required release semantic writes through accepted canonical trusted backends;
- tool-call/model input cannot become trusted lifecycle/authorization identity by assertion;
- generic model prose cannot synthesize Human/Product authorization or Acceptance;
- no generic capability/Event/Manifest/shell escape hatch is exposed;
- all existing candidate/generation/cancel/Effect Lineage/launch/Persist fences remain mandatory;
- deterministic adapter validation is wired into the repository's authoritative validation path;
- Public Runtime packaging is truthful and regression-green.

## 17. Explicit non-scope and release boundary

Feature PASS does not prove or complete:

- Issue #221 real-runtime Worker effect-safety fault injection matrix;
- real external Worker exactly-once execution;
- all v0.3 happy-path/remediation/session-recovery dogfood;
- Issue #218 release evidence ledger synchronization, except that later governance may cite this Feature's durable evidence;
- final security/publication/Release Review;
- overall v0.3.0 release readiness;
- `VERSION` publication or creation of final `release/v0.3.0.yaml`;
- expansion of MCP beyond its accepted read-only surface;
- autonomous Human/Product Acceptance.

Feature-level `release-gate: PASS` will mean only that this adapter Feature satisfies its bounded approved scope. Release-level governance must separately update the adapter blockers and readiness ledger from durable evidence.
