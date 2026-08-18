# Design — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

Feature: `F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001`

Issue: `#232`

Adapter identity: `ai-sdlc.openai.responses`

Adapter protocol version: `1`

Canonical API: `ai-sdlc.operator/v1`

## 1. Context and design objective

v0.3 requires at least two materially independent supported AI-client adapters over the same canonical Operator boundary. The existing MCP adapter is a genuine read-only MCP stdio transport. This Feature adds the second adapter at the real OpenAI **Responses API function-tool** boundary and makes that adapter support the frozen v0.3 write slice without transferring lifecycle, dispatch, Persist, Decision, authorization, or repository authority to the model or provider session.

The adapter is therefore not an MCP wrapper and is not a renamed MCP tool surface. It owns its own Responses tool registry, strict tool schemas, Responses output-item parser, `call_id` correlation, durable adapter replay binding, function-call-output encoder, Responses lifecycle handling, and independent conformance driver. It delegates only after a validated Responses tool call has been converted into the existing canonical `ai-sdlc.operator/v1` request.

Normative repository inputs are:

- approved `requirement-v1`;
- `evidence-requirement-review-v1`;
- frozen `docs/v0.3-release-spec.md`;
- `release/v0.3.0-draft.yaml` as the current release planning projection;
- merged canonical Operator API, MCP adapter, Operation Store, Vertical Loop, Effect Lineage, and Decisions/Notifications/Inbox work;
- the production-runtime contracts being developed in PRs #245, #247, #249, #251, #253 and #255, only after their exact interfaces are available on the implementation baseline.

The Requirement Review carry-forward MINOR is resolved in section 5 by pinning the concrete supported OpenAI Responses function-call profile, strict-schema rules, `call_id` correlation, `function_call_output`, streaming completion boundary, and unknown-field compatibility policy.

## 2. Goals

The implementation produced from this Design must provide:

1. a genuine OpenAI Responses function-tool adapter independent of MCP transport code;
2. exactly the frozen common conformance subset as invokable Responses tools:
   - `system.capabilities`;
   - `feature.status`;
   - `operator.inbox`;
   - `operation.status`;
   - `decision.list`;
   - `notification.list`;
3. the required adapter write slice:
   - `operation.start`;
   - `operation.cancel`;
   - `decision.respond`;
   - `notification.ack`;
4. stable adapter identity/version and stable tool names/schemas;
5. durable duplicate/replay handling across process/session boundaries;
6. server-owned target, ref, Store, credential, policy, runtime-profile and adapter-registration context;
7. composition with the trusted production Operator/Vertical runtime rather than a second authority implementation;
8. preservation of the frozen v0.3 semantic-effect, launch, cancellation, recovery, Effect Lineage and Persist invariants;
9. an independent Responses adapter conformance driver that crosses the Responses protocol representation and the production adapter parser/translator;
10. public runtime packaging and a path to later real dogfood evidence without claiming dogfood in this Feature's Design/implementation evidence.

## 3. Non-goals

This Feature does not:

- expose `operation.resume` to the model;
- expose `project.inspect` as a Responses tool in the v0.3 supported profile;
- expose a generic canonical capability dispatcher to the model;
- accept a raw canonical request envelope as a tool argument;
- accept or generate arbitrary Feature Events, Manifest patches, Store mutations, GitHub writes, workflow dispatches, policy mutations, shell commands, or backend selectors;
- add a second Persist linearization point;
- add a second external dispatch authority;
- replace the trusted Feature Event transport;
- replace the Operation Store, Vertical executor, Decision/Notification coordinator, Effect Lineage planner, or Persist gateway;
- make OpenAI `response.id`, `call_id`, item id, model text, tool arguments, request ids, or provider metadata a security authority;
- make Responses object storage the source of Operator truth;
- support OpenAI custom tools, hosted MCP tools, programmatic tool calling, tool search, computer-use calls, or arbitrary built-in tools as Operator capability substitutes;
- change `VERSION`, create `release/v0.3.0.yaml`, declare #221 PASS, or claim release readiness.

## 4. Architecture

### 4.1 High-level composition

```text
OpenAI Responses API / official SDK
        |
        | Response output items: type=function_call
        v
ResponsesProtocolCollector
        |
        | completed call_id + fixed tool name + strict arguments
        v
ResponsesCallJournal -------------+
        |                            |
        | exact replay binding       | same protected Operator Store runtime/ref
        v                            |
OpenAIResponsesOperatorAdapter      |
        |                            |
        | fixed tool -> canonical capability
        | server-owned trusted target/context
        v                            |
operator_api.dispatch               |
        |                            |
        v                            |
Trusted v0.3 production bundle -----+
        |
        +--> trusted read backends
        +--> Operation Store writes
        +--> trusted Vertical operation.start
        +--> Decision/Notification writes
        +--> Feature Truth / Feature Event gateway
        +--> DurableVerticalFeaturePersistGateway
        +--> Effect Lineage + launch/recovery gateway
```

The Responses adapter terminates provider protocol semantics. The canonical dispatcher terminates canonical API semantics. The production bundle terminates trusted runtime composition. These are deliberately separate boundaries.

### 4.2 Planned components

The implementation should introduce components conceptually equivalent to:

- `scripts/operator_openai_responses.py`
  - fixed supported tool registry;
  - stable adapter identity/version;
  - strict tool schemas;
  - Responses function-call parser and function-call-output encoder;
  - canonical request builder;
  - adapter protocol errors;
  - production builder that consumes trusted runtime dependencies.
- `scripts/operator_openai_responses_journal.py`
  - durable call binding/result receipt over the existing protected Operator Store runtime;
  - no lifecycle/effect/dispatch/Persist authority.
