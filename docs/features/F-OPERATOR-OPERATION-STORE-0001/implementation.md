# Implementation — F-OPERATOR-OPERATION-STORE-0001

## Candidate

Functional implementation candidate: `9418094c485f89c663de4bc4c7621d943a96c237` on `feature/F-OPERATOR-OPERATION-STORE-0001`.

This implementation realizes the approved Requirement v2 and Design v2 bounded durable Operator Store substrate. It does not implement the later Developer → Reviewer → Remediation → Re-review → QA orchestration loop, Decision/Notification product backing, broad recovery policy, or v0.3 release publication.

## Delivered components

### Store contracts and deterministic model

- `spec/operator/store/operation-event.schema.json`
- `spec/operator/store/operation-projection.schema.json`
- `spec/operator/store/semantic-reservation.schema.json`
- `spec/operator/store/dispatch-claim.schema.json`
- `spec/operator/store/feature-claim.schema.json`
- `spec/operator/store/protection-receipt.schema.json`
- `scripts/operator_store_model.py`

The model provides append-only `ai-sdlc.operation-event/v1` history, deterministic projection rebuild, generation-independent semantic-effect identity, stable external dispatch identity, immutable reservation/claim paths, and replaceable projection cache only.

### Pure semantic command planner

`scripts/operator_store.py` implements bounded pure commands for:

- Operation start/convergence;
- immutable semantic reservation;
- generation-specific dispatch claim;
- launch authorization linearization;
- NOT_LAUNCHED / LAUNCHED / UNKNOWN receipt correlation;
- callback correlation;
- Operation cancellation;
- trusted generation takeover;
- Persist requested / linearized / confirmed records;
- trusted unfinished-Operation discovery.

The planner emits `StoreMutationPlan` values and does not write Git/network state itself.

### Protection and exact-ref CAS

- `scripts/operator_store_protection.py`
- `scripts/operator_store_git.py`

Semantic writes require a repository/ref-bound trusted `PROTECTED` receipt. `UNPROTECTED`, `UNKNOWN`, missing, or mismatched receipts fail closed before Store mutation.

The Git backend performs exact state-ref CAS via `git update-ref <ref> <new> <expected>` against a commit descending from the exact expected state commit. CAS conflict handling re-reads state and re-runs the semantic planner rather than replaying stale bytes.

Reservation, dispatch-claim, feature-claim, and Operation Event objects are create-once at the trusted writer boundary. Only projection cache paths may be replaced.

### Canonical API backing

- `scripts/operator_store_backends.py`
- bounded structured-error propagation update in `scripts/operator_api.py`

This Feature honestly backs only:

- `operation.start`
- `operation.status`
- `operation.cancel`

`operation.start` requires trusted Feature verification bound to repository / Feature / expected revision. Canonical Store domain failures retain machine-readable canonical error codes.

`operator.inbox`, `operation.resume`, Decision backing, Notification backing, `decision.respond`, and `notification.ack` remain unbacked in this Feature. No MCP semantic-write tools are added.

### Trusted production composition

`scripts/operator_store_runtime.py` composes the Git-backed Store from `TrustedOperatorStoreConfig` plus a trusted protection verifier. The default state ref is `refs/heads/ai-sdlc-operator-state` and there is no canonical/MCP/Worker argument that can select or redirect it.

Constructing a runtime does not attest protection. Semantic-write availability remains dependent on positive trusted protection verification.

### Deterministic verification

- `scripts/validate_operator_store.py`
- `scripts/validate_operator_store_runtime.py`
- both are invoked by `scripts/validate.py`

Coverage includes immutable artifacts, projection rebuild, injected CAS conflict/re-planning, active-operation convergence, semantic/external key stability, duplicate dispatch claim convergence, launch/cancel ordering, UNKNOWN fail-closed behavior and takeover inheritance, callback binding/replay safety, Persist linearization ordering, lost-ack exact correlation, protection fail-closed, local Git exact-ref CAS, canonical start/status/cancel backing, and deferred capability honesty.

## Safety boundary notes

- Operation Store state is orchestration metadata, not Feature lifecycle authority.
- Store code never directly edits authoritative Feature Manifests or PASSes/Waives lifecycle gates.
- Launch authorization and Persist authorization remain distinct linearization boundaries.
- Cancellation does not retroactively revoke an already launch-linearized external side effect or already Persist-linearized exact Feature write; only exact post-cancel correlation/confirmation paths remain permitted.
- UNKNOWN remains BLOCKED across generation takeover and preserves the same semantic reservation / external dispatch key.
- Missing local acknowledgement is not interpreted as absence of an external launch or Feature Persist.
- Remote protection provisioning itself remains installation/control authority; this workstream provides the verifier/receipt boundary and fails closed when protection cannot be proved.

## Release boundary

Completion of this Feature establishes only the durable Operation Store / dispatch-safety substrate. It does not make v0.3.0 release-ready. The frozen workstreams for the role vertical loop, recovery/Decision/Notification, second supported AI client / write-capable dogfood as applicable, and final release evidence remain outstanding.
