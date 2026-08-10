# Requirement — F-OPERATOR-VERTICAL-LOOP-0001

## 1. Purpose

Implement the fourth frozen AI-SDLC v0.3 workstream: one durable, unattended vertical Operator loop for an already-installed Feature from Implementation through independent Review, remediation/re-review when needed, and Verification QA.

The Operator remains orchestration metadata and transport. Authoritative Feature lifecycle truth remains the Feature Manifest advanced only by trusted bounded Feature Events and trusted Persist.

## 2. Normative upstream

This Feature consumes, without silently redefining:

- `docs/v0.3-release-spec.md` and tracking issue #205;
- `ai-sdlc.operator/v1` canonical API;
- the supported MCP read-only adapter;
- `F-OPERATOR-OPERATION-STORE-0001` durable Operation Store, remote CAS, launch/Persist linearization, cancellation and UNKNOWN semantics;
- existing Feature lifecycle role/gate authority rules.

## 3. Product outcome

For a compatible already-installed Feature at the Implementation slice, a trusted Operator SHALL be able to start or resume one durable Operation that progresses, without repeated user `continue` messages, through:

1. Implementation Developer work;
2. independent Code Review;
3. if review requests rework: remediation Developer;
4. fresh independent Code Re-review;
5. Verification QA;
6. a stable stop of `DONE`, `BLOCKED`, `WAITING_EXTERNAL`, `NEEDS_USER`, or `CANCELLED`.

Restarting the Operator process or opening a new chat session SHALL NOT lose progress or require chat-history reconstruction.

## 4. Scope boundaries

This Feature SHALL NOT implement Requirement/Design/Plan/full-lifecycle automation, Decision persistence/UX, Notification Outbox, complete `operator.inbox`, project takeover/install/upgrade, or v0.3 release publication.

`NEEDS_USER` is an honest stable stop marker only in this Feature; durable Decision/Authorization objects are later work.

## 5. Vertical-loop states

The trusted orchestration layer SHALL derive its next action from authoritative Feature Manifest state plus durable Operation history. It SHALL NOT maintain a second Feature lifecycle truth.

At minimum it SHALL support these semantic steps:

- `IMPLEMENTATION_WORK`;
- `CODE_REVIEW`;
- `CODE_REMEDIATION`;
- `CODE_REREVIEW`;
- `VERIFICATION_QA`;
- stable terminal/stop outcomes.

The step projection is recoverable from durable Operation Events and may be rebuilt after projection loss.

## 6. Exact binding and stale-state fencing

Every role dispatch SHALL durably bind:

- target repository;
- Feature id;
- expected Feature revision;
- expected Feature stage/status;
- role and task identity;
- Operation id/generation;
- candidate head SHA when the role evaluates or changes a candidate;
- semantic-effect/external-dispatch identity from the Operation Store.

Immediately before launch authorization and immediately before Feature Persist linearization, trusted code SHALL re-read authoritative Feature state and reject stale revision/stage/candidate bindings.

A Feature branch, Worker payload, model result, or chat message SHALL NOT self-assert trusted revision, policy, candidate, or credential authority.

## 7. Worker Result contracts

Each role SHALL return a typed bounded Worker Result containing evidence and role-specific outcome data only.

Worker Results SHALL NOT contain arbitrary executable Feature Events, arbitrary Manifest mutations, shell commands for trusted execution, arbitrary gate changes, or policy expansion.

Unknown result fields that could alter lifecycle semantics SHALL be rejected rather than ignored.

At minimum, result contracts SHALL exist for:

- Developer implementation/remediation result;
- Reviewer PASS/REWORK result;
- QA PASS/REWORK/BLOCKED result.

## 8. Trusted role-specific translators

Only trusted default-branch/control-plane translators MAY convert a validated Worker Result into bounded Feature Event changes.

Translator allow-lists SHALL be explicit per role/outcome.

### Developer translator

May record implementation/remediation artifacts/evidence/task completion and request the bounded next lifecycle stage allowed by the approved lifecycle. It SHALL NOT PASS code-gate or verification-gate.

### Reviewer translator

May record review evidence and only the bounded Code Review outcomes legal for the current stage: PASS to the code-gate/Verification READY path, or REWORK with a remediation task. It SHALL NOT author implementation evidence on behalf of Developer or PASS verification-gate.

### QA translator

May record verification evidence and only bounded Verification outcomes legal for the current stage. It SHALL NOT retroactively approve code review or synthesize Product Acceptance.

All translated Events SHALL still pass the normal trusted Feature Event/Persist validators.

## 9. Role independence

Reviewer and QA identities SHALL be durable trusted identities, not free-form Worker claims.

A Reviewer whose identity conflicts with the candidate author/remediation identity where independence is required SHALL NOT satisfy Code Review PASS.

After a REWORK/remediation cycle, re-review SHALL require a fresh reviewer dispatch identity distinct from the remediation Developer and satisfying repository role-independence policy.

QA SHALL use a QA role identity and SHALL NOT be satisfied by the Developer or current reviewer identity when policy requires separation.

Identity/policy ambiguity SHALL fail closed to `BLOCKED` or `NEEDS_USER`; it SHALL NOT silently waive independence.

## 10. Dispatch and callback behavior

All role dispatches SHALL use the existing Operation Store semantic reservation, stable external dispatch key, generation-specific claim and `dispatch.launch.authorized` linearization.

Duplicate role-dispatch requests SHALL converge on the same semantic effect rather than create another Worker launch.

