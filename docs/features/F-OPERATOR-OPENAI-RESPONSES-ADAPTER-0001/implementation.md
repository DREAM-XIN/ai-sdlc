# Implementation — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## Status and authority

Developer implementation is **complete** against the approved Requirement, Design v2 and Plan, and is ready for independent Code Review.

The implementation-completion lifecycle transition records `implementation: DONE` and `code-review: READY` only. It does **not** PASS `code-gate`, `verification-gate` or `release-gate`; it does not claim Supported status, real OpenAI service dogfood, Issue #221 completion, or v0.3 release readiness.

The authoritative Feature Manifest remains the lifecycle source of truth.

## Exact-candidate evidence convention

Do **not** hard-code the current PR head, merge-ref SHA, workflow run id, or artifact id in this tracked implementation document. Updating this file creates a new Git commit, so embedding the then-current head here would make the statement stale immediately.

Exact-candidate evidence is bound mechanically instead:

1. `.github/workflows/operator-openai-responses.yml` includes this implementation document in both push and pull-request path filters, so changing this completion surface forces the full Responses validation suite to re-run on the resulting candidate.
2. The readiness artifact records the exact PR head and exact merge-ref checkout used by the workflow.
3. WU6 Persist classification, WU8, Lane B and WU9 repository-wide validation booleans are derived from their **actual validation step outcomes**, not merely prerequisite probes.
4. The readiness artifact records whether the PR head itself contains current trusted `main`; merge-ref freshness alone cannot satisfy completion candidacy.
5. PR metadata may point an independent reviewer to the latest exact-head run and readiness artifact without mutating the candidate commit.

## Trusted production baseline

The trusted default branch contains the reviewed production prerequisites consumed by this Feature:

- generation-bound Durable Vertical Feature Persist;
- deterministic/transient Persist reconcile classification;
- final shared Adapter + Vertical production composition;
- full Vertical write-ready production factory;
- accepted stale-recorded-callback durable convergence, including crash repair from durable `worker.result.rejected` to the mapped stable stop.

The Responses Feature does not copy or create those authorities locally. Dependency-provenance validation fails closed if the Feature branch attempts to manufacture protected upstream runtime authority.

## WU1 — strict Responses protocol surface

Implemented and validated:

- adapter id `ai-sdlc.openai.responses`, protocol version `1`;
- exactly ten fixed `type:function` tools;
- exact tool-to-canonical-capability mapping;
- strict closed schemas and bounded protocol errors;
- no generic canonical router, raw Feature Event/Persist authority, `project.inspect`, or model-facing `operation.resume`;
- forged repository/ref/Store/principal/policy/adapter/trusted-context fields are rejected at the model-facing boundary.

## WU2 — collector, streaming and call correlation

Implemented and validated:

- `parallel_tool_calls=false`;
- zero-or-one executable function call admitted before dispatch;
- incomplete/interrupted streams perform zero semantic work;
- legal `call_id` is the semantic call correlation identity;
- provider response/item ids never substitute for durable Operator authority;
- terminal and streaming collection normalize through the same parser and correlation rules.

## WU3 — durable Responses call journal

Implemented and validated:

- immutable server-owned trusted binding before semantic dispatch;
- deterministic call key and canonical idempotency key;
- crash after canonical write but before Responses result receipt;
- fresh-process replay adopts the already-converged semantic write;
- result receipt repair is durable;
- later replay is journal-only/read-only with zero second backend dispatch;
- candidate and durable result receipts are fail-closed on malformed shape, orphan binding, wrong `call_id`, non-canonical output or digest mismatch.

## WU4 — fail-closed production binding

The production Responses constructor consumes only the authoritative final full-Vertical production bundle. It rejects semantic-only/test fallback, non-production Store authority, split Store runtimes, raw start backends, expanded model writes, obsolete recovery executors, wrong Persist gateway types and mismatched callback coordinator/executor binding.

A fresh canonical `SystemCapabilitiesBackend` is derived over the already-filtered Responses backend map. Broader upstream capabilities such as `project.inspect`, server-only `operation.resume`, or an upstream standalone `system.capabilities` object cannot expand the model-facing surface.

The stale-callback dependency probe requires both fresh Feature/candidate validation inside the deterministic rejection boundary and fresh-process mapped stable-stop repair from an already-durable rejection. Historical catch-only/skip-only shapes remain rejected.

## WU5 — official Responses host boundary

Implemented and validated with SDK-shaped deterministic fixtures for synchronous create, streaming, background create/retrieve, interrupted-stream retrieval recovery and durable replay.

Function calls execute only after an explicit provider `completed` boundary. Pending, failed, cancelled, incomplete, missing-status, interrupted-stream, cross-response-id, and item-done-without-response-completed cases remain zero semantic dispatch.

