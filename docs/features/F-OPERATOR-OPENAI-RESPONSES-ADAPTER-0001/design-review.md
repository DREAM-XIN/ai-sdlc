# Independent Design Review — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## 1. Review identity and exact candidate

Role: **Independent Design Reviewer**.

Feature: `F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001` / Issue #232 / PR #233.

Reviewed Design candidate:

- PR head containing the Design candidate: `253f937730ed0b68f5944cf08ff657ddc4f6560d`
- `docs/features/F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001/design.md` blob: `8c5818882248ff52b475d9a6e80044e7ed498233`
- authoritative Feature state re-read before review: revision `6`, `current_stage: design-review`, `design: DONE`, `design-review: READY`, `design-gate: PENDING`

The later commit that adds Design Review lifecycle/evidence does not alter the reviewed Design blob.

## 2. Verdict

**REWORK — 0 BLOCKER / 2 MAJOR / 0 MINOR**

The Design is strong on the OpenAI Responses protocol boundary, fixed capability surface, trusted-context separation, identity/idempotency separation, Effect Lineage/UNKNOWN/launch/Persist invariants, Decision/Notification authority, and provider replay/retrieval semantics. It also correctly treats the currently unmerged production runtime PRs as contract dependencies rather than `main` facts.

Two MAJOR gaps remain before the Design can satisfy the approved Requirement's supported write-adapter proof.

## 3. Revalidated dependency state

The merged canonical foundations were re-read from `main`: Canonical Operator API, MCP adapter, Operation Store, Vertical Loop, Effect Lineage, and Decisions/Notifications are accepted merged foundations.

The production runtime workstreams were re-read independently and remain outside `main` at this review:

- PR #245 — open, head `7192bf92e6672d643846dd4c0e4670c87ad75d8b`
- PR #247 — open, head `446af5ea0fdab75fcf84d8f3936d8001d801ea85`
- PR #249 — open stacked PR, head `9faa963fd49ad3ad2af9a861e1e2e61c1eb166b2`
- PR #251 — open **Draft** stacked PR, head `8d8df23afc5ec3d876aeb4ba4a44d1178bbc68f1`
- PR #253 — open **Draft** stacked integration PR, head `b95eca31433940d5a27e2db20296749dd4b19100`
- PR #255 — open **Draft**, head `d2330e08b5932051abb60a717a6ec8c126be89f4`

Their current validations were inspected, but successful CI on an unmerged dependency is not `main` authority and is not evidence that PR #233 already has production composition.

## 4. OpenAI Responses protocol review

The Design's pinned protocol profile is consistent with the current official OpenAI Responses function-calling contract reviewed on 2026-08-11:

- Responses function tools use a fixed function name plus JSON-schema `parameters` and `strict: true`;
- strict mode requires `additionalProperties: false` for objects and all declared properties to be required (nullable types are used for optional values);
- model calls are `function_call` output items carrying `name`, serialized `arguments`, and `call_id`;
- tool results are returned as `function_call_output` correlated by the exact `call_id`;
- `parallel_tool_calls: false` constrains a response turn to zero or one tool call;
- streaming function calls expose `response.output_item.added`, `response.function_call_arguments.delta`, `response.function_call_arguments.done`, and `response.output_item.done` events.

The Design correctly buffers partial arguments and performs zero semantic dispatch until a complete accepted call exists. It also fails closed on malformed calls, unsupported/unknown tool names and incompatible protocol fields. No protocol BLOCKER/MAJOR was found.

Official references reviewed:

- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/guides/streaming-responses

## 5. Findings

### MAJOR-1 — Supported write conformance can pass without crossing the real production canonical composition

The approved Requirement's deterministic acceptance scenario #18 is explicit: `operation.start` must execute against the **real trusted canonical backend**, with equivalent duplicate start converging. The Feature outcome also requires the write-capable adapter to execute the four semantic writes end-to-end through accepted canonical trusted backends.

The Design does define the intended production composition in sections 11/12/21/22, but its actual conformance architecture in section 17.3 says the write fixtures run through the production-shaped Responses adapter **plus trusted deterministic Store/Vertical/gateway doubles**. The test matrix correspondingly proves the write slice with "production-shaped backends". That is insufficient as the proof that earns the `Supported OpenAI Responses Adapter` label.

Under the current Design, all adapter/common/write/effect tests could pass while the real implementation-base production bundle is absent, miswired, filtered incorrectly, built with a different Store runtime, leaks `operation.resume`, or fails to connect the Responses request builder to the final `DurableVerticalFeaturePersistGateway` / Decision / Notification composition. PR #253's own integration tests do not close this gap for PR #233 because #253 is currently an unmerged Draft dependency and, in any event, the Responses adapter itself must be proven to traverse the integrated production boundary.

**Required Design remediation:**

1. Keep protocol-side deterministic Responses fixtures; a live/billable OpenAI call is not required for ordinary CI.
2. Add a distinct supported-adapter production-composition conformance lane in which the independent Responses driver traverses the same production registry/parser/call journal/request builder/output encoder **and the actual final production composition constructor/interfaces present on the implementation baseline**, not Store/Vertical/gateway doubles at the proof boundary.
3. That lane must prove at minimum:
   - `operation.start` reaches the real trusted profile-bound Vertical canonical backend;
   - Adapter / Vertical / Decision / Notification / Persist share the exact trusted Store runtime/ref;
   - `operation.resume` is absent from the client tool surface even if present as server-internal orchestration;
   - the four required writes are the only client write slice;
   - Persist crosses the final durable Persist gateway / trusted Feature Event transport exactly once;
   - no second dispatch or Persist authority is introduced;
   - production filtering/registration cannot silently fall back to a test-only backend.