- `scripts/operator_openai_responses_runtime.py`
  - official OpenAI SDK host loop for synchronous, streaming, retrieval and optional background Responses lifecycle;
  - server-owned model/project credentials and runtime configuration.
- `scripts/operator_openai_responses_conformance.py`
  - independent canonical-conformance adapter driver over Responses protocol objects/events;
  - no MCP delegate and no direct canonical-dispatch shortcut.
- `scripts/validate_operator_openai_responses.py`
  - deterministic protocol/security/conformance/failure validation.
- public runtime packaging/documentation entries required by repository convention.

Exact filenames may change during Plan if repository layout has evolved, but the boundaries above are normative.

## 5. Supported OpenAI Responses protocol profile

### 5.1 Official protocol baseline

This Design was checked against the OpenAI official Function Calling, Streaming Responses, Background Mode and Conversation State documentation on 2026-08-11:

- <https://developers.openai.com/api/docs/guides/function-calling>
- <https://developers.openai.com/api/docs/guides/streaming-responses>
- <https://developers.openai.com/api/docs/guides/background>
- <https://developers.openai.com/api/docs/guides/conversation-state>

The supported adapter intentionally pins a narrower profile than the complete Responses API so later provider extensions cannot silently expand Operator authority.

### 5.2 Function tool definition shape

Every Operator tool supplied to `responses.create` is an OpenAI Responses `type: "function"` tool with this closed shape:

```json
{
  "type": "function",
  "name": "<fixed name>",
  "description": "<fixed bounded description>",
  "parameters": { "...": "strict JSON schema" },
  "strict": true
}
```

Rules:

- all tools use `strict: true`;
- every object schema sets `additionalProperties: false`;
- every declared property is listed in `required`, as required by OpenAI strict mode;
- when the adapter needs a semantically optional property, its schema uses a nullable type and still lists the property in `required`, or the adapter omits that property from the public tool and supplies the canonical default server-side;
- the implementation does not rely on Responses' best-effort strict-schema normalization;
- tool definitions are code-owned and cannot be replaced by prompt/model/tool arguments.

### 5.3 Accepted function-call item

The adapter executes only a completed Responses output item whose semantic type is exactly `function_call` and whose normalized representation contains:

```json
{
  "type": "function_call",
  "call_id": "<non-empty bounded string>",
  "name": "<fixed registered tool name>",
  "arguments": "<JSON string>",
  "id": "<provider item id, diagnostic only>",
  "status": "<documented provider status when present>"
}
```

Normative fields for execution are `type`, `call_id`, `name`, and the final JSON-encoded `arguments`. `id` and `status` are accepted documented provider metadata and are diagnostic only.

The adapter must not execute:

- `custom_tool_call`;
- program/programmatic-tool items;
- hosted MCP calls;
- built-in-tool calls;
- unknown future item kinds;
- an incomplete/partial `function_call`;
- a `function_call` missing or empty `call_id`;
- a `function_call` containing an unknown field under this adapter protocol version.

Unknown non-function output-item kinds are non-authoritative and may be preserved for Responses conversation continuity, but they never enter canonical dispatch. Unknown fields on a candidate `function_call` fail the adapter turn closed so a provider protocol expansion cannot silently change executable semantics. A future deliberately supported shape requires an adapter protocol-version change and review.

### 5.4 Arguments

`arguments` must be a complete JSON string that parses to exactly one object accepted by the registered tool's strict schema. No coercion from free text, JSON repair, fuzzy field matching, alias matching, or prompt-based recovery is allowed before semantic dispatch.

Malformed JSON or schema-invalid arguments produce a bounded adapter error and zero canonical backend invocations.

### 5.5 Function-call output correlation

For every accepted call the adapter returns a Responses input item:

```json
{
  "type": "function_call_output",
  "call_id": "<exact accepted call_id>",
  "output": "<JSON string>"
}
```

For a call that reached canonical dispatch, `output` is the serialized canonical `ai-sdlc.operator/v1` response envelope without converting its machine-readable error code into provider-specific prose.

For failures before canonical dispatch, such as malformed arguments or unknown tool name, `output` is a bounded adapter-protocol error object and must not pretend that canonical dispatch executed.

The output is correlated only by the exact provider `call_id`; adapter code never substitutes the Responses item `id` or provider request id.

### 5.6 Parallel and multiple tool calls

The supported write-capable production profile sets:

```text
parallel_tool_calls = false
```

The OpenAI official contract states that this constrains a model response to zero or one tool call. This Design deliberately chooses that profile because the frozen Operator API has no cross-tool transaction semantics and a model-generated batch must not become an implicit write transaction.

The collector nevertheless validates the complete terminal Responses output before executing any call. If a response contains more than one executable `function_call` despite the supported profile, the entire response is a protocol violation: **zero calls are dispatched** and the host reports a bounded fail-closed adapter error. Thus a malformed or future provider batch cannot partially execute writes.

This is distinct from duplicate delivery of the same valid response/call across retrieval, network retry, or process restart; duplicate delivery is handled by the durable call journal in section 8.

### 5.7 Streaming completion boundary

OpenAI streaming can emit `response.output_item.added`, `response.function_call_arguments.delta`, `response.function_call_arguments.done`, and `response.output_item.done` while function arguments are being generated.

The adapter may collect those events for diagnostics/progress, but **no canonical invocation is allowed from a delta or an in-progress item**. The collector waits until the function call is complete and the response is terminal enough to prove the final set of output items. It then validates the final call item and the zero-or-one-call rule before dispatching.

If the stream is interrupted before that completion boundary, no tool call executes. A later retrieval/replay of a completed Responses object is processed through the same collector and durable call journal.

