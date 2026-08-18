# Design v2 — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

Feature: `F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001`

Issue: `#232`

Adapter identity: `ai-sdlc.openai.responses`

Adapter protocol version: `1`

Canonical API: `ai-sdlc.operator/v1`

## 1. Status and normative precedence

This document is the remediated Design candidate produced for `F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001-DESIGN-REMEDIATION-1` after Independent Design Review `4905115842` returned:

`REWORK — 0 BLOCKER / 2 MAJOR / 0 MINOR`.

It **supersedes `design.md` for the next Design Re-review** while preserving every non-conflicting protocol, capability, identity, trusted-context, effect-safety, Decision/Notification, recovery, packaging and release-boundary rule from that reviewed Design.

The following v1 areas remain normative without semantic expansion:

- genuine OpenAI Responses function-tool boundary independent from MCP;
- exact ten-tool supported registry;
- no `project.inspect` or `operation.resume` in the supported Responses tool surface;
- strict closed schemas and pinned Responses `function_call` / `call_id` / `function_call_output` profile;
- collect-before-dispatch streaming behavior and `parallel_tool_calls=false` fail-closed handling;
- durable Responses call journal in the protected Operator Store authority;
- provider identity separated from canonical idempotency, Operation identity, semantic-effect identity and `external_dispatch_key`;
- server-owned repository/ref/Store/credential/principal/policy/runtime-profile/adapter-registration context;
- one trusted `dispatch.launch.authorized` linearization point;
- generation-independent semantic effect identity, stable external dispatch identity, cancellation fencing, UNKNOWN fail-closed, lost-ACK same-key recovery and Effect Lineage predecessor fencing;
- exact bounded `decision.respond` and receipt-only `notification.ack` authority;
- no direct Feature Manifest/Event/Gate/repository/shell/policy/worker-selector escape;
- public-runtime packaging requirements and later real-service dogfood remaining distinct from deterministic Feature conformance;
- no VERSION change, no final release manifest, no #221 PASS claim and no v0.3 release-readiness claim.

Where this Design v2 conflicts with `design.md`, **Design v2 wins**. In particular, this Design replaces the old write-conformance proof boundary and the optional wording around stale recorded callback convergence.

## 2. Remediation objective

This revision closes exactly the two Design Review MAJOR findings:

1. a `Supported OpenAI Responses Adapter` must be proven through the **actual production canonical composition present on the implementation baseline**, not merely through Store/Vertical/gateway doubles;
2. durable convergence of an already-recorded stale callback, as repaired by PR #255 or a reviewed semantically equivalent implementation, is a **hard production-support prerequisite**, not an optional workstream.

No new client capability or authority is introduced by this remediation.

## 3. Revalidated dependency posture

At remediation authoring time, the production-runtime workstreams remain unmerged dependencies rather than `main` facts:

- PR #245 — trusted adapter-facing production composition;
- PR #247 — trusted Feature truth / exact Feature Event transport;
- PR #249 — `DurableVerticalFeaturePersistGateway`;
- PR #251 — deterministic Persist reconciliation classification;
- PR #253 — adapter + Vertical + Decision/Notification + Persist integrated composition;
- PR #255 — durable stale-recorded-callback convergence.

The exact PR numbers are implementation history, not permanent architecture. The normative dependency is the **reviewed semantic contract available on the implementation baseline**.

Plan and Implementation must therefore re-read the actual merged/approved interfaces. The adapter must not copy authority code from an unmerged branch merely to satisfy this Design.

## 4. Supported production composition boundary

### 4.1 One final production bundle

The supported production adapter is constructed from the final trusted v0.3 production composition available on the implementation baseline.

Conceptually:

```text
Provider-side deterministic Responses fixture OR real OpenAI Responses service
        |
        v
production Responses tool registry
        |
        v
production Responses collector/parser
        |
        v
production Responses call journal
        |
        v
production canonical request builder
        |
        v
operator_api.dispatch
        |
        v
FINAL TRUSTED PRODUCTION COMPOSITION
        |
        +--> exact trusted read backends
        +--> profile-bound Vertical operation.start backend
        +--> Operation Store operation.cancel/status
        +--> Decision coordinator
        +--> Notification coordinator
        +--> trusted Feature truth / Feature Event transport
        +--> DurableVerticalFeaturePersistGateway or reviewed final equivalent
        +--> Effect Lineage / launch / callback recovery
```

The supported proof boundary is the lower box above. Tests may instrument it, but must not replace it with Store/Vertical/Persist/Decision/Notification doubles when deciding whether the adapter is Supported.

### 4.2 Exact Store authority sharing

The final production composition must prove that the following use the same trusted protected Operator Store authority:

- Responses call journal;
- adapter canonical backends;
- Vertical executor/recovery path;
- Decision coordinator;
- Notification coordinator;
- Durable Persist gateway.

When implementation composes these as an in-process runtime object, the supported conformance lane must assert the exact intended shared runtime object where feasible. Where an interface intentionally abstracts object identity, the proof must instead establish equality of the full trusted Store binding tuple, including repository, protected state ref, checkout/remote binding, protection verifier and runtime profile constraints required by the production constructor.

A second Store ref or alternate test Store is a support failure.

### 4.3 `operation.start` backend requirement

The Responses tool `aisdlc_v1_operation_start` must traverse:

```text
Responses parser
→ fixed tool mapping
→ canonical `operation.start`
→ production canonical backend map
→ trusted profile-bound Vertical start backend
→ protected Operation Store
```

A raw Store-only `operation.start` shortcut cannot satisfy the Supported proof.

This is the direct Design proof for approved Requirement deterministic acceptance scenario #18: `operation.start` executes against the **real trusted canonical backend**, and an equivalent duplicate start converges through the production Store/canonical idempotency semantics.

### 4.4 Client write slice remains exact

The supported Responses production registry contains exactly these write tools:

- `operation.start`;
- `operation.cancel`;
- `decision.respond`;
- `notification.ack`.

`operation.resume` remains server-internal orchestration even if the Vertical runtime contains such an internal backend. The production Responses registry/backend filter must make it impossible for that server-only capability to become model-invokable.

No generic canonical dispatcher or backend selector is exposed.

### 4.5 Persist authority remains singular

The supported production lane must prove that any Feature progression reached after Vertical work uses the final reviewed trusted path:

```text
bounded Worker result
→ trusted result/callback validation
→ bounded Feature Event translation
→ Persist requested
→ Persist linearized
→ final DurableVerticalFeaturePersistGateway / reviewed equivalent
→ trusted exact Feature Event transport
→ Persist confirmed
```

The Responses adapter does not create Feature Event files, does not directly mutate Feature Manifests and does not interpret `function_call_output` delivery as Persist confirmation.

The production-composition conformance lane must observe exactly one traversal of the final Persist authority for the exercised semantic Persist and prove there is no alternate adapter-local Persist route.

### 4.6 Dispatch authority remains singular

The adapter cannot call a Worker launcher directly. External reservation, Effect Lineage checks, `dispatch.launch.authorized`, stable `external_dispatch_key`, receipt lookup and recovery remain inside the trusted Vertical/effect runtime.

The supported production lane must prove no Responses production constructor installs an alternate launcher or second dispatch authority.

### 4.7 No test-only fallback

Production composition is fail closed.

If a required trusted production backend, Store binding, Feature binding, Decision policy verifier, Persist gateway or runtime profile is absent/mismatched, production adapter construction must fail or the affected capability must remain honestly unavailable according to canonical rules.

It must never silently substitute:

- an in-memory Store;
- a test-only Store implementation;
- a fake Vertical executor;
- a fake Persist gateway;
- a fake Feature Event transport;
- direct fixture canonical dispatch;
- a test Decision/Notification backend.

Tests must contain an explicit negative assertion that the supported production builder cannot fall back to test-only authority.

