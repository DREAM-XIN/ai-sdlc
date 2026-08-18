# OpenAI Responses Operator Runtime — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## Runtime identity

- Operator protocol: `ai-sdlc.operator/v1`
- adapter id: `ai-sdlc.openai.responses`
- adapter protocol version: `1`
- transport boundary: OpenAI Responses function tools
- provider SDK compatibility: official OpenAI Python SDK `>=2,<3`

The adapter is a protocol boundary only. It does not own Feature lifecycle authority, Gate authority, Worker authority, protected Store policy, Effect Lineage policy, Persist linearization, or Product Acceptance.

## Exact model-facing tool surface

The Responses registry is closed to exactly ten function tools:

1. `aisdlc_v1_system_capabilities`
2. `aisdlc_v1_feature_status`
3. `aisdlc_v1_operator_inbox`
4. `aisdlc_v1_operation_start`
5. `aisdlc_v1_operation_status`
6. `aisdlc_v1_operation_cancel`
7. `aisdlc_v1_decision_list`
8. `aisdlc_v1_decision_respond`
9. `aisdlc_v1_notification_list`
10. `aisdlc_v1_notification_ack`

The only model-invokable writes are the frozen v0.3 slice:

- `operation.start`
- `operation.cancel`
- `decision.respond`
- `notification.ack`

`project.inspect` is not exposed by this adapter. `operation.resume` remains server-side orchestration authority and is not a Responses tool.

## Trusted configuration boundary

Provider and Operator authority are server-owned. Model/tool arguments cannot select or expand:

- OpenAI API credentials, organization/project identity, model, instructions, background policy, or SDK client construction;
- target repository allowlists or Feature-to-ref bindings;
- protected Operator Store repository/ref, credentials, ruleset/protection verifier, or writer identity;
- trusted human/service principal or authorization context;
- Decision policy source or responder authority;
- Effect Lineage / unknown-resolution policy;
- Feature Event or Persist transport authority;
- Worker dispatch credentials or collector authority.

The host receives OpenAI/provider configuration out of band. Canonical requests are rebuilt from strict Responses arguments plus trusted registration/configuration; client/provider payloads do not become trusted identity merely by being well formed.

## Durable Responses call replay

Function-call identity is bound durably in the protected Operator Store. The journal records the exact provider call identity and canonical binding before semantic dispatch, then records the canonical function-call output receipt.

An exact equivalent replay returns the durable existing output without re-running the semantic backend. Conflicting reuse of the same call identity fails closed. This is an adapter replay boundary only; it does not replace Operation idempotency, external dispatch identity, Effect Lineage, or Persist idempotency.

## Production composition

The production binding accepts only the authoritative final full-Vertical v0.3 factory `build_v03_vertical_write_ready_operator_bundle()` when that reviewed factory is present on the implementation baseline. There is no fallback to the older semantic-only compatibility helper.

The final composition must prove one shared protected Store runtime across:

- Responses call journal;
- canonical adapter backends;
- Vertical executor/recovery;
- Decision and Notification coordination;
- durable Persist gateway.

`operation.start` must resolve to the trusted profile-bound Vertical start backend. Memory/test Store implementations, test-only protection, raw Store-only start, split Store authority, expanded write surfaces, or missing hard dependencies fail before Supported production construction.

`system.capabilities` is derived over the exact Responses-filtered backend map. It is not inherited from a broader production map, so `project.inspect` and server-only `operation.resume` cannot appear available through model-facing introspection.

As long as the final full-Vertical factory or stale-recorded-callback convergence contract is absent on the baseline, `production_dependency_status()` remains false and the adapter must not be described as Supported.

## Validation lanes

### Lane A — provider-shaped deterministic boundary

Deterministic CI may emulate OpenAI Responses objects/stream events at the provider side. The real Responses registry/parser/request builder/output encoder and durable call journal are still exercised before lower-level test seams.

Lane A covers protocol mapping, replay, writes and adversarial Operator states, but **Lane A is never Supported-production evidence**.

### Lane B — mandatory production composition

Supported status additionally requires deterministic provider fixtures driving the actual final production composition object from the implementation baseline. Lane B must prove the same protected Store authority, profile-bound Vertical start, exact four-write surface, server-only resume, semantic Persist through the final durable gateway exactly once, and no fallback/test authority.

The hard stale-recorded-callback WU8 proof must execute successfully before Lane B is allowed to execute or be recorded PASS. A structural dependency probe alone is insufficient.

Lane B cannot be substituted with copied code from an open dependency branch or a production-shaped test double.

## OpenAI service dogfood

Repository deterministic CI does not require or perform a live OpenAI service call. The host tests use SDK-shaped injected clients so provider behavior is deterministic and credentials are not required in CI.

A real OpenAI service run is separate release/dogfood evidence. Passing Feature conformance or Lane A does not imply that real provider dogfood has happened, does not complete Issue #221 effect-safety evidence, and does not make v0.3 release-ready.
