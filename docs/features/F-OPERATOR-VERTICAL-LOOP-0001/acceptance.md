# Acceptance — F-OPERATOR-VERTICAL-LOOP-0001

## Role

Independent Product / Acceptance owner.

## Authoritative starting state

- revision: `27`;
- current stage: `acceptance`;
- `acceptance: WORKING`;
- requirement-gate: PASS;
- design-gate: PASS;
- code-gate: PASS;
- verification-gate: PASS;
- `release-gate: PENDING`.

## Verdict

**PASS**

The Feature satisfies the approved Requirement and Issue #216 within its explicitly bounded vertical-loop scope.

## Accepted product outcomes

### Durable unattended vertical loop

For an already-installed compatible Feature, the trusted Operator now has a bounded durable orchestration loop across:

`Implementation → independent Code Review → Remediation → fresh Re-review → Verification QA → stable stop`.

The implemented control plane can progress through those stages from authoritative Feature state plus durable Operation history without depending on chat-history reconstruction.

### Feature truth remains authoritative

The Operator does not become a second lifecycle authority. Feature Manifest state still advances through bounded trusted Feature Events and trusted Persist. Worker Results remain evidence/outcome payloads rather than arbitrary executable Feature Events or Manifest/gate mutation authority.

### Bounded role-specific translation

Developer, Reviewer and QA result contracts are closed and role-specific. Trusted translators permit only lifecycle effects appropriate to the current role/stage:

- Developer cannot PASS code-gate or verification-gate;
- Reviewer can produce only bounded Code Review PASS/REWORK effects;
- QA can produce only bounded Verification effects and cannot PASS `release-gate` or synthesize Product Acceptance.

### Independent Reviewer / QA identities

Reviewer and QA separation is durably reconstructed from accepted callback history. Repeated REWORK preserves the complete candidate-contributor and Reviewer lineage so an earlier remediation Developer cannot later satisfy Reviewer/QA independence merely because another remediation round occurred.

### Exact candidate and stale-state fencing

Role dispatch, callback adoption and Persist bind repository, Feature id, expected revision/stage, role/task identity, Operation generation/profile and candidate head where applicable. Stale revision/stage/candidate state fails closed before launch/Persist authority is granted.

### Trusted callback and collected-output boundary

The old parallel executor callback ingress is fail-closed. Production callback-to-lifecycle processing is behind the trusted coordinator path with durable semantic reservation/launch binding, trusted role-policy reconstruction and mandatory collected-output content/digest verification.

### Durable launch/callback/Persist recovery

The accepted implementation covers:

- NOT_LAUNCHED / LAUNCHED / UNKNOWN launch reconciliation;
- unresolved UNKNOWN inheritance across generation takeover;
- duplicate/conflicting callback safety;
- lost callback acknowledgement recovery;
- cancellation around launch authorization;
- Persist requested/linearized/confirmed ordering;
- lost Persist acknowledgement exact reconciliation without duplicate lifecycle advancement;
- cancellation before/after Persist linearization;
- CAS conflict with semantic re-plan.

### Restart / resume boundary

A new process/session can reconstruct the vertical-loop step from durable Operation state plus authoritative Feature truth without previous chat context. `operation.resume` is bounded to the approved vertical-loop profile and unsupported/unprofiled Operations fail honestly rather than receiving invented recovery semantics.

### Stable-stop and capability honesty

The vertical loop preserves honest stable outcomes (`DONE`, `BLOCKED`, `WAITING_EXTERNAL`, `NEEDS_USER`, `CANCELLED`) and does not busy-loop through unresolved safety conditions.

Incomplete `operator.inbox`, Decision and Notification capabilities remain unavailable; the read-only MCP adapter does not silently gain write authority.

## Verification basis

Independent Verification initially found QA-MAJOR-1 because two material deterministic validators existed but were not executed by authoritative `scripts/validate.py`.

The bounded remediation candidate:

`68b956468622c15f5d6fe94a8106f093b3eeffe9`

changed only `scripts/validate.py` to execute the existing completion-path and fault/replay validators while preserving all other validators.

Exact remediation-candidate CI:

- Validate AI-SDLC protocol — run `31369523086` — **SUCCESS**;
- Validate Public Runtime Distribution — run `31369523121` — **SUCCESS**;
- Required PR Gate — run `31369523089` — **SUCCESS**.

The Protocol log explicitly records successful execution of the vertical loop, completion-path, recovery, deterministic fault/replay, Code Review remediation and gh-aw validators, followed by `AI-SDLC validation passed`.

Fresh independent QA re-verification is recorded in `verification-v2.md` with verdict:

**PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR**.

## Candidate / lifecycle-head integrity

No runtime source, schema or validator file changed after the validated remediation functional candidate. Later commits only record Verification remediation evidence, QA v2 evidence, lifecycle Events and Manifest projections.

Lifecycle-only PR heads may show GitHub `action_required` runs with zero jobs. Acceptance does not mislabel those as successful CI and does not use them as execution evidence. The accepted executable candidate remains the exact green candidate above, with exact-diff evidence that no executable code changed afterward.

## Explicitly not accepted as part of this Feature

This Feature-level Acceptance does **not** claim completion or approval of:

- Issue #219 Effect Lineage / UNKNOWN Resolution;
- Issue #221 real-runtime fault injection / release-level effect-safety proof;
- general Requirement/Design/Plan/full-lifecycle automation;
- Decision/Authorization persistence or UX;
- Notification Outbox persistence/UX;
- complete `operator.inbox` semantics;
- project takeover/install/upgrade;
- a second materially independent AI adapter/client;
- Naming/Benchmark work;
- full v0.3 dogfood;
- publication/security readiness of v0.3 as a whole;
- overall v0.3 release authorization.

## Product decision

Feature-level `release-gate`: **PASS**.

`F-OPERATOR-VERTICAL-LOOP-0001` may complete its standard Feature lifecycle. This decision proves the durable bounded Implementation→Review→Remediation→Re-review→QA vertical loop only and must not be interpreted as overall v0.3 release readiness.
