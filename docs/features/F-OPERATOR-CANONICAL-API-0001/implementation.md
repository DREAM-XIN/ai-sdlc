# Implementation — v0.3 canonical typed Operator API foundation

Feature: `F-OPERATOR-CANONICAL-API-0001`

Issue: `#208`

Approved Requirement: `requirement-v1`

Approved Design: `design-v1`

Approved Plan: `plan-v1`

## Implemented scope

This implementation adds only the transport-independent `ai-sdlc.operator/v1` contract foundation:

- JSON Schema 2020-12 request/response envelopes, structured errors, identity boundaries, capability discovery, and dedicated request/response payload schemas for all twelve canonical capabilities;
- one trusted code-owned capability registry matching the Plan matrix;
- deterministic dispatcher validation with version, capability, payload, idempotency, expected-revision, trusted-context, backend availability, result-schema, and secret-safe failure handling;
- a bounded backend abstraction where only `system.capabilities` is available by default;
- a reusable deterministic conformance validator with two distinct fixture adapter identities/transports and alias rejection as independent evidence.

## Frozen capability matrix

| Capability | Class | Idempotency | Expected Feature revision | Default backend | Conformance subset |
| --- | --- | --- | --- | --- | --- |
| `system.capabilities` | read | no | no | available | yes |
| `project.inspect` | read | no | no | unavailable | no |
| `feature.status` | read | no | no | unavailable | yes |
| `operator.inbox` | read | no | no | unavailable | yes |
| `operation.start` | write | yes | yes | unavailable | no |
| `operation.status` | read | no | no | unavailable | yes |
| `operation.resume` | write | yes | yes | unavailable | no |
| `operation.cancel` | write | yes | no | unavailable | no |
| `decision.list` | read | no | no | unavailable | yes |
| `decision.respond` | write | yes | no | unavailable | no |
| `notification.list` | read | no | no | unavailable | yes |
| `notification.ack` | write | yes | no | unavailable | no |

## Authority boundary

The new API layer does not directly invoke Feature Persist, Gate mutation, merge, release, shell execution, provider inference, or unrestricted repository mutation. Trusted runtime/service authorization context is supplied separately from client identity and cannot be asserted through the canonical request envelope.

Typed schemas for Operation/Decision/Notification capabilities do not imply durable backing behavior. With the default foundation backend set, every capability other than `system.capabilities` returns `CAPABILITY_UNAVAILABLE`.

## Deterministic validation

The Feature-specific umbrella command is:

```bash
python scripts/validate_operator_api.py
```

It validates the schema family, exact capability matrix, version ordering, unknown/unavailable taxonomy, identity injection rejection, idempotency/revision requirements, default availability honesty, secret-safe backend failures, prohibited capability absence, typed fixture success, and fixture-adapter independence.

The current Feature Manifest remains validated separately with:

```bash
python scripts/validate_feature_manifest.py state/features/F-OPERATOR-CANONICAL-API-0001.yaml
```

Exact candidate-head CI and PR identity are recorded in the durable implementation handoff after the implementation commit/PR exists.

## Explicit unresolved v0.3 release blockers

This Feature does **not** complete or prove:

- two supported materially independent AI client adapters;
- durable protected Operation Store / append-only Journal;
- generation fencing, semantic-effect reservation, dispatch claim, launch linearization, callback/receipt recovery, or Persist linearization;
- Decision/Notification durable backing and authorization workflows;
- Developer → Reviewer → Remediation → Re-review → QA unattended vertical-loop dogfood;
- recovery/concurrency fault injection;
- release security/readiness publication;
- `VERSION` update or final `release/v0.3.0.yaml`.

Therefore this implementation must not be represented as v0.3 release readiness.
