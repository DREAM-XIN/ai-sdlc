# Plan — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## Goal

Implement approved `design-v2` as the second materially independent supported AI-client adapter at the OpenAI Responses function-tool boundary, with the exact ten-tool surface and the required four-write slice, while preserving the existing trusted Operator/Vertical/Decision/Notification/Effect-Lineage/Persist authority model.

Implementation must not claim Supported production status until the final reviewed production runtime semantics required by Design v2 are present on the implementation baseline.

## WU0 — Production dependency baseline gate

Before production binding work is treated as complete, re-read and resolve the actual reviewed/merged equivalents of the runtime contracts currently represented by:

- #245 — trusted production target/Store + adapter backend composition;
- #247 — trusted Feature truth / exact Feature Event transport;
- #249 — durable Vertical Persist gateway and restart receipt recovery;
- #251 — deterministic Persist reconciliation classification;
- #253 — integrated adapter/Vertical/Decision/Notification/Persist composition over one Store authority;
- #255 — durable stale-recorded-callback convergence.

Required behavior:

- do not copy unreviewed authority code into the adapter branch;
- do not treat an open/green PR as implementation-baseline authority;
- if required final semantics are absent, mark production-support implementation blocked rather than substituting test-only backends;
- once present, pin implementation evidence to the exact baseline and interfaces actually consumed.

Validation:

- dependency inventory records exact baseline commit/interface source;
- production builder cannot silently fall back to fake/in-memory authority;
- stale-recorded-callback convergence semantic contract is demonstrably present before Supported status is asserted.

## WU1 — OpenAI Responses protocol registry and strict schemas

Implement the genuine Responses adapter protocol boundary, conceptually in `scripts/operator_openai_responses.py` or the repository-equivalent module.

Implement:

- adapter id `ai-sdlc.openai.responses` and adapter protocol version `1`;
- exactly ten fixed Responses `type: function` definitions;
- strict JSON schemas with `additionalProperties: false` and required/nullable fields according to the approved Design;
- exact tool-name → canonical-capability mapping;
- no `project.inspect`, `operation.resume`, generic canonical router, raw Event/Manifest/Gate/repository/shell/policy/backend selector tool;
- protocol-version compatibility checks and bounded adapter errors.

Tests:

- exact registry equality;
- strict schema shape;
- unknown function name;
- malformed JSON;
- schema-extra forged trusted field;
- missing/invalid `call_id`;
- incompatible/unknown executable function-call fields fail closed;
- unsupported canonical API version reaches canonical version handling without semantic backend work.

## WU2 — Responses collector, streaming completion and output encoder

Implement the production parser/collector and `function_call_output` encoder.

Implement:

- accepted terminal `function_call` with exact `call_id`, name and serialized arguments;
- parse arguments exactly once, without fuzzy repair/coercion;
- `parallel_tool_calls=false` production profile;
- collect-before-dispatch zero-or-one executable-call enforcement;
- streaming buffering for output-item/function-argument events;
- zero semantic dispatch from deltas/incomplete items;
- synchronous, retrieval and background terminal-object collection through the same boundary;
- exact `function_call_output` correlation by `call_id` with canonical JSON output.

Tests:

- partial stream → zero dispatch;
- interrupted stream → zero dispatch;
- unexpected multiple executable calls → zero dispatch;
- same terminal object from sync/retrieval/stream produces the same normalized call;
- provider item id/response id never substitute for `call_id` or authority.

## WU3 — Trusted request builder and durable Responses call journal

Implement trusted canonical request construction plus adapter replay state over the same protected Operator Store authority.

Implement:

- server-owned Feature/repository/ref/Store/profile/principal/policy/provider-scope registration;
- model-visible identifiers act only as bounded selectors;
- deterministic `responses_call_key` from trusted adapter/provider scope + `call_id`;
- adapter-derived canonical `request_id` and write `idempotency_key`;
- immutable call binding before semantic dispatch;
- result receipt containing bounded canonical response needed to reproduce `function_call_output`;
- exact replay, conflict detection and crash-after-canonical-write recovery using the same canonical idempotency key;
- call journal has no Feature/Operation/effect/Persist authority of its own.

