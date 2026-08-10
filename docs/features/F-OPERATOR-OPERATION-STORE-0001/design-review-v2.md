# Design Re-review — F-OPERATOR-OPERATION-STORE-0001

## Role

Fresh independent Design Reviewer.

## Context

- authoritative state re-read before verdict: revision `13`, `design-review: WORKING`, `design-gate: PENDING`;
- approved Requirement: `requirement-v2`;
- prior review: `evidence-design-review-v1` (REWORK, 2 MAJOR);
- remediation: `evidence-design-remediation-v1`;
- reviewed Design: `design-v2`.

## Verdict

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0

## Prior MAJOR-1 — protected state-ref enforcement — CLOSED

Design v2 no longer assumes that a named state ref is protected. It defines a trusted `StateRefProtectionVerifier`, bounded PROTECTED / UNPROTECTED / UNKNOWN outcomes, and repository/ref-bound protection receipts.

Production semantic Store writes are allowed only with a positively verified PROTECTED receipt. Missing, UNPROTECTED, UNKNOWN, mismatched or invalid receipts fail closed before semantic commit.

The first-ref bootstrap sequence is also bounded: if a platform requires the ref to exist before protection is configured, only an initialization-only non-semantic commit may exist before protection; Operation/reservation/claim/launch/Persist semantic writes remain disabled until protection is positively verified.

Feature/Worker/client input cannot select the state ref or self-attest protection. Provisioning remains a trusted installation/control responsibility rather than role-worker authority.

This satisfies the approved Requirement's protected control-plane state-ref boundary.

## Prior MAJOR-2 — immutable reservations/claims — CLOSED

Design v2 removes replaceable logical ledgers for durable reservations and consumed claims.

It now requires:

- create-once immutable semantic reservation files permanently binding the semantic key and one stable `external_dispatch_key`;
- create-once immutable generation-specific dispatch claim files;
- create-once immutable Feature claim artifacts;
- all evolving launch, receipt, callback, generation, cancellation and Persist facts represented by immutable Operation Events;
- only projection cache may be replaced.

The trusted mutation planner explicitly distinguishes `create_immutable` from `replace_projection` and rejects update/delete/replace operations on immutable Event/reservation/claim paths.

This matches the frozen distinction between immutable journal/reservation/claim state and replaceable projection cache.

## Full design checks

### Git CAS — PASS

The exact snapshot → semantic plan → descendant commit → exact lease/ref CAS path remains intact. A CAS loser re-reads durable state and re-evaluates semantics instead of replaying stale bytes.

### Operation projection — PASS

Projection is reconstructable solely from immutable durable artifacts/events and is explicitly non-authoritative cache.

### Active Operation ownership — PASS

Equivalent concurrent starts converge; incompatible nonterminal ownership is rejected; Feature ownership derives from immutable claims plus journal state.

### Generation vs semantic effect — PASS

Operation generation remains distinct from the generation-independent semantic-effect key. Takeover creates new immutable generation ownership records while preserving the same immutable reservation and external dispatch key.

### Launch linearization — PASS

`dispatch.launch.authorized` remains an immutable journal event and the exact launch linearization point. Cancellation/supersession ordering remains fail-closed.

### Receipt/callback recovery — PASS

NOT_LAUNCHED / LAUNCHED / UNKNOWN observations become immutable journal facts. UNKNOWN still blocks speculative relaunch and survives takeover with the same reservation/external key.

### Persist linearization — PASS

`persist.requested`, `persist.linearized`, and `persist.confirmed` are immutable Operation Events. Store does not edit Feature Manifests and lost acknowledgements require exact Event/receipt correlation.

### Trusted bindings — PASS

Protection and Feature/candidate verification receipts are trusted runtime objects outside canonical/Worker schemas and are binding-checked by Store commands.

### Canonical API scope — PASS

Only `operation.start`, `operation.status`, and `operation.cancel` gain backing. `operator.inbox`, `operation.resume`, Decision and Notification capabilities remain honestly unavailable. The prior Requirement Review correction remains preserved.

### MCP and authority boundary — PASS

No MCP write tools are introduced. Operation Store remains orchestration metadata, never Feature lifecycle/Gate authority.

### Deterministic validation — PASS

The validation plan covers protected/unprotected/unknown state-ref behavior, immutable-artifact enforcement, CAS conflicts, concurrent starts, generation takeover, linearization ordering, UNKNOWN behavior, callback replay, Persist lost-ack recovery, canonical availability and repository regressions without relying on timing sleeps.

## Gate recommendation

`design-gate`: **PASS** with `design-v2` as the approved Design.

Next legal stage: Plan / Orchestrator.
