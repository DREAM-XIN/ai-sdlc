# Acceptance — F-OPERATOR-OPERATION-STORE-0001

## Role

Independent Product / Acceptance owner.

## Authoritative starting state

- revision: `26`;
- stage: `acceptance`;
- `acceptance: WORKING`;
- requirement/design/code/verification gates: PASS;
- `release-gate: PENDING`.

## Verdict

**PASS**

The Feature satisfies approved `requirement-v2` within its explicitly bounded scope.

## Accepted product outcomes

### Durable repository-backed Operation Store

Operator state is represented under the versioned `state/operator/v1` logical namespace and persisted through a shared remote control-repository state ref rather than process-local/chat/local-runner state.

A fresh checkout can reconstruct committed Operation state.

### Protected state-ref authority boundary

Production semantic writes require positive trusted protection evidence for the exact repository/ref. Missing, ambiguous or insufficient protection fails closed; the test-only static verifier is not accepted by the normal production runtime.

Feature branches, Worker payloads and canonical client input do not select or self-attest the state ref/protection policy.

### Append-only / immutable safety core

Operation Events, semantic-effect reservations, dispatch claims and Feature claims are create-once immutable. Only projection cache is replaceable and canonical state is rebuildable from durable immutable history.

### CAS and concurrent writers

Remote writes bind to the exact durable state-ref SHA and use non-force remote update semantics. Conflicts cause durable re-read plus semantic re-plan; stale bytes are not replayed as last-writer-wins.

### Operation/generation ownership

The Store provides stable Operation identity, active Feature ownership and generation takeover primitives. Generation identifies orchestration ownership but does not redefine semantic side-effect identity.

### Semantic reservation and dispatch safety

Equivalent semantic work converges on one generation-independent semantic-effect reservation and one stable external dispatch key. Generation takeover preserves unresolved external identity.

### Launch linearization / cancellation

`dispatch.launch.authorized` is the durable launch linearization point. Cancellation/supersession before authorization prevents new launch; authorization first permits only exact correlation/completion of that already-authorized side effect.

### External UNKNOWN handling

NOT_LAUNCHED / LAUNCHED / UNKNOWN correlation is durably represented. UNKNOWN fails closed against speculative relaunch and survives generation takeover.

### Persist linearization

Launch authorization and Feature Persist authorization remain distinct. Persist requested/linearized/confirmed facts are separately durable and exact Event correlation is required.

### Canonical API integration

`operation.start`, `operation.status`, and `operation.cancel` receive durable Store backing without changing the frozen `ai-sdlc.operator/v1` schemas.

## Explicitly not accepted as part of this Feature

This Feature does **not** claim completion of:

- automated Developer → Reviewer → Remediation → Re-review → QA vertical orchestration;
- `operation.resume`;
- complete `operator.inbox` semantics;
- Decision/Authorization persistence and UX;
- Notification Outbox persistence and UX;
- a second materially independent AI client adapter;
- full v0.3 recovery/product dogfood/publication/release readiness.

Those remain later frozen v0.3 workstreams.

## Evidence basis

Acceptance relies on the approved Requirement/Design, fresh Code Re-review PASS and independent Verification PASS. The validated remediation runtime candidate `0a6cd5d19f51aef1ded3c6610740e0fc57cc4ba1` passed Protocol, Public Runtime and Required PR Gate, and later lifecycle commits did not change the runtime/test/dependency tree.

## Recommendation

Feature-level `release-gate`: **PASS** and lifecycle may complete.

This is a Feature acceptance decision only; it must not be interpreted as overall v0.3 release authorization.

## Final lifecycle receipt

Trusted Persist subsequently materialized the accepted Feature as authoritative Manifest revision `27` with `workflow.status: DONE` and requirement/design/code/verification/Feature-level release gates all `PASS`. This final receipt does not expand the accepted scope or alter the runtime candidate.