Deterministic CI does not require a billable OpenAI request. Real service dogfood remains separate release evidence.

## WU6 — independent Lane A and Persist classification

The materially independent Lane-A conformance driver and the strict Responses-boundary adversarial matrix execute successfully. Lane A remains explicitly insufficient for Supported status.

Coverage includes malformed/version/identity/replay semantics plus launch/cancel ordering, UNKNOWN lookup, lost ACK same-key recovery, takeover identity, stale-candidate fencing, Effect Lineage successor blocking, Decision adversaries, Notification duplicate ACK, Persist recovery/classification, and secret redaction.

Readiness records Persist classification PASS only from the actual strict WU6 execution step.

## WU7 — mandatory Lane B

The exact Feature Event seam and scope fence are implemented and validated against the final shared production composition.

Mandatory Lane B executes only after:

1. the hard production dependencies are present on trusted `main`;
2. the PR head contains that exact trusted baseline; and
3. mandatory WU8 has actually executed successfully.

The authoritative production workflow enforces that ordering with explicit fail-closed guards. The completion candidate executed WU8 successfully before Lane B, then executed Lane-B production composition successfully.

## WU8 — stale-recorded-callback production prerequisite

Accepted stale-callback convergence is now part of trusted `main`, and mandatory WU8 executes successfully against that inherited runtime rather than copying prerequisite authority into this Feature.

WU8 covers:

- normal stale-candidate durable rejection and convergence;
- the crash window where `worker.result.rejected` is durable but the mapped stable stop is absent;
- `STALE_REVISION` / `BLOCKED` / `POLICY_DENIED` mapping to durable `BLOCKED`;
- `NEEDS_USER` mapping to durable `NEEDS_USER`;
- fresh-process no-reprocess repair and second-recovery zero mutation;
- transient Feature-read failures remaining retryable and not durably reclassified;
- zero stale `feature.event.translated`, zero Persist authority and zero fresh external effect;
- lineage-required candidate-A stale rejection with candidate-B successor remaining `UNRESOLVED_PREDECESSOR`, receiving zero second reservation, launch authorization or external launch;
- the frozen Effect-Lineage adversarial contract.

The earlier prerequisite-integration preview remains non-authoritative historical compatibility evidence only and is not used for implementation completion.

## WU9 — documentation, Public Runtime and authoritative repository validation

Completion requires all of the following on the synchronized production candidate:

1. the final Vertical production root is included in Public Runtime;
2. Public Runtime validation executes successfully;
3. the full authoritative repository suite `python scripts/validate.py` executes successfully inside the full Responses workflow;
4. the Supported-facing runtime document preserves the exact adapter id, protocol version `1`, frozen ten-tool surface, server-owned provider/Operator configuration boundary, deterministic provider-emulator CI policy, and separation of real OpenAI service dogfood from Feature/release evidence.

These conditions are included in readiness rather than inferred from documentation alone.

## Readiness v2 anti-overclaim contract

`ai-sdlc.openai-responses-implementation-readiness/v2` records WU1 through WU9, current-main synchronization, hard production dependency state, actual WU6/WU8/Lane-B step outcomes, Public Runtime validation and repository-wide validation.

`mechanical_completion_candidate=true` requires every Feature-owned mechanical signal, exact current-main synchronization, strict WU6 classification, strict WU8, strict Lane B, Public Runtime closure, repository-wide validation and all hard dependencies.

Even when mechanical completion is true, readiness is forbidden from claiming:

- `implementation_done_claimed`;
- `supported_status_claimed`;
- `code_gate_pass_claimed`;
- `release_ready_claimed`;
- Lane A as Supported evidence.

Lifecycle authority remains separate.

## Implementation completion

The approved implementation plan's completion prerequisites have been satisfied:

- accepted stale-callback convergence is on trusted `main`;
- the Feature candidate is synchronized to that trusted baseline;
- mandatory WU8 executes and PASSes;
- mandatory Lane B executes only after WU8 and PASSes;
- Public Runtime validation PASSes;
- authoritative repository-wide validation PASSes;
- readiness reports every WU1–WU9 mechanical signal true and `mechanical_completion_candidate=true`;
- readiness keeps all Code Gate / Supported / release claims false.

The Developer implementation-verification evidence is recorded separately under `docs/features/F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001/evidence/implementation-verification.md`.

The legal `IMPL-DONE` Feature Event registers this implementation artifact and Developer verification evidence, advances only `implementation: DONE` and `code-review: READY`, and leaves every later Gate pending.

## Next authority

The next authority is an **independent Code Reviewer** on the resulting exact PR head.

This Developer completion does not self-review, merge PR #233, provision a live Operator Store, complete Issue #221, perform real-service dogfood, change VERSION, create final `release/v0.3.0.yaml`, or claim v0.3 release readiness.