## 5. Independent conformance architecture

### 5.1 Provider-side fixture remains allowed

Deterministic CI does **not** require a live or billable OpenAI request.

The independent `OpenAIResponsesConformanceAdapter` may supply Responses-shaped objects/events from a deterministic fixture only on the **provider side** of the production Responses boundary.

It must still traverse the same production:

- tool registry;
- strict schema validation;
- collector/parser;
- durable call journal;
- canonical request builder;
- `function_call_output` encoder.

It may not call `operator_api.dispatch` directly and may not delegate to MCP/direct/json-roundtrip fixture adapters.

### 5.2 Two distinct conformance lanes

Implementation must maintain two separate deterministic lanes with different proof purposes.

#### Lane A — protocol/fault-injection lane

This lane may use trusted deterministic Store/Vertical/gateway doubles after crossing the real Responses parser/translation boundary.

It exists to make rare and adversarial states controllable, including:

- malformed provider objects;
- partial/interrupted streaming;
- lost provider output ACK;
- launch UNKNOWN;
- cancellation races;
- Persist failure classification;
- Effect Lineage adversarial transitions.

**Lane A alone can never earn the `Supported OpenAI Responses Adapter` label.**

#### Lane B — supported production-composition lane

This lane is mandatory for Supported status.

It uses the independent Responses driver and deterministic provider-side Responses fixtures, but after the adapter boundary it must instantiate and traverse the **actual final production composition constructor/interfaces present on the implementation baseline**.

At the Supported proof boundary, Lane B must not replace Store/Vertical/Decision/Notification/Persist composition with test doubles.

External services may still be replaced only at already-reviewed trusted external seams where deterministic tests normally do so, for example a temporary remote-Git repository or a launch transport emulator, provided the production composition object and authority path remain real.

### 5.3 Lane B minimum proofs

Lane B must prove all of the following on one reviewed candidate:

1. the Responses driver traverses the exact production registry/parser/journal/request builder/output encoder;
2. `operation.start` reaches the real trusted profile-bound Vertical canonical backend;
3. adapter, Vertical, Decision, Notification and Persist use the same protected Store authority;
4. `operation.resume` is absent from the model-facing registry/backend map;
5. the client write slice is exactly start/cancel/respond/ack;
6. the production Persist path crosses the final durable Persist gateway and trusted Feature Event transport exactly once for the exercised semantic Persist;
7. no second Persist linearization point exists;
8. no second dispatch authority exists;
9. production registration/composition cannot silently fall back to test-only authority;
10. an exact equivalent `operation.start` replay/duplicate converges to the same production semantic Operation outcome according to canonical rules.

If any of these cannot be proven because the final production runtime is not on the implementation baseline, Supported status remains blocked.

## 6. Durable stale-recorded-callback convergence is a hard prerequisite

### 6.1 Normative semantic dependency

A supported write-capable Responses adapter **must not be declared Supported** unless its implementation baseline contains the stale-recorded-callback convergence contract currently represented by PR #255, or a separately reviewed semantically equivalent implementation.

This dependency is mandatory because an already-durable Worker callback is Operator truth that must converge deterministically after fresh Feature/ref/revision/stage/candidate validation detects that the callback is stale.

The adapter cannot paper over a runtime recovery defect by merely reporting an exception or retrying in a later provider session.

### 6.2 Required stale callback behavior

For a callback envelope that has already been durably adopted, if fresh trusted binding validation determines that Feature ref, revision, stage or candidate no longer matches the callback's authorized execution binding, the production recovery runtime must:

1. classify the bounded deterministic stale-binding result through the reviewed deterministic rejection path;
2. append exactly one durable rejection fact, such as `worker.result.rejected(code=STALE_REVISION)` under the current reviewed runtime contract;
3. converge the Operation to the reviewed stable fail-closed state, currently `BLOCKED` for the stale-revision class;
4. produce zero fresh `feature.event.translated` authority from that stale callback;
5. produce zero Persist request/linearization/transport authority from that stale callback;
6. retain all Effect Lineage predecessor fencing;
7. create no new external reservation for an overlapping successor while the predecessor remains unresolved;
8. create no new `external_dispatch_key` for that blocked successor;
9. perform no second external launch.