Tests:

- exact duplicate call replay;
- conflicting `call_id` reuse fails before second semantic dispatch;
- lost provider output ACK returns the same result;
- crash after canonical write before result receipt converges to one semantic write;
- fresh process reconstructs replay truth from Store;
- forged repository/ref/Store/principal/policy/adapter identity cannot override trusted context.

## WU4 — Exact production adapter binding

Bind the Responses adapter to the final trusted production Operator composition available after WU0.

Implement:

- consume the final production composition constructor/interfaces rather than reimplementing Store/Vertical/Decision/Notification/Persist authority;
- exact ten-capability adapter-facing backend map;
- exact four-write slice:
  - `operation.start`;
  - `operation.cancel`;
  - `decision.respond`;
  - `notification.ack`;
- `operation.start` maps to the trusted profile-bound Vertical start backend, not a raw Store-only shortcut;
- `operation.resume` remains server/internal and is absent from the client registry/backend map;
- Responses call journal + adapter + Vertical + Decision + Notification + Persist bind the same protected Store authority;
- final Persist path remains translator → Persist requested/linearized → durable Persist gateway → trusted Feature Event transport → Persist confirmed;
- no adapter-local Worker launcher, Persist gateway, Event writer or second linearization point.

Tests:

- construction fails closed on missing/mismatched production dependencies;
- same Store runtime/binding tuple across all authority-bearing components;
- exact four-write surface and no `operation.resume`;
- raw Store-only start shortcut cannot satisfy production binding;
- no test-only production fallback.

## WU5 — Official OpenAI Responses host/runtime entrypoint

Implement the supported host layer using the repository-approved OpenAI SDK dependency and official Responses endpoint contract.

Implement:

- server-owned OpenAI credential/project/model configuration;
- synchronous create path;
- streaming collection path;
- retrieval/retrieval-after-interruption path;
- optional background polling only if dependency/runtime policy supports it cleanly;
- provider conversation state remains model context only, never Operator truth;
- provider retries/retrieval always re-enter the durable call journal before semantic work.

Tests should not require billable external calls in authoritative deterministic CI. Provider HTTP/SDK transport may be replaced at the provider-side fixture seam only.

## WU6 — Lane A protocol and adversarial fault-injection conformance

Implement `OpenAIResponsesConformanceAdapter` as a materially independent driver that constructs Responses-shaped protocol objects/events and never delegates to MCP/direct/json-roundtrip canonical fixture adapters.

Lane A may use deterministic lower-level trusted doubles after the real Responses translation boundary to force rare states.

Cover at least:

1. six frozen common canonical capabilities;
2. adapter identity/version propagation;
3. version negotiation and structured errors;
4. malformed/unknown protocol cases;
5. duplicate/conflicting Responses replay;
6. cancellation before/after launch linearization;
7. external lookup `UNKNOWN` fail closed;
8. lost external launch ACK same-key recovery;
9. generation takeover with stable semantic/external identities;
10. candidate stale before launch;
11. Effect Lineage blocked successor;
12. Decision invalid/stale/expired/policy mismatch;
13. Notification duplicate ack;
14. Persist requested/linearized/ACK-loss and deterministic rejection classification;
15. secret/error redaction.

Lane A tests must be explicitly marked insufficient for Supported production proof.

## WU7 — Lane B mandatory supported production-composition conformance

Build the distinct Design-v2 Lane B proof using provider-side deterministic Responses fixtures but the **actual final production composition constructor/interfaces on the implementation baseline**.

Required assertions:

1. independent Responses driver crosses the production registry/parser/call journal/request builder/output encoder;
2. `operation.start` reaches the real trusted profile-bound Vertical canonical backend;
3. exact equivalent duplicate start converges to the existing canonical Operation outcome, satisfying Requirement scenario #18;
4. call journal / adapter / Vertical / Decision / Notification / Persist share the same protected Store authority;
5. only the exact four writes are model-invokable;
6. `operation.resume` remains server-only;
7. an exercised semantic Persist crosses the final durable Persist gateway and trusted Feature Event transport exactly once;
8. no second Persist or dispatch authority exists;
9. production composition cannot silently fall back to test-only authority.

The test harness may use temporary Remote Git / trusted external transport emulators only at already-reviewed external seams; it may not replace the production composition object itself.

## WU8 — Hard stale-recorded-callback recovery proof

Against the implementation baseline runtime from WU0/WU4, add or reuse authoritative deterministic coverage proving the hard Design-v2 prerequisite.

Scenario:

- Worker callback for candidate A is durably recorded;
- trusted Feature/ref/revision/stage/candidate truth changes so the callback is stale;
- processing produces exactly one durable deterministic rejection and the reviewed stable fail-closed/BLOCKED state;
- stale callback creates zero `feature.event.translated` and zero Persist authority;
- destroy/rebuild runtime and reconcile again;
- second recovery performs zero Store mutation and no duplicate rejection;
- successor candidate B remains behind unresolved Effect Lineage predecessor fencing;
- B creates zero new reservation, zero new `external_dispatch_key`, zero second external launch;
- a separate transient Feature-read failure remains transient and is not persisted as stale.

If this semantic contract is absent, the adapter cannot be marked Supported and Implementation completion must remain blocked.

## WU9 — Public Runtime, documentation and authoritative validation integration

Add all supported runtime modules, schemas, entrypoints and dependency declarations to the Public Runtime distribution.

Documentation must state:

- exact adapter identity/version;
- exact ten-tool surface;
- server-owned trusted configuration requirements;
- OpenAI provider configuration/credentials stay server-side;
- deterministic CI may emulate Responses protocol on the provider side;
- real OpenAI service dogfood is later release evidence, not implied by Feature deterministic conformance.

Wire the adapter validators into the authoritative repository validation path rather than leaving orphan scripts.

Required completion checks:

- `python scripts/validate.py` succeeds on the exact functional candidate;
- Validate AI-SDLC protocol succeeds;
- Validate Public Runtime Distribution succeeds;
- Required PR Gate succeeds;
- dedicated Responses protocol/conformance validation succeeds;
- dedicated Lane B production-composition validation succeeds;
- stale-recorded-callback hard-prerequisite validation succeeds.

## Dependency order

Primary order:

`WU0 → WU1 → WU2 → WU3 → WU4 → WU5 → WU6 → WU7 → WU8 → WU9`

Allowed parallelism:

- WU1/WU2 may begin while WU0 dependencies are converging because they do not consume production authority interfaces;
- WU3 journal schema may proceed against the accepted Store runtime contract, but final Store binding proof waits for WU4;
- WU6 protocol/fault tests may be developed before WU4 final production composition;
- WU7/WU8 cannot be declared passing until WU0 production dependencies are genuinely present on the implementation baseline.

No Plan item authorizes copying temporary dependency-branch authority implementations into the adapter branch to bypass WU0.

## Completion evidence

Implementation completion requires an exact candidate head where:

- all WU1–WU9 code/tests/docs are present;
- WU0 dependency baseline is recorded and satisfies the approved Design-v2 hard contracts;
- Requirement scenario #18 is proven through Lane B;
- the hard stale-recorded-callback restart/lineage/transient matrix passes;
- all existing canonical/MCP/Store/Vertical/Effect-Lineage/Decision/Notification/Persist validators remain green;
- Public Runtime packaging is truthful;
- Implementation Evidence pins exact candidate SHA and identifies the exact final production interfaces consumed.

Developer completion does not PASS `code-gate`; fresh independent Code Review follows. This Plan does not modify VERSION, create the final release manifest, claim #221 PASS, claim real external dogfood, or claim v0.3 release readiness.