### 5.8 Background / retrieval profile

The host may support `background: true` for a server-owned Responses request profile. Background execution is provider/model execution only; it grants no Operator authority. The host polls/retrieves the provider Responses object until a terminal state, then processes the terminal output through the same collector.

Retrieval, re-retrieval, browser/session reconnect, or background polling never directly invokes a canonical capability. Only a completed function-call item crossing the adapter boundary can propose one.

### 5.9 Conversation state

The adapter may use `previous_response_id`, stored Responses objects, Conversations API state, or manual Responses history according to the host's provider configuration. Those mechanisms preserve model conversation state only.

When history is manually managed, provider-required response output items may be preserved/resubmitted, but replay of a prior function-call item still passes through the durable call journal. Provider conversation storage is never Operator state and cannot override the protected Operator Store.

## 6. Stable capability-to-tool mapping

The production Responses profile registers exactly these ten tools:

| Responses function tool | Canonical capability | Model-visible arguments | Server/runtime-owned material |
|---|---|---|---|
| `aisdlc_v1_system_capabilities` | `system.capabilities` | `api_version` | adapter registration, trusted scope |
| `aisdlc_v1_feature_status` | `feature.status` | `api_version`, `feature_id` | target repository, exact trusted Feature ref |
| `aisdlc_v1_operator_inbox` | `operator.inbox` | `api_version` | repository/Feature scope, principal |
| `aisdlc_v1_operation_status` | `operation.status` | `api_version`, `operation_id` | Store repository/ref, authorization scope |
| `aisdlc_v1_decision_list` | `decision.list` | `api_version` | repository/Feature scope, principal |
| `aisdlc_v1_notification_list` | `notification.list` | `api_version` | repository/Feature scope, principal |
| `aisdlc_v1_operation_start` | `operation.start` | `api_version`, `feature_id`, `expected_feature_revision`, `mode` | target repository/ref, Vertical profile, idempotency, trusted Feature verification |
| `aisdlc_v1_operation_cancel` | `operation.cancel` | `api_version`, `operation_id`, `reason` | Operation ownership lookup, scope, idempotency |
| `aisdlc_v1_decision_respond` | `decision.respond` | `api_version`, `decision_id`, `response` | responder identity, policy, Decision/Feature binding, idempotency |
| `aisdlc_v1_notification_ack` | `notification.ack` | `api_version`, `notification_id` | acknowledging principal/client scope, idempotency |

The names are stable OpenAI-safe function names and are intentionally not the MCP tool registry.

No production Responses tool is registered for `project.inspect` or `operation.resume`. The adapter-facing backend map is filtered to the ten capabilities above before it is handed to canonical dispatch. Consequently `system.capabilities` can still describe all known canonical capabilities, while capabilities not exposed/backed in this adapter profile remain honestly unavailable.

### 6.1 Strict argument schemas

Every tool has a dedicated schema rather than a generic `target`/`context`/`payload` bag. At minimum:

- `api_version`: required bounded string for real version negotiation;
- `feature_id`: canonical Feature-id pattern; only for Feature selection;
- `operation_id`: canonical bounded identifier; only for operation lookup;
- `expected_feature_revision`: non-negative integer;
- `mode`: enum `AUTO | ASSISTED`;
- `reason`: bounded string, maximum 512 characters;
- `decision_id`: canonical bounded identifier;
- `response`: bounded string matching the canonical Decision response limit;
- `notification_id`: canonical bounded identifier.

All schemas reject additional properties. Arguments such as `repository`, `target_ref`, `store_repository`, `state_ref`, `credential`, `token`, `policy`, `role`, `generation`, `candidate_head_sha`, `external_dispatch_key`, raw Event data, or backend selector do not exist in the model-visible schema.

If a model/provider nevertheless supplies one, the runtime schema validator rejects the entire call before canonical dispatch.

## 7. Canonical request construction

### 7.1 Server-owned target resolution

The supported production adapter instance is constructed from trusted registration/configuration and one or more trusted Feature bindings. For the v0.3 write profile, each selected Feature must resolve to the production Vertical bundle for that exact trusted Feature/ref.

`feature_id` is only a selector into that server-owned allowlist. It never determines repository URL, Git ref, Store ref, credentials or runtime profile.

For operation/Decision/Notification identifiers, the trusted runtime reads durable Store truth and verifies the referenced object is within the registered repository/Feature/principal scope. The model does not provide a parallel target claim.

### 7.2 Request identity

For an accepted Responses call, the adapter derives a stable call key:

```text
responses_call_key = H(
  adapter_registration_id,
  provider_scope_id,
  call_id
)
```

where `adapter_registration_id` and `provider_scope_id` are trusted server configuration and `call_id` is provider correlation material.

The canonical `request_id` is a bounded deterministic identifier derived from this call key. It is diagnostic/correlation identity, not authorization.

### 7.3 Semantic-write idempotency

For every write tool, the adapter derives rather than accepts:

```text
idempotency_key = H("openai-responses-write/v1", responses_call_key)
```

The model cannot choose or override it.

The idempotency key is only the canonical request identity for retry convergence. It is not:

- Operation identity;
- semantic effect identity;
- Effect Lineage identity;
- `external_dispatch_key`;
- launch authorization;
- Feature Event identity;
- Persist authority.

Those identities remain derived/verified by their existing trusted layers.

### 7.4 Trusted client identity

The canonical request sets:

```text
client_identity.adapter_id = ai-sdlc.openai.responses
```

A represented human principal, when present, is injected from the authenticated server/session boundary. It is never copied from model text or tool arguments.

Trusted runtime identity and authorization policy remain outside the canonical client-writable envelope and are supplied as canonical `trusted_context` by the trusted production composition.