A newer OpenAI response/session/process does not change any of these rules.

### 6.3 Fresh-process convergence

After the durable deterministic rejection exists, a fresh process or repeated recovery pass must observe durable truth and skip reprocessing the stale callback.

The deterministic proof must assert:

- zero additional Store mutation on the fresh recovery pass;
- zero duplicate rejection fact;
- zero duplicate Feature Event translation;
- zero Persist attempt;
- zero new reservation/external key/launch.

Process memory cannot be the reason the duplicate is avoided.

### 6.4 Transient Feature-read failures remain transient

The runtime must distinguish deterministic stale-binding evidence from non-deterministic Feature-read/infrastructure failure.

A transient/unclassified read exception must:

- propagate or enter the reviewed transient waiting/retry path;
- **not** be persisted as a stale rejection merely because it occurred during fresh callback validation;
- not fabricate `STALE_REVISION`;
- not mutate durable callback truth in a way that prevents legitimate later recovery.

This distinction is part of the Supported production prerequisite.

### 6.5 Effect Lineage continuation after stale rejection

If trusted current candidate B differs from callback predecessor candidate A, durably rejecting A's stale callback does not itself resolve A's external semantic predecessor.

Any candidate-B successor work must still traverse the reviewed Effect Lineage planner. While A remains an unresolved overlapping predecessor, the only permissible successor state is the reviewed proposal/blocked form; no successor reservation, external key or launch is permitted.

## 7. Updated deterministic test matrix

All v1 protocol/security/replay/effect tests remain required. The following rows replace or strengthen the affected v1 rows.

| Case | Required proof |
|---|---|
| supported production composition | independent Responses driver traverses actual final production composition constructor/interfaces on implementation baseline |
| real `operation.start` backend | Responses start reaches trusted profile-bound Vertical canonical backend; no raw Store-only shortcut |
| real duplicate start convergence | equivalent duplicate/replay crosses same production adapter/canonical path and converges to canonical existing Operation semantics |
| exact production Store sharing | call journal + adapter + Vertical + Decision + Notification + Persist bind the same protected Store authority |
| exact client write surface | only start/cancel/respond/ack are model-invokable writes; `operation.resume` absent |
| final Persist path | exercised semantic Persist crosses final durable Persist gateway + exact trusted Feature Event transport exactly once |
| no second authority | no adapter-local Persist linearization or direct Worker launch path |
| no test fallback | missing/mismatched production dependency fails closed; no fake/in-memory authority substitution |
| stale recorded callback — first recovery | already-durable stale callback creates exactly one durable deterministic rejection and stable reviewed fail-closed/BLOCKED state |
| stale recorded callback — zero fresh lifecycle authority | stale callback creates zero `feature.event.translated` and zero Persist authority |
| stale recorded callback — fresh process | second process/recovery performs zero Store mutation and does not append duplicate rejection |
| stale recorded callback — lineage | unresolved predecessor remains fenced; successor may be proposed/blocked only, with zero new reservation/external key/launch |
| stale recorded callback — transient read failure | transient Feature-read failure is not durably misclassified as stale and remains recoverable |
| lower-level fault lane separation | tests using Store/Vertical/gateway doubles are marked fault-injection/conformance support only and cannot satisfy Supported-production criterion |

Existing rows for strict schemas, malformed calls, `call_id` conflict/replay, provider lost ACK, cancel-vs-launch, UNKNOWN, lost external ACK, generation takeover, candidate stale before launch, Decision constraints, Notification idempotency, Persist reconciliation and public packaging remain normative.

## 8. Updated implementation decomposition

Plan must decompose at least:

1. Responses protocol registry and strict schemas;
2. collector/encoder;
3. trusted request builder;
4. durable call journal on the protected Store authority;
5. production runtime binding to the final trusted composition;
6. official OpenAI Responses host;
7. independent provider-side Responses conformance driver;
8. Lane A lower-level deterministic fault-injection tests;
9. **Lane B mandatory supported production-composition conformance**;
10. durable stale-recorded-callback prerequisite validation against the implementation baseline;
11. public runtime/package/docs validation.

Plan must not schedule implementation of an adapter-local substitute for missing production runtime semantics. If the mandatory production composition or stale-callback convergence semantic contract is absent, the Feature remains blocked until the reviewed dependency is available.

## 9. Hard production-support dependencies

The `Supported OpenAI Responses Adapter` label is blocked unless the reviewed implementation baseline provides all required final equivalents of:

- trusted production target/Store configuration and adapter backend composition;
- trusted Feature truth and exact Feature Event transport;
- durable Vertical Persist gateway with restart-safe receipt recovery;
- deterministic Persist reconciliation classification;
- one integrated adapter/Vertical/Decision/Notification/Persist composition over the same protected Store authority;
- durable stale-recorded-callback convergence with deterministic-vs-transient classification and Effect Lineage preservation.

At remediation authoring time, these semantics are being developed across PRs #245/#247/#249/#251/#253/#255. Those PRs must be revalidated; being open/green is not equivalent to being part of the implementation baseline.

## 10. Updated Supported criteria

The label **Supported OpenAI Responses Adapter** is allowed only when all v1 Supported criteria still pass **and**:

- Lane B production-composition conformance passes through the independent Responses driver;
- Requirement scenario #18 is proven against the real trusted production canonical/Vertical backend;
- exact Store authority sharing across adapter/Vertical/Decision/Notification/Persist is proven;
- exact four-write surface and server-only `operation.resume` are proven on the production bundle;
- final Persist/trusted Feature Event traversal is proven without a second authority;
- production composition cannot fall back to test-only authority;
- stale-recorded-callback deterministic rejection/restart convergence is present on the implementation baseline;
- stale callback re-recovery proves zero duplicate rejection, zero translated Feature Event/Persist and zero new reservation/external key/launch;
- transient Feature-read failures remain distinct from deterministic stale binding.

A live OpenAI-hosted model call is still not required for ordinary deterministic CI. Real service dogfood remains later release evidence and is not implied by Supported Feature conformance alone.

## 11. Explicit retained non-authority guarantees

This remediation does not weaken any existing authority boundary:

1. model-selected tool calls remain proposals to invoke fixed canonical capabilities;
2. OpenAI `call_id`/response/item/model text remain transport evidence only;
3. trusted target/ref/Store/credentials/profile/policy/principal/registration remain server-owned;
4. `operation.start` enters trusted Vertical orchestration and is not a direct launch authorization;
5. `operation.resume` remains server-only;
6. call-journal state remains transport replay metadata only;
7. `dispatch.launch.authorized` remains the unique launch linearization fact;
8. UNKNOWN remains fail closed;
9. Effect Lineage predecessor fencing remains mandatory;
10. provider session/process/candidate/generation changes cannot mint a fresh external effect identity;
11. Decision response cannot synthesize arbitrary lifecycle authority;
12. Notification acknowledgement remains receipt state only;
13. the reviewed Durable Persist path remains the sole Persist authority;
14. stale callback recovery cannot be reset by a new provider session;
15. no adapter result can PASS any lifecycle Gate.

## 12. Re-review boundary

This remediation is complete as an Architect artifact when:

- MAJOR-1 is closed by the mandatory Lane B production-composition proof boundary;
- MAJOR-2 is closed by making durable stale-recorded-callback convergence a hard Supported prerequisite with the full deterministic restart/lineage/transient test contract;
- remediation evidence identifies these exact closures;
- the Feature lifecycle is returned to a state eligible for fresh independent Design Re-review.

The Architect does **not** perform that Re-review, does not PASS `design-gate`, does not enter Plan and does not write implementation code.
