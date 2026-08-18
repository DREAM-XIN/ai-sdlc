# Independent Design Re-review — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## 1. Review identity and exact candidate

Role: **Independent Design Re-reviewer**.

Feature: `F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001` / Issue #232 / PR #233.

Reviewed remediated Design candidate:

- PR head before review evidence write: `2dc28728c36a3cf69c986a96f56117134539a42d`
- `docs/features/F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001/design-v2.md` blob: `4ca3a69cc3d2496a02f859c7b202c96957103331`
- authoritative Feature state before re-review: revision `10`, `current_stage: design-review`, remediation task `DONE`, `design-gate: PENDING`
- source review: PR Review `4905115842`, verdict `REWORK — 0 BLOCKER / 2 MAJOR / 0 MINOR`

`design-v2.md` explicitly supersedes the reviewed `design.md` only for the remediated candidate while incorporating the non-conflicting v1 protocol/security/effect/authority constraints.

## 2. Verdict

**PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR**

Both prior MAJOR findings are closed by the remediated Design. No new Design-level finding is introduced by the remediation.

This verdict approves the Design candidate only. It does not prove implementation, production dependency merge state, CI, dogfood, #221, or v0.3 release readiness.

## 3. MAJOR-1 re-review — CLOSED

### Prior finding

The original Design allowed Supported write conformance to be earned with the production-shaped Responses adapter plus Store/Vertical/gateway doubles. That did not prove approved Requirement scenario #18 or real production composition.

### Remediation reviewed

Design v2 now defines two distinct conformance lanes:

- **Lane A** may use deterministic lower-level doubles for protocol and adversarial fault injection, but is explicitly forbidden from earning Supported status;
- **Lane B** is mandatory for Supported status and must drive provider-side Responses fixtures through the exact production registry/parser/call journal/request builder/output encoder and then through the actual final trusted production composition constructor/interfaces on the implementation baseline.

Lane B is required to prove:

- `operation.start` reaches the trusted profile-bound Vertical canonical backend;
- equivalent duplicate start converges through the same real production canonical/Store path;
- adapter / Vertical / Decision / Notification / Persist share the same protected Store authority;
- `operation.resume` is absent from the model-facing surface;
- the client write slice is exactly `operation.start`, `operation.cancel`, `decision.respond`, `notification.ack`;
- the exercised semantic Persist crosses the final durable Persist gateway / trusted Feature Event transport exactly once;
- no second Persist linearization or dispatch authority exists;
- missing/mismatched production dependencies fail closed instead of falling back to test-only or in-memory authority.

The Design also explicitly states that if the actual final production runtime is not present on the implementation baseline, Supported status remains blocked.

### Re-review conclusion

**CLOSED.** This is now sufficient Design specificity to ensure that lower-level test doubles can support fault injection without being mistaken for the production proof required by Requirement scenario #18.

## 4. MAJOR-2 re-review — CLOSED

### Prior finding

The original Design treated the stale-recorded-callback repair represented by PR #255 as optional (`when/if`, “relevant semantics”), allowing Supported status even if a durable stale callback could repeatedly fail/reprocess across restart.

### Remediation reviewed

Design v2 now makes durable stale-recorded-callback convergence a **hard production-support prerequisite**. Supported status is blocked unless the implementation baseline contains PR #255’s reviewed semantic contract or a separately reviewed semantically equivalent implementation.

The required deterministic contract now includes:

- an already-durable callback that becomes stale against fresh Feature/ref/revision/stage/candidate truth produces exactly one durable deterministic rejection;
- the Operation converges to the reviewed stable fail-closed/BLOCKED state for that class;
- the stale callback produces zero fresh `feature.event.translated` and zero Persist authority;
- fresh-process/repeated recovery performs zero further Store mutation and no duplicate rejection;
- unresolved Effect Lineage predecessor fencing remains intact;
- no successor reservation, new `external_dispatch_key`, or second external launch occurs while the predecessor remains unresolved;
- transient/unclassified Feature-read failure is kept distinct and is not durably misclassified as stale.

The Design further states that a stale predecessor rejection does not itself resolve the predecessor’s external semantic effect; successor candidate work must still traverse the Effect Lineage proposal/blocked path.

### Re-review conclusion

**CLOSED.** The previously optional recovery work is now an explicit mandatory Supported-runtime semantic dependency with sufficient deterministic restart and lineage proof requirements.

## 5. Dependency posture

The production-runtime PRs revalidated during remediation remain outside `main` at the observed candidate time, including #245/#247/#249/#251/#253/#255. Design v2 correctly treats their PR numbers as implementation history and the final reviewed semantic contracts/interfaces on the implementation baseline as the normative dependency.

Therefore Design PASS does **not** mean those dependencies are already available for Implementation. Plan/Implementation must re-read their actual merge/review state and block Supported production claims if required runtime semantics are absent.

## 6. Retained reviewed areas

No regression was found in the v1 areas that Design v2 incorporates by reference:

- genuine OpenAI Responses function-tool boundary independent of MCP;
- exact ten-tool supported surface;
- no `operation.resume`, generic canonical router, raw Feature Event/Manifest/Gate/shell/repository-write escape;
- strict schema / serialized arguments / exact `call_id` correlation / `function_call_output` handling;
- collect-before-dispatch streaming and multiple-call fail-closed behavior;
- durable call journal and replay/idempotency separation;
- server-owned trusted target/ref/Store/credential/principal/policy/profile/registration context;
- generation-independent semantic effect identity and stable `external_dispatch_key`;
- `dispatch.launch.authorized`, cancellation fencing, UNKNOWN fail-closed, lost-ACK same-key recovery and Effect Lineage predecessor fencing;
- bounded Decision response and receipt-only Notification ack authority;
- provider retrieval/replay/fresh-process state treated as transport evidence rather than Operator truth;
- deterministic Feature conformance kept separate from later real OpenAI service dogfood and release evidence.

## 7. Exact-head CI note

At the reviewed candidate head, the observed Required PR Gate run is `action_required` with zero jobs. This is not positive exact-head CI evidence, but it is also not treated as a semantic validation failure. Design approval is based on the Design artifacts and dependency contracts; later lifecycle stages must obtain their own required exact-head validation evidence.

## 8. Gate and handoff

Design Re-review verdict is **PASS**.

The lifecycle may now legitimately:

- approve `design-v2`;
- PASS `design-gate` using this review evidence;
- mark `design-review: DONE`;
- make `plan: READY`.

This reviewer does not author Plan, implementation, code-review, verification or acceptance evidence, and does not modify VERSION, create `release/v0.3.0.yaml`, claim #221 PASS, claim real dogfood, or claim v0.3 release readiness.
