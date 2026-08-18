# Acceptance — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## Role

Human/Product Acceptance authority for the v0.3 OpenAI Responses adapter Feature.

## Authoritative starting state

- Feature Manifest revision: `19`;
- current stage: `acceptance`;
- `acceptance: WORKING`;
- requirement-gate: PASS;
- design-gate: PASS;
- code-gate: PASS;
- verification-gate: PASS;
- `release-gate: PENDING` before this decision.

## Human/Product decision

**ACCEPT / PASS**

The user explicitly issued the Human/Product decision `ACCEPT #233` on 2026-08-18. This decision is not inferred from CI, Reviewer/QA output, automation, or prior `continue` instructions.

## Accepted product outcome

The Feature is accepted as the second materially independent supported AI-client protocol boundary for the bounded v0.3 release slice, using OpenAI Responses function tools over the existing canonical `ai-sdlc.operator/v1` authority model.

The accepted model-facing surface remains exactly the reviewed bounded set:

- reads: `system.capabilities`, `feature.status`, `operator.inbox`, `operation.status`, `decision.list`, `notification.list`;
- writes: `operation.start`, `operation.cancel`, `decision.respond`, `notification.ack`.

No client `operation.resume`, raw Feature Event/Persist/Store authority, generic capability selector, arbitrary repository/shell write, policy selector, Worker/Gate authority, or Human/Product authority is accepted or exposed.

## Accepted trust and lifecycle behavior

The accepted implementation preserves server-owned repository/ref/provider/principal/policy/runtime bindings, strict tool schemas, durable Responses call identity, canonical idempotency, whole-response completion fencing, shared protected Store authority, current production Vertical composition, classified recovery, stale-callback convergence, one-shot external-create authority, exact Feature Event/Persist semantics, candidate/revision/generation/cancel/Effect-Lineage fencing, and Reviewer/QA independence.

Model text or provider output cannot synthesize Human/Product Acceptance. `decision.respond` remains bounded by trusted responder context and the exact authorized Decision choice.

## Verification and Review basis

The accepted executable candidate is PR #233 exact head:

`001921faf1df923333844489e454001be1644734`

That head completed 14/14 fresh PR-triggered workflows successfully, including the main OpenAI Responses integration lane, WU6 Persist classification, WU8 stale-callback validation, mandatory Lane-B production composition, exact Feature Event seam, result receipt, explicit status, dependency provenance, Public Runtime Distribution, protocol validation and Required PR Gate.

The readiness artifact for that head is:

- artifact id: `9274900148`;
- digest: `sha256:27f2fc90f07369654a3d0a3d735dd43f33c57dfd53f317b829ab75450f647e8f`.

Fresh Independent Code / Production-Integration Re-review on the same exact head recorded:

**PASS — 0 BLOCKER / 0 MAJOR / 0 MINOR**.

## Current-main drift handling

After the reviewed Feature head was established, trusted main advanced from `a0efd4653bdb1d6a8469243cc3b40b890f30684b` to `1aee7593a10e7ea290b02c25af96bf0f701cc11e` through the #294 live Vertical-policy protection remediation.

That main-only delta changes six protected-policy/protection-verifier paths and does not overlap the 76 Feature-owned PR #233 paths. Therefore this Human/Product decision remains bound to the reviewed Feature semantic tree. Before merge, PR #233 must still be cleanly reconstructed onto the then-current trusted main and its semantic Feature tree re-confirmed; baseline reconstruction alone must not be treated as a reason to repeat Product Acceptance.

## Explicit release boundary

This Feature-level Acceptance does **not** claim:

- Issue #263 live protected Vertical-policy materialization PASS;
- Issue #221 real-runtime effect-safety fault-injection PASS;
- completion of the three real v0.3 dogfood scenarios;
- final release evidence-ledger synchronization;
- `VERSION=0.3.0`;
- creation or approval of final `release/v0.3.0.yaml`;
- overall v0.3 release readiness.

At the time of this Acceptance, the new trusted-main #263 materialization run `32086293996` failed before materialization, so #263 remains a separate release blocker.

## Product decision

Feature-level `release-gate`: **PASS**.

`F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001` may complete its standard Feature lifecycle through the trusted Feature Event/Persist path. This decision accepts only the reviewed bounded OpenAI Responses adapter Feature and must not be interpreted as final v0.3 release authorization.
