# Design Review — F-OPERATOR-OPERATION-STORE-0001

## Role

Independent Design Reviewer.

## Context

- reviewed approved Requirement: `requirement-v2`;
- reviewed Design: `design-v1`;
- authoritative review-start stage: `design-review`;
- normative upstream: frozen v0.3 Release Spec, especially Operation Store immutability, protected state-ref, CAS, generation, launch and Persist linearization rules.

## Verdict

**REWORK**

- BLOCKER: 0
- MAJOR: 2
- MINOR: 0

## MAJOR-1 — Protected state-ref requirement is named but not operationally enforced or verified

### Observation

The Design fixes a trusted state-ref name and correctly prevents canonical/MCP/Feature/Worker input from selecting it. It also defines exact Git CAS behavior.

However, the frozen Requirement says the Operator state lives on a **protected** control-plane state ref. The Design currently treats that as an assumed deployment property and does not define a trusted provisioning/preflight/verification boundary that proves the remote ref is actually protected before production writes are accepted.

A fixed ref name plus worker credential isolation is not equivalent to server-side ref protection. A repository actor with ordinary contents write authority could otherwise mutate or reset the state ref outside the Operator Store path, defeating append-only/CAS assumptions.

### Required remediation

The Design must define a fail-closed trusted state-ref protection boundary, including:

- how trusted installation/control configuration selects the state ref;
- how production runtime establishes that the target ref is protected by repository policy/rules or an equivalent trusted control-plane enforcement mechanism;
- what happens when protection cannot be verified (production writes must fail closed, not silently continue);
- how this precondition is represented in the trusted Git adapter/writer path so Feature/Worker/client input cannot self-attest it;
- deterministic tests for protected/unprotected/unknown verification outcomes;
- migration/bootstrap behavior for first state-ref creation without opening a bypass window.

The implementation does not have to give role workers repository-admin authority; protection verification/provisioning belongs to trusted installation/control components.

## MAJOR-2 — Replacing reservation/claim ledger files conflicts with the frozen immutable reservation/claim boundary

### Observation

The Design proposes fixed-path reservation/claim JSON files containing an append-only `records` array, with the containing file replaced on each logical append.

The frozen Release Spec explicitly distinguishes replaceable cached projections from immutable Operation Events and consumed reservations/claims. Replacing the reservation/claim object itself with a larger file means the durable reservation/claim artifact at that path is mutable, even if prior array entries are preserved.

This is materially different from the allowed replaceable projection cache and weakens the invariant that a semantic reservation or consumed claim cannot be rewritten after creation.

### Required remediation

The Design must make reservation and consumed claim artifacts themselves immutable after creation.

A safe bounded direction is:

- `reservations/external/<semantic-effect-key>.json` is created once and permanently binds the semantic key inputs + stable external dispatch key;
- `claims/dispatch/<dispatch-claim-id>.json` is created once per claim and never modified;
- feature claim artifact is created once for the Feature/Operation binding or otherwise redesigned so historical claims remain immutable rather than overwritten;
- subsequent observations/state transitions such as launch authorization, receipt lookup, callback correlation, generation takeover, claim release/terminal status, and Persist correlation live in immutable Operation journal events (or other newly specified immutable event records), with current state derived by the reducer;
- no history-changing replacement of reservation/claim bytes is allowed through the trusted mutation planner.

If the Architect chooses another structure, it must still satisfy the literal frozen invariant that consumed reservations/claims remain immutable and projections alone are replaceable cache.

## Other review checks

### CAS model — PASS

The Design's exact-ref snapshot → bounded plan → commit → lease-protected ref update → semantic re-read/re-evaluation on conflict is aligned with the Requirement.

### Domain/Git separation — PASS

Pure semantic planner/reducer separated from trusted Git credentials is appropriate and testable.

### Operation Event journal — PASS

Individual immutable event files with contiguous sequence and deterministic rebuild are sound.

### Generation vs semantic-effect identity — PASS

The Design correctly excludes Operation generation from semantic-effect-key derivation and preserves the external dispatch key across takeover.

### Launch linearization — PASS

`dispatch.launch.authorized` is correctly treated as the durable launch linearization point with cancellation/supersession ordering preserved.

### Receipt UNKNOWN semantics — PASS

UNKNOWN remains fail-closed against speculative relaunch and is inherited across takeover.

### Persist linearization — PASS

Launch authorization and Feature Persist authorization remain independent and the Store does not directly mutate the Feature Manifest.

### Canonical API boundary — PASS

Only `operation.start`, `operation.status`, and `operation.cancel` gain backing; `operator.inbox` and later capabilities remain unavailable.

### Requirement Review MAJOR closure — PASS

The Design preserves the corrected all-or-nothing `operator.inbox` boundary and provides only an internal unfinished-Operation query.

### Deterministic tests — PASS

The memory CAS/fault-injection approach avoids race-by-sleep testing and covers the required ordering cases.

## Gate recommendation

`design-gate`: **REWORK / remain PENDING**.

Architect must remediate MAJOR-1 and MAJOR-2, then a fresh independent Design Re-review is required.