## 8. Durable Responses call binding and replay

### 8.1 Why a durable adapter journal is required

`call_id` is useful provider correlation but is not itself trustworthy enough to be the safety mechanism. Provider retries, response retrieval, duplicate delivery, a lost `function_call_output` ACK, or a fresh process can all cause the same call to be observed again.

The adapter therefore persists a bounded call binding in the **same protected Operator Store state ref/runtime** used by the production composition. It does not introduce a separate database/ref/credential or a new lifecycle authority.

A conceptual location is:

```text
state/operator/v1/adapter-calls/openai-responses/<responses-call-key>.json
state/operator/v1/adapter-call-results/openai-responses/<responses-call-key>.json
```

Exact paths/schema names are implementation-reviewable.

### 8.2 Immutable binding

Before canonical dispatch, CAS commits an immutable binding containing at least:

```text
schema_version
adapter_id
adapter_protocol_version
trusted adapter_registration_id digest
trusted provider_scope_id digest
responses_call_key
call_id
tool_name
normalized_arguments_digest
canonical_request_id
canonical_request_digest
canonical_idempotency_key when write
created_at
```

Provider response id/item id may be recorded as bounded diagnostics but are not part of authority.

Replay rules:

- no existing binding -> create it before semantic dispatch;
- identical binding -> replay/recovery path;
- same call key with different tool name, normalized arguments or canonical request semantics -> fail closed as `RESPONSES_CALL_ID_CONFLICT`; do not dispatch either the conflicting request or a second semantic write.

### 8.3 Result receipt

After canonical dispatch, the adapter stores a result receipt bound to the immutable call binding and canonical response digest; the bounded canonical response JSON needed to reproduce the `function_call_output` is retained according to Store schema limits.

If delivery to OpenAI succeeds, no Operator mutation follows from the provider ACK.

If provider delivery ACK is lost:

- a replay with an existing result receipt returns the same stored function-call output;
- a crash after canonical semantic commit but before result receipt causes recovery to re-dispatch the **same canonical request and same idempotency key**, allowing the trusted Store/domain command to converge before the result receipt is recorded;
- recovery must never mint a new call key or new canonical idempotency key merely because the process/session changed.

The call journal is transport replay state only. It cannot mark an Operation DONE, authorize launch, resolve UNKNOWN, respond to a Decision, acknowledge a Notification, or Persist a Feature Event except through the canonical backend originally selected by the fixed tool mapping.

## 9. Identity model and separation

The implementation must maintain these distinct identities:

| Identity | Source | Purpose | Must not become |
|---|---|---|---|
| OpenAI `response.id` | provider | response retrieval/diagnostics | authorization or semantic effect identity |
| OpenAI function `call_id` | provider | tool-output correlation and durable replay key input | lifecycle/effect/launch authority |
| Responses item `id` | provider | stream/item diagnostics | idempotency or authorization authority |
| canonical `request_id` | adapter-derived | canonical correlation | Operation/effect identity |
| canonical `idempotency_key` | adapter-derived | semantic-write retry convergence | effect/dispatch identity |
| `operation_id` | trusted Operation Store semantics | durable orchestration identity | provider session identity |
| Operation generation | trusted Store | current orchestration owner/fencing | semantic effect discriminator |
| `semantic_effect_key` | trusted lifecycle/task/candidate semantics | exact external semantic reservation | provider/tool-call identity |
| `effect_lineage_id` | trusted durable causal provenance | cross-revision predecessor/successor safety | model/provider identity |
| `external_dispatch_key` | trusted effect reservation | stable external launch identity | adapter/provider request id |

A model generating the same or different text/arguments cannot directly select any of the trusted effect identities.

## 10. Trusted context boundary

The production builder owns and validates all of the following outside model-visible tool arguments:

- target repository;
- Feature -> exact trusted ref mapping;
- Store repository;
- protected Store state ref;
- target and Store credentials;
- GitHub App / installation identity;
- target read identity/token;
- authorization policy and principal scope;
- Decision policy verifier;
- trusted Feature truth / Feature Event gateway;
- Persist authority/gateway;
- Worker dispatch gateway/credentials;
- candidate verification dependencies;
- supported Vertical runtime profile;
- trusted adapter registration and adapter identity/version;
- OpenAI API credential, organization/project/provider scope configuration;
- supported OpenAI model/configuration profile.

The model may propose only one of the ten fixed capability invocations with its bounded arguments.

Any attempt to smuggle trusted fields through tool arguments, prompt text, provider metadata, tool output from another client, or a raw canonical envelope fails closed.

## 11. Production runtime composition

### 11.1 Existing merged foundations