External launch lookup outcomes SHALL preserve the existing `NOT_LAUNCHED` / `LAUNCHED` / `UNKNOWN` semantics. `UNKNOWN` SHALL block speculative relaunch and survive generation takeover.

Equivalent Worker callback replay SHALL be idempotent. A callback with conflicting operation/generation/role/task/candidate/external-dispatch binding SHALL fail closed and remain auditable.

## 11. Persist linearization

A validated Worker Result is evidence, not Feature authority.

Before Feature mutation, the trusted translator SHALL produce one bounded Event candidate. The Operation SHALL durably record exact Persist request identity, then re-read Feature/Operation state before `persist.linearized`.

Cancellation/supersession/staleness durable before Persist linearization SHALL fence the Feature write. Persist linearization durable first MAY allow only that exact already-authorized Event to complete and later be confirmed.

Lost local acknowledgement after Feature Persist SHALL be recoverable by correlating the exact Event/Feature revision and SHALL NOT cause duplicate lifecycle advancement.

## 12. Automated review/remediation loop

### Happy path

A successful Developer result progresses to independent Code Review. Reviewer PASS progresses to Verification QA. QA PASS reaches this Feature's stable `DONE` boundary for the vertical loop.

### Rework path

Reviewer REWORK SHALL create/activate only the bounded remediation task allowed by lifecycle policy. A remediation Developer result SHALL return the Feature to fresh Code Re-review. A subsequent Reviewer PASS SHALL progress to QA.

Repeated REWORK MAY continue only while policy permits and safety invariants remain satisfied; otherwise the Operation SHALL stop `BLOCKED` or `NEEDS_USER` rather than loop indefinitely.

## 13. `operation.resume`

This Feature SHALL provide canonical `operation.resume` backing only for Operations owned by this approved vertical-loop profile.

Resume SHALL:

- load durable Operation Store state;
- re-read authoritative Feature Manifest/ref/candidate state;
- reconcile already-linearized launch/Persist facts;
- take over generation only through the approved durable takeover primitive;
- choose the next safe vertical-loop action or stable stop.

Resume SHALL NOT fabricate missing evidence, clear unresolved `UNKNOWN`, bypass pending human authorization, or provide generic full-lifecycle recovery for unsupported profiles.

Unsupported Operations/profiles SHALL return honest structured `CAPABILITY_UNAVAILABLE` or a bounded invalid-state error.

## 14. Stable stops

- `DONE`: vertical loop reached the approved post-QA success boundary for this workstream.
- `WAITING_EXTERNAL`: an authorized external action is outstanding and does not require human approval.
- `BLOCKED`: safety/policy/state inconsistency prevents autonomous progress.
- `NEEDS_USER`: progress requires a human decision/authorization that this Feature cannot durably represent yet.
- `CANCELLED`: Operation cancellation is durable; exact previously linearized side effects may only reconcile under Operation Store rules.

The Operator SHALL NOT busy-loop at a stable stop.

## 15. Recovery and restart

A fresh process/session SHALL be able to reconstruct the current vertical-loop step from the Operation Store and authoritative Feature state without previous chat context.

Recovery SHALL detect and reconcile at least:

- Worker launch authorized but local dispatch acknowledgement missing;
- Worker callback durable but local handling acknowledgement missing;
- Feature Persist linearized/committed but local confirmation missing;
- CAS conflict during Operation state write;
- superseded generation;
- stale Feature revision/stage/candidate;
- unresolved UNKNOWN launch state.

This is bounded recovery for this vertical loop, not the complete v0.3 Decision/Notification recovery product.

## 16. Capability honesty

After this Feature is accepted:

- `operation.start`, `operation.status`, `operation.cancel` remain backed by the Operation Store;
- `operation.resume` MAY be advertised available only for the supported vertical-loop profile and only through a transport/runtime that has the required trusted backing;
- `operator.inbox` remains unavailable as a complete canonical capability while Decision/Notification backing is absent;
- Decision/Notification write capabilities remain unavailable;
- the MCP read-only adapter SHALL NOT silently gain write tools merely because canonical backing exists.

## 17. Deterministic verification requirements

Implementation SHALL include deterministic tests for at least:

1. Developer → Reviewer PASS → QA PASS happy path;
2. Reviewer REWORK → remediation Developer → fresh Reviewer PASS → QA PASS;
3. Reviewer independence rejection;
4. QA role/identity rejection;
5. Worker Result carrying arbitrary/proposed executable Feature Event mutation rejected;
6. stale revision/stage/candidate before launch;
7. stale revision/stage/candidate before Persist;
8. duplicate Worker callback replay;
9. lost callback acknowledgement recovery;
10. `NOT_LAUNCHED` / `LAUNCHED` / `UNKNOWN` launch reconciliation;
11. UNKNOWN inheritance across generation takeover;
12. cancellation before/after launch authorization;
13. cancellation before/after Persist linearization;
14. Operation Store CAS conflict plus semantic re-plan;
15. restart/new-session reconstruction without chat history;
16. lost Feature Persist acknowledgement reconciliation without duplicate advancement;
17. unsupported profile resume fails honestly;
18. `operator.inbox`, Decision and Notification capabilities remain honestly unavailable;
19. existing protocol/lifecycle/cross-repository/security/public-runtime regressions.

## 18. Acceptance criteria

The Feature is acceptable only if an independent QA can deterministically demonstrate both happy and rework vertical paths, restart recovery, exact-binding fences, role independence, duplicate/lost-ack safety and bounded translator authority, with no direct authoritative Manifest mutation outside trusted Event/Persist.

Feature acceptance proves the vertical loop only. It does not make v0.3 release-ready.
