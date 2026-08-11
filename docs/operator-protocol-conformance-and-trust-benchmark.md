# Operator Protocol Conformance vs Autonomous SDLC Trust Benchmark

Issue: #223

## Decision

AI-SDLC maintains two intentionally different validation suites.

### Suite A — AI-SDLC Operator Protocol Conformance

Suite A answers one narrow question: **does an adapter/runtime implementation conform to the AI-SDLC Operator protocol contract?**

It is normative for AI-SDLC compatibility and may use AI-SDLC-specific vocabulary and invariants. The executable transport-neutral harness is `scripts/operator_conformance.py`.

The frozen common adapter subset is:

- `system.capabilities`
- `feature.status`
- `operator.inbox`
- `operation.status`
- `decision.list`
- `notification.list`

Suite A checks protocol semantics such as version negotiation, structured errors, identity propagation, unavailable-capability behavior, trusted-context injection rejection, and materially independent adapter evidence.

Test-only fixtures such as `fixture.direct` and `fixture.json-roundtrip` are useful for harness verification but **never count as supported release adapters**. A thin wrapper/alias around another adapter also cannot establish material independence.

Supported adapters must exercise their real production protocol boundary. For example, the accepted MCP adapter runs the shared suite through the actual MCP stdio translation path; a future OpenAI Responses adapter must run the same common assertions through its own function-tool parser/output path rather than calling canonical dispatch through a conformance-only shortcut.

### Suite B — Autonomous SDLC Trust Benchmark

Suite B answers a different question: **how safely and recoverably does a product behave under observable SDLC failure and authority scenarios?**

It is product-neutral and is not a compatibility test. A competing system must not be penalized because it does not implement AI-SDLC object names, storage layout, or lifecycle vocabulary.

The benchmark therefore describes black-box conditions such as:

- a reviewed candidate changing before verdict application;
- duplicate external completion callbacks;
- external execution succeeding while acknowledgement is lost;
- orchestration crash followed by resume/takeover;
- cancellation racing with an in-flight external action;
- author and reviewer sharing the same execution identity;
- prior approval replayed against a different commit;
- uncertain external execution state during retry.

Evaluation is based on observable outcomes and metrics such as duplicate effects, unauthorized transitions, stale-evidence acceptance, self-review acceptance, speculative retries under uncertainty, recovery success/time, human intervention, false-BLOCKED rate, happy-path latency, and persistent write/API/storage overhead.

Results must distinguish at least `unsupported`, `unsafe`, `requires-human-intervention`, and `safe-recovered`.

## Release relationship

Suite A is directly relevant to v0.3 adapter compatibility and can be used as release evidence when a supported adapter runs the production protocol path.

Suite B is initially an experimental/comparative track. It does **not** become a v0.3 release blocker merely because the benchmark exists. Individual v0.3 release-level fault-injection requirements, such as Issue #221, remain governed by the frozen Release Spec and their own evidence requirements.

## Machine-readable contract

`benchmarks/operator-trust-suites-v0.1.yaml` is the machine-readable boundary between the two suites. `scripts/validate_operator_trust_suites.py` validates that:

- Suite A remains aligned with the canonical frozen conformance subset and release contract;
- test fixtures are not promoted to supported adapters;
- Suite B contains the approved black-box scenarios and metric vocabulary;
- Suite B does not require AI-SDLC internal protocol objects in its observable scenario definitions;
- the benchmark remains non-normative for compatibility and non-blocking for v0.3 unless separately approved.