At Design authoring time, the canonical API (#209), MCP adapter (#211), Operation Store (#215), Vertical Loop (#217), Effect Lineage (#228), and Decisions/Notifications/Inbox (#230) are already merged foundations.

### 11.2 Current unmerged production workstreams are dependencies, not main facts

At Design authoring time, PRs #245, #247, #249, #251, #253 and #255 remain production-runtime workstreams outside `main`. This Design does not copy their implementations into the adapter branch and does not claim they are already shipped.

Implementation must re-read their eventual merged/approved contracts and bind to the final equivalents of:

- #245 trusted production target/Store configuration and adapter backend composition;
- #247 trusted Feature Event / Decision Feature Truth gateway;
- #249 `DurableVerticalFeaturePersistGateway` using the exact production Store runtime;
- #251 deterministic Persist reconciliation classification;
- #253 integrated adapter + Vertical + Decision/Notification + Persist composition with the frozen write slice and server-only `operation.resume`;
- #255 stale recorded callback convergence when/if that remediation lands on the implementation baseline.

If any interface changes before Plan/Implementation, the implementation must adapt to the reviewed merged interface rather than freezing this Design to a transient branch implementation.

### 11.3 One authority composition

The Responses production builder receives a trusted v0.3 production bundle; it does not construct another Feature Event gateway, Persist gateway, dispatcher, or Store ref.

For each write-capable Feature binding it must prove:

- adapter backends and Vertical executor share the intended protected Operator Store runtime;
- `operation.start` is the trusted Vertical start backend, not a raw Store-only start shortcut;
- `operation.resume` is absent from the adapter-facing backend map;
- Decision/Notification writes share the same trusted Store domain and protected policy verification;
- Vertical Persist uses the existing durable Persist gateway and exact trusted Feature Event transport;
- the Responses call journal is bound to the same protected Store authority and cannot select another state ref.

### 11.4 No second Persist linearization

The adapter does not write Feature Event files or Feature Manifests itself.

A Worker result can lead toward Feature progression only through the existing trusted translator -> Feature Event gateway -> durable Vertical Persist sequence. The adapter cannot bypass `DurableVerticalFeaturePersistGateway`, cannot treat a provider tool-output ACK as Persist confirmation, and cannot turn a Decision response directly into an arbitrary Feature Event.

### 11.5 No second dispatch authority

The adapter never calls a Worker launcher directly. `operation.start` enters the supported Vertical runtime. External effect reservation, Effect Lineage checks, generation claim, `dispatch.launch.authorized`, launch gateway and receipt reconciliation remain in the trusted Vertical/Store runtime.

## 12. End-to-end request/response flow

### 12.1 Read flow

```text
Responses terminal function_call
→ validate supported protocol profile
→ validate exact tool schema
→ create/reuse durable call binding
→ fixed tool mapping
→ resolve trusted scope/Feature binding
→ build canonical request
→ operator_api.dispatch
→ trusted read backend
→ canonical response envelope
→ durable result receipt
→ function_call_output(call_id, canonical JSON)
```

### 12.2 `operation.start`

```text
function_call aisdlc_v1_operation_start
→ feature_id selector + expected revision + mode
→ trusted feature binding resolves repository/exact ref
→ fresh trusted Feature verification
→ adapter-derived canonical idempotency key
→ canonical operation.start
→ trusted production Operation Store claim
→ supported Vertical start backend auto-advances according to policy
→ all later external work uses existing Effect Lineage/launch/Persist semantics
```

The model cannot choose the Vertical operation profile, target ref, task role, candidate head, Worker runtime, dispatch key, Store ref, or credentials.

### 12.3 `operation.cancel`

The model provides only `operation_id` and a bounded reason. Trusted runtime resolves the durable Operation and verifies it is in the authenticated adapter/principal scope.

Cancellation preserves the existing launch-linearization ordering:

- cancellation durable before an unlinearized launch prevents authorization/launch;
- an exact launch already durably `dispatch.launch.authorized` is not retroactively erased;
- cancellation fences later unlinearized work and later automatic lifecycle progression unless an explicit trusted recovery/adoption rule permits it.

The adapter does not promise that cancellation can revoke an already-linearized external side effect.

### 12.4 `decision.respond`

The model supplies only exact `decision_id` and bounded `response` text/choice.

Trusted runtime must re-read the durable Decision and verify:

- Decision exists and is pending as required;
- repository/Feature/Operation scope;
- expected revision/generation/candidate bindings;
- current policy and allowed choices;
- trusted responder principal/client identity;
- expiry and any authorization constraints.

The adapter cannot supply `authorized_action`, Feature Event, changes, Gate verdict, responder identity, candidate identity, generation, revision, policy, or expiry.

A successful Decision response records the bounded durable Decision fact through the trusted coordinator. Any subsequent orchestration action remains server-side trusted logic.

### 12.5 `notification.ack`

The model supplies only `notification_id`. Trusted runtime verifies scope and records durable acknowledgement identity using the server-authenticated principal and fixed adapter identity.

Acknowledgement is receipt state only. It does not PASS a Gate, advance lifecycle, resolve a Decision, cancel/start an Operation, authorize a Worker, or Persist a Feature Event.

## 13. Effect-safety inheritance

The Responses adapter must preserve, not reinterpret, all existing/frozen effect invariants.

### 13.1 Generation-independent semantic identity

Operation generation changes orchestration ownership only. The adapter cannot put `call_id`, provider response id, process/session id, retry number, or generation into the semantic effect material in order to manufacture a new external effect.

### 13.2 Stable external dispatch identity

`external_dispatch_key` remains derived/bound by trusted effect reservation semantics and survives generation takeover. A new Responses call or new provider session cannot mint a new dispatch identity for the same exact semantic effect.

### 13.3 Launch linearization

`dispatch.launch.authorized` remains the only trusted launch linearization point. A provider tool call, canonical `operation.start`, model text, adapter journal binding, or provider ACK is not a launch authorization.

### 13.4 Cancellation fencing

The adapter relies on current Store/Vertical cancellation fencing and does not add an adapter-local cancellation flag as authority.

### 13.5 UNKNOWN

If trusted launch-receipt lookup is `UNKNOWN`, the Operation remains fail-closed according to the production runtime. The Responses host must surface canonical `BLOCKED`/status truth and must not respond to provider uncertainty by creating another call, Operation generation, reservation, or external launch.

### 13.6 Lost ACK same-key recovery

A lost provider ACK for `function_call_output` replays the same Responses call binding and canonical idempotency key. A lost external dispatch ACK remains a Vertical/dispatch-gateway recovery problem using the same stable `external_dispatch_key`. The adapter must never conflate these two ACK domains.

### 13.7 No speculative retry

Provider/API retry may cause a call to be observed again, but no adapter retry path may bypass Store deduplication, Effect Lineage, launch receipt lookup, or Persist state. Unknown external launch state never authorizes a speculative new launch.

### 13.8 Effect Lineage predecessor fencing

If candidate/revision change proposes a successor exact semantic effect while an overlapping predecessor is unresolved, the trusted planner remains responsible for durable successor proposal + `BLOCKED` convergence. The Responses adapter has no operation that can force reservation, external-dispatch-key creation, launch authorization, or external launch of that successor.

### 13.9 Stale callback / stale candidate

A stale callback or stale candidate cannot become fresh authority because it is observed through a newer Responses session. Trusted candidate/Feature verification and lineage rules remain decisive. The adapter only reports the canonical result/status.

### 13.10 Persist ordering

Persist authority remains ordered by the trusted runtime's requested/linearized/confirmed facts. Provider output delivery does not reorder, confirm, or replace those facts.

## 14. Responses lifecycle, failure and recovery semantics

### 14.1 Synchronous response

The host receives a terminal Responses object, collects its completed output items, validates the supported profile, then processes zero or one accepted function call.

### 14.2 Streaming response

The host buffers function-call argument deltas but performs no semantic work until completion as defined in section 5.7. Interrupted partial streams cause zero canonical invocations.

### 14.3 Background response

The host may poll/retrieve until the provider response leaves queued/in-progress states. Only the terminal collected function call can be processed.

### 14.4 Network timeout before any complete call is known

No Operator action is inferred from the timeout. If a response id is available, retrieve it. If not, a provider-level request may be retried according to host policy, but any later tool call still starts at the durable adapter/Operator Store boundary; provider retry itself grants no semantic-write authority.

### 14.5 Timeout after canonical execution but before function output delivery

Replay the same call binding. If a result receipt exists, return it. Otherwise re-dispatch exactly the same canonical request/idempotency key and converge through trusted Store semantics before storing the result receipt.

### 14.6 Duplicate delivery

Exact duplicate call delivery across retrieval/session/process boundaries is idempotent through the durable call binding. Conflicting reuse of the same call key fails closed.

### 14.7 Fresh process / new chat session

No in-memory session data is needed to establish Operator truth. `operator.inbox`, `operation.status`, Decision/Notification reads, the protected Store, and durable Responses call bindings allow recovery. A new process must not regenerate a prior write with a new key when the prior `call_id` is available.

### 14.8 Provider response replay/retrieval

Retrieving the same Responses object repeatedly can reproduce the same function call but cannot reproduce a second semantic effect. The adapter journal identifies exact replay before domain execution.

### 14.9 Malformed/unknown tool

- unknown tool name with a valid `call_id`: bounded adapter error output; zero canonical dispatch;
- missing/invalid `call_id`: fail the adapter turn; do not invent a correlation id or execute;
- malformed JSON/schema: bounded adapter error; zero canonical dispatch;
- unsupported canonical API version supplied through a known tool: build the canonical request and allow canonical dispatch to return `UNSUPPORTED_API_VERSION` before backend semantic work.

## 15. Security model

Security invariants are enforced at provider schema, adapter parser, trusted context resolution, canonical dispatch and production backend boundaries.

Required invariants:

- fixed code-owned tool allowlist;
- strict closed schemas;
- no generic function router exposed to the model;
- no raw target repository/ref input;
- no raw Store ref/credential/policy/runtime-profile input;
- no model-selected backend/module/URL/workflow/shell command;
- server-authenticated principal and adapter identity;
- trusted Feature binding and exact ref verification;
- Store-backed idempotency/replay before writes;
- protected-state CAS for adapter journal and Operator state;
- structured bounded errors without secrets/credentials/raw exception dumps;
- OpenAI API credentials remain server-side and are not logged in tool outputs;
- Responses/provider metadata is never authorization evidence;
- no hidden lifecycle authority in notification acknowledgement;
- no Decision response can directly construct arbitrary Event changes;
- no automatic Gate PASS/WAIVE/merge/release capability.

## 16. Compatibility

This Feature is additive.

- MCP remains the existing independent MCP stdio adapter and stays read-only unless a separate reviewed Feature changes it.
- canonical `ai-sdlc.operator/v1` remains unchanged by adapter-specific transport concerns;
- existing Operation/Decision/Notification Store formats remain authoritative;
- adapter call-journal data is namespaced transport replay metadata and must be ignored by canonical lifecycle/effect projection code;
- existing target installations need trusted server registration/configuration for the new adapter but no Feature Manifest schema migration;
- OpenAI provider protocol changes outside the pinned profile fail closed until intentionally reviewed;
- no release-version change is part of this Feature.

## 17. Independent conformance architecture

### 17.1 Driver independence

`OpenAIResponsesConformanceAdapter` must not:

- call `operator_api.dispatch` directly;
- delegate to `McpStdioConformanceAdapter`;
- delegate to direct/json-roundtrip canonical fixture adapters;
- invoke the production backend functions while skipping Responses parsing;
- merely rename MCP tool names.

It must construct Responses-shaped function-call items/events, pass them through the same production tool registry/parser/call journal/canonical request builder/output encoder used by the supported adapter, and recover the result from the resulting `function_call_output` object.

The deterministic driver may replace the remote OpenAI HTTP transport with a protocol fixture because deterministic CI must not require credentials/billing, but the fixture boundary is on the **provider side of the Responses protocol**, not behind the adapter. Production code must separately use the official OpenAI SDK/Responses endpoint.

Later real dogfood evidence must exercise the actual OpenAI Responses service and supported production runtime; deterministic conformance does not substitute for that dogfood evidence.

### 17.2 Common canonical conformance

The same frozen assertions used for MCP are driven through the Responses adapter for:

- `system.capabilities`;
- `feature.status`;
- `operator.inbox`;
- `operation.status`;
- `decision.list`;
- `notification.list`;
- version negotiation;
- structured errors;
- adapter identity propagation;
- unavailable/unsupported capability semantics where applicable.

The Responses driver owns transport conversion; shared canonical assertions own canonical semantics.

### 17.3 Write conformance

Separate write fixtures run through the production-shaped Responses adapter plus trusted deterministic Store/Vertical/gateway doubles and prove:

- operation start claim/idempotency;
- Vertical start composition;
- cancellation fencing;
- bounded Decision response;
- durable Notification ack;
- process/replay convergence;
- no duplicate external semantic effect.

## 18. Deterministic test matrix

Implementation must include at least the following tests. "External launches" is counted at the trusted fake launch gateway, not inferred from adapter return values.

| Case | Expected proof |
|---|---|
| tool registry exactness | exactly ten supported Responses functions; no generic capability/router tool |
| strict schemas | all objects closed; all declared properties required/nullable as needed; `strict: true` |
| common canonical subset | six frozen capabilities pass through Responses driver |
| write slice | start/cancel/respond/ack pass through Responses driver and production-shaped backends |
| material independence | no MCP/direct fixture delegate; distinct adapter id/transport implementation |
| malformed JSON arguments | adapter error; zero canonical/backend calls |
| schema-extra forged field | adapter error; zero canonical/backend calls |
| unknown tool | adapter error; zero canonical/backend calls |
| missing/invalid `call_id` | fail closed; zero canonical/backend calls |
| unknown field on `function_call` | fail closed under adapter protocol v1 |
| partial streaming args | zero canonical calls until terminal completed item |
| interrupted stream | zero canonical calls if completion boundary not reached |
| unexpected multiple function calls | collect-first fail closed; zero calls because `parallel_tool_calls=false` profile was violated |
| unsupported API version | canonical `UNSUPPORTED_API_VERSION`; zero semantic backend work |
| forged repository | field absent/rejected; no target override |
| forged target ref | field absent/rejected; no ref override |
| forged Store/state ref | field absent/rejected; no Store override |
| forged principal/policy/adapter identity | field absent/rejected; trusted server values preserved |
| unconfigured Feature selector | `UNAUTHORIZED`/bounded fail closed; zero write |
| stale Feature revision | canonical `STALE_REVISION`; no new Operation/effect |
| foreign Operation | trusted scope rejection; no status/cancel leak/mutation |
| unauthorized cancel | `UNAUTHORIZED`/`POLICY_DENIED`; no cancel fact |
| duplicate exact tool call | same call binding/result; no second semantic write |
| conflicting reuse of call id | adapter conflict; no second dispatch |
| lost provider function-output ACK | replay same call/output; no second semantic effect |
| crash after canonical write before result receipt | same canonical idempotency recovery; one semantic write |
| fresh process restart | Store call binding recovered; no in-memory dependency |
| Responses retrieval replay | repeated retrieval does not duplicate write |
| operation.start duplicate with new provider session but same active Feature semantics | trusted Store returns/converges to existing active Operation according to canonical rules |
| cancel-vs-launch before linearization | cancel wins; zero external launch |
| launch-authorized-before-cancel | only exact already-authorized dispatch may launch with same external key; no second dispatch |
| external lookup UNKNOWN | `BLOCKED`/fail closed; zero speculative second launch |
| lost external launch ACK | same `external_dispatch_key` lookup/recovery; no new external identity |
| generation takeover | same semantic reservation/external key; no generation-derived duplicate effect |
| candidate stale before launch | no stale launch; canonical/Operation safety state |
| stale callback after candidate change | no translated fresh authority/Persist; no second launch |
| Effect Lineage blocked successor | successor proposal/blocked state; no new reservation/external key/launch |
| predecessor later safely resolved | only trusted lineage transition may permit successor progression |
| Decision invalid choice | fail closed; no Decision response fact |
| Decision stale revision/generation/candidate | fail closed; no hidden Feature Event |
| Decision expired/current-policy mismatch | fail closed according to trusted policy |
| notification ack duplicate | durable idempotent acknowledgement; no lifecycle mutation |
| Persist requested but not linearized | recovery follows durable Persist gateway; provider retry irrelevant |
| Persist linearized but ACK lost | durable same Persist recovery; no second Persist authority path |
| deterministic Persist rejection | current production classification converges to reviewed blocked/failure state |
| no second external effect | adversarial aggregate assertion: external launch counter <= 1 per semantic effect/lineage rules |
| secret redaction | provider/backend errors never expose credentials/tokens |
| public packaging | supported runtime entrypoint imports without repository-internal-only path assumptions |

## 19. Observability

Safe diagnostics may record:

- adapter id/version;
- Responses response id/item id/call id only as correlation values;
- hashed provider-scope/registration identity;
- tool name;
- canonical request id/capability;
- Operation id when authorized;
- canonical outcome/error code;
- call-journal state;
- latency/retry/retrieval counters.

Logs must not include OpenAI API keys, GitHub tokens, raw authorization headers, Store credentials, policy contents, unrestricted prompts/tool payloads, Decision secrets, or raw backend exceptions.

Observability is never lifecycle truth or authorization evidence.

## 20. Implementation decomposition

Plan should decompose implementation without crossing authority boundaries:

1. **Responses protocol registry and strict schemas**
   - stable adapter identity/version;
   - ten fixed function definitions;
   - schema validator and protocol compatibility tests.
2. **Protocol collector/encoder**
   - terminal function-call parsing;
   - streaming aggregation boundary;
   - zero-or-one-call validation;
   - function-call-output encoding.
3. **Trusted request builder**
   - feature-scoped trusted registration;
   - fixed capability mapping;
   - server principal/client identity;
   - deterministic request/idempotency derivation.
4. **Durable call journal**
   - same protected Store runtime/ref;
   - CAS binding/result receipt;
   - restart/lost-ACK/conflict recovery.
5. **Production runtime binding**
   - consume final trusted production bundle interfaces;
   - exact ten-capability backend filtering;
   - Vertical `operation.start` only;
   - Decision/Notification write integration;
   - no `operation.resume` leak.
6. **Official OpenAI Responses host**
   - synchronous create;
   - streaming collector;
   - retrieval/background support as selected by Plan;
   - server-owned provider configuration.
7. **Independent conformance driver**
   - Responses-shaped fixture transport outside adapter;
   - shared canonical common assertions;
   - dedicated write/failure/restart/effect tests.
8. **Public runtime/package/docs validation**
   - supported invocation docs;
   - dependency pinning compatible with repository policy;
   - public distribution validators.

No implementation item includes Design Review, Gate PASS, release publication or real dogfood declaration.

## 21. Dependencies and rollout

### 21.1 Hard contract dependencies

Implementation cannot honestly claim a supported write-capable production adapter until the final reviewed/merged production runtime provides the trusted write composition this adapter consumes. The implementation must therefore re-verify #245/#247/#249/#251/#253 and relevant #255 semantics at Plan/Implementation time.

If the integrated production composition is not yet on the implementation base, adapter production-write readiness remains blocked rather than being replaced with test-only backends or copied authority code.

### 21.2 Rollout order

Recommended rollout:

1. merge/rebase onto the finalized trusted production runtime contracts;
2. land adapter protocol + strict schemas + call journal;
3. land production composition binding and exact write slice;
4. pass deterministic Responses protocol/canonical/write/effect conformance;
5. pass repository/public-runtime/security validations;
6. independently Code Review and Verification under normal lifecycle;
7. later collect real OpenAI Responses + real production runtime dogfood evidence under the release evidence process.

Deterministic conformance is necessary but not sufficient for release dogfood.

## 22. Supported OpenAI Responses Adapter criteria

The label **Supported OpenAI Responses Adapter** is allowed only when all of these are true on the reviewed candidate:

- adapter identity is exactly `ai-sdlc.openai.responses` with explicit adapter protocol version;
- production host crosses the real OpenAI Responses function-tool protocol boundary using the official SDK/endpoint contract;
- implementation is materially independent from MCP transport code and fixture canonical adapters;
- ten-tool production registry is fixed and contains the six common capabilities plus the four required writes;
- strict schemas and pinned protocol compatibility rules pass;
- canonical common conformance passes through the independent Responses driver;
- write conformance passes through the same adapter path;
- production adapter consumes trusted Store/Vertical/Decision/Notification/Persist composition and does not replace it;
- durable call replay/lost-ACK/process-restart tests pass;
- deterministic effect-safety adversarial tests pass, including UNKNOWN, candidate stale, lineage blocking and no-second-effect assertions;
- public runtime packaging/installation validation passes;
- no prohibited capability/authority escape hatch is present.

Real service dogfood is a later required release evidence item. Passing the criteria above does not by itself claim the v0.3 release is ready.

## 23. Explicit non-authority guarantees

The following statements are normative invariants of this Design:

1. A model-selected function call is a **proposal to invoke one fixed canonical capability**, not authorization to mutate lifecycle state.
2. OpenAI `call_id`, response id, item id, model text and function arguments are never trusted authorization evidence.
3. Target repository/ref, Store repository/ref, credentials, runtime profile, policy, principal and adapter registration are server-owned.
4. `operation.start` cannot directly launch a Worker; it enters the trusted Vertical runtime.
5. `operation.resume` remains server/internal orchestration and is never a Responses function tool.
6. The Responses call journal is replay metadata only and cannot become a second Feature/Operation/effect authority.
7. There is exactly one trusted launch linearization semantics: durable `dispatch.launch.authorized` in the Operator Store.
8. There is no adapter-local exception to UNKNOWN fail-closed behavior.
9. There is no adapter-local exception to Effect Lineage predecessor fencing.
10. Candidate/session/retry change cannot mint a new external effect identity.
11. Decision response cannot construct arbitrary Feature Events or Gate changes.
12. Notification acknowledgement cannot hide lifecycle authority.
13. There is exactly one trusted Vertical Persist path; provider ACKs never confirm Persist.
14. A new provider session/process cannot erase durable Operation, effect, Decision, Notification, call-replay or Persist truth.
15. No adapter result can self-PASS `design-gate`, `code-gate`, `verification-gate` or `release-gate`.

## 24. Design completion boundary

This Design is complete when an independent Design Reviewer has enough specificity to review:

- real Responses function-tool boundary and compatibility profile;
- exact capability/tool surface and strict schemas;
- canonical translation and trusted context separation;
- call identity/idempotency/replay semantics;
- production runtime composition and dependency handling;
- effect, cancellation, UNKNOWN, lineage and Persist safety inheritance;
- Decision/Notification boundaries;
- sync/stream/background/retrieval/restart behavior;
- independent conformance architecture and deterministic failure matrix;
- supported-adapter criteria and non-authority guarantees.

Authoring this document does not review or approve itself. `design-gate` remains `PENDING` until a fresh independent Design Reviewer evaluates the candidate and produces legitimate review Evidence. The Architect stops at `design-review: READY`; Plan and implementation are outside this role.