4. Lower-level deterministic doubles remain useful for fault injection, but they cannot by themselves satisfy Requirement scenario #18 or the supported-production-composition criterion.

Until this is explicit, the Design does not prove that the second adapter is a real supported write-capable production adapter rather than a correct protocol implementation connected only to production-shaped test seams.

### MAJOR-2 — #255 stale-recorded-callback recovery semantics are not a hard Supported-adapter prerequisite

The Design correctly states the desired stale callback invariant in sections 13.9 and 18, but section 11.2 describes PR #255 as a dependency "when/if that remediation lands", and section 21.1 only requires re-verifying "relevant #255 semantics". That wording leaves the current recovery defect optional at the exact point where the Design later permits the `Supported OpenAI Responses Adapter` label.

This is material because #255 is not a speculative enhancement. The current unmerged remediation changes `process_recorded_callback()` so fresh Feature/ref/revision/stage/candidate binding failures that occur **after the callback envelope is already durable** enter the deterministic durable rejection path. Its deterministic validation requires one `worker.result.rejected` / `STALE_REVISION`, stable `BLOCKED`, zero translated Feature Event/Persist, and a fresh resume that skips the already-rejected callback with zero Store mutation. Without that semantic fix or an equivalent merged implementation, a durable stale recorded callback can remain a repeated recovery failure instead of converging to durable truth.

The current Design test row `stale callback after candidate change | no translated fresh authority/Persist; no second launch` is therefore too weak: an implementation can satisfy that one-shot safety assertion while still repeatedly throwing/reprocessing the same durable stale callback across process restart.

**Required Design remediation:**

1. Make the semantic contract repaired by #255 a **hard production-support dependency**. The exact PR number need not be immortalized as architecture, but the Supported label must be blocked unless the implementation baseline contains #255 or a reviewed semantically equivalent durable stale-recorded-callback convergence contract.
2. Extend the deterministic recovery matrix to prove, after a callback has already been durably recorded and Feature/ref/revision/stage/candidate truth becomes stale:
   - exactly one durable deterministic rejection (or reviewed equivalent) is recorded;
   - Operation converges to the intended stable `BLOCKED`/safety state;
   - a fresh process/repeated recovery does not reprocess or append duplicate rejection facts;
   - zero `feature.event.translated` / Persist authority is produced;
   - no successor bypasses Effect Lineage predecessor fencing;
   - zero new reservation / external dispatch key / second external launch is created.
3. Keep transient/non-deterministic Feature-read failures distinct from deterministic stale-binding rejection; they must not be falsely persisted as stale facts.

This must be a runtime invariant consumed by the Responses adapter, not an assumption that an optional dependency may eventually merge.

## 6. Areas reviewed with no MAJOR finding

The following Design areas are sufficiently specified for the next remediation revision:

- exact ten-tool surface: six common capabilities plus `operation.start`, `operation.cancel`, `decision.respond`, `notification.ack`;
- no `operation.resume`, raw Feature Event/Store mutation, generic canonical dispatcher, GitHub/workflow/policy/shell escape tool;
- materially independent Responses tool registry/parser/call-output path and conformance driver, with no MCP/direct-fixture delegation;
- strict separation of provider `call_id` / response ids from canonical idempotency, Operation identity, semantic effect identity and `external_dispatch_key`;
- durable replay journal in the same protected Store authority, with conflicting call-id reuse fail-closed;
- server-owned repository/ref/Store/credentials/policy/principal/runtime-profile/adapter-registration context;
- one trusted launch linearization at `dispatch.launch.authorized`;
- generation-independent semantic effect identity, stable external dispatch identity, cancellation fencing, UNKNOWN fail-closed, lost-ACK same-key recovery, no speculative retry, and Effect Lineage predecessor fencing;
- `decision.respond` as an exact bounded Decision response checked against trusted responder/revision/ref/candidate/generation/policy/expiry truth;
- `notification.ack` as durable receipt state only;
- sync/stream/background/retrieval/provider-lost-ACK/fresh-process semantics treating provider objects as transport evidence rather than Operator truth;
- public runtime packaging and later real-service dogfood kept distinct from deterministic Feature conformance.

## 7. CI note

The reviewed Design candidate head `253f937730ed0b68f5944cf08ff657ddc4f6560d` had `Validate AI-SDLC protocol` and `Required PR Gate` in `action_required` with zero useful passing jobs. As prior Issue #232 handoff already records, those runs are not treated as a semantic test failure, but they also are not positive exact-head CI evidence. A remediated Design candidate must revalidate its own exact head.

## 8. Gate and handoff

Because MAJOR findings remain:

- verdict is **REWORK**;
- `design-gate` MUST remain `PENDING`;
- this reviewer does not edit `design.md`;
- this reviewer does not act as Architect / remediation author;
- Plan and Implementation MUST NOT start from this review;
- next role is a fresh **Architect / Design Remediation Author** that closes MAJOR-1 and MAJOR-2, then hands a new exact candidate to a fresh independent Design Reviewer.

This review does not change VERSION, create `release/v0.3.0.yaml`, approve #221, claim real dogfood, or claim v0.3 release readiness.
