# Design — F-OPERATOR-VERTICAL-LOOP-0001

## 1. Design objective

Build one trusted, durable vertical orchestration controller over the existing Operator Store and existing autonomous role workers:

`Implementation → Code Review → Remediation → fresh Re-review → Verification → Operation stable stop`

The design deliberately does not create another Feature lifecycle state machine. The Feature Manifest remains authoritative; the Operation controller only derives and records orchestration facts needed to choose the next safe action.

## 2. Upstream components reused unchanged

The implementation SHALL reuse:

- canonical `ai-sdlc.operator/v1` dispatch and schema validation;
- `OperatorStoreRuntime`, protected remote state ref, remote CAS and re-plan;
- semantic-effect reservation / stable `external_dispatch_key`;
- generation-specific dispatch claim;
- `dispatch.launch.authorized` linearization;
- launch lookup `NOT_LAUNCHED | LAUNCHED | UNKNOWN`;
- callback correlation;
- `persist.requested | persist.linearized | persist.confirmed`;
- cancellation/supersession/takeover rules;
- existing trusted gh-aw role routing and autonomous Developer/Reviewer/QA worker contracts;
- existing Feature Event + trusted Persist validators.

No role Worker obtains Store or Feature Persist credentials.

## 3. New trusted modules

Reference implementation modules:

```text
scripts/operator_vertical_loop.py
scripts/operator_vertical_result.py
scripts/operator_vertical_translators.py
scripts/operator_vertical_runtime.py
scripts/operator_vertical_backends.py
scripts/validate_operator_vertical_loop.py
```

Responsibilities:

### `operator_vertical_loop.py`

Pure deterministic orchestration reducer/planner. Inputs are authoritative Feature snapshot + durable Operation snapshot + trusted policy/identity registry. Output is a bounded next-action plan, never network/Git mutations.

### `operator_vertical_result.py`

Strict trusted Worker Result envelope and role-specific payload validators. Rejects unknown lifecycle-affecting fields and any `event`, `events`, `proposed_events`, `manifest_patch`, `gate`, `revision`, or trusted execution payload.

### `operator_vertical_translators.py`

Deterministic role-specific result translators that emit bounded Feature Event documents from trusted dispatch context + validated Worker Result + current authoritative Feature snapshot.

### `operator_vertical_runtime.py`

Trusted coordinator that composes Feature truth reader, Operation Store, role gateway, identity policy, translator registry and Feature Persist gateway.

### `operator_vertical_backends.py`

Canonical `operation.resume` backend for supported `vertical-implementation-review-qa/v1` Operations only.

## 4. Feature truth model

`FeatureSnapshot` is read from the target repository/ref by trusted control code and includes at least:

```text
repository
feature_id
target_ref
revision
current_stage
stage statuses
gates
candidate_pr_number
candidate_head_sha when applicable
manifest_digest
```

The controller never writes this structure directly.

Every planner iteration begins by loading a fresh `FeatureSnapshot` and a fresh Operator Store snapshot.

## 5. Vertical profile identity

Operations started for this loop carry immutable profile metadata in `operation.started` payload:

```text
operation_profile = vertical-implementation-review-qa/v1
```

`operation.resume` is available only when this metadata is present and supported.

Legacy/unprofiled Operations created by the previous Store Feature remain readable/status/cancellable but are not automatically resumed by this backend.

## 6. Operation status extension

Extend Store projection status vocabulary with:

```text
NEEDS_USER
```

Add an immutable Event:

```text
operation.needs-user
```

with bounded payload:

```text
reason_code
feature_revision
current_stage
candidate_head_sha? 
```

This event does **not** create a Decision object. It is only the honest stable Operation stop required by the frozen release slice until workstream #5 provides Decision/Authorization persistence.

`NEEDS_USER` is nonterminal in the broader product sense but a stable stop for this Feature. It is discoverable later by `operator.inbox`; this Feature does not make complete inbox available.

No automatic dispatch or Persist may occur while status is `NEEDS_USER`.

## 7. Operation DONE versus Feature DONE

Requirement Review MINOR-1 is resolved structurally:

- Operation `DONE` means the bounded vertical automation slice completed successfully after QA PASS reconciliation;
- after QA PASS, the translated Feature Event must leave authoritative Feature state at normal `acceptance: READY` with `release-gate: PENDING`;
- the controller may then append `operation.done`;
- it MUST NOT set Feature `workflow.status: DONE`, PASS `release-gate`, create Acceptance evidence, or invoke Product Acceptance automatically.

Tests assert this exact separation.

## 8. Loop-step facts

The controller records immutable orchestration facts as Operation Events rather than relying on process memory:

```text
loop.step.selected
worker.result.validated
worker.result.rejected
feature.event.translated
loop.stable-stop
```

`loop.step.selected` payload binds:

```text
step
feature_revision
feature_stage
role
task_identity
candidate_head_sha?
```

These facts are audit/recovery metadata. They do not override the Feature Manifest.

The Store reducer must accept these event types without changing lifecycle state except where explicit status transitions are normative.

## 9. Deterministic next-step derivation

The planner derives the semantic next step primarily from authoritative Feature state:

### Implementation

If Feature is `implementation: READY|WORKING`, choose `IMPLEMENTATION_WORK` with role `developer`.

If Feature implementation is DONE and `code-review: READY|WORKING`, choose `CODE_REVIEW` with role `reviewer` and exact candidate head.

### Code Review rework

If authoritative lifecycle has an active code-review remediation task in `TODO|WORKING`, choose `CODE_REMEDIATION` with role `developer`.

After remediation completion returns lifecycle to a review-ready state, choose `CODE_REREVIEW` using a fresh reviewer dispatch semantic task identity.

### Verification

If code-gate PASS and `verification: READY|WORKING`, choose `VERIFICATION_QA` with role `qa` and exact approved candidate head.

### After QA PASS

If verification-gate PASS and `acceptance: READY`, append Operation `DONE`; do not perform Acceptance.

### Human/policy stop

If Feature state requires an authority not supplied by this profile, append `operation.needs-user` with bounded reason.

Inconsistent/unrecognized state becomes `BLOCKED` rather than guessed progression.

## 10. Semantic task identities

Deterministic task identities:

```text
vertical:implementation:<feature-revision>
vertical:code-review:<candidate-head>
vertical:code-remediation:<remediation-task-id>:<candidate-head>
vertical:code-rereview:<remediation-task-id>:<candidate-head>
vertical:verification:<candidate-head>
```

These feed the existing generation-independent semantic-effect key.

A candidate head change necessarily changes Reviewer/QA semantic task identity and requires a new reservation/dispatch. Generation change alone does not.

## 11. Trusted dispatch gateway

Define a narrow `RoleDispatchGateway` protocol:

```text
launch(external_dispatch_key, trusted_dispatch_request) -> launch receipt
lookup(external_dispatch_key) -> NOT_LAUNCHED | LAUNCHED | UNKNOWN + receipt identity
```

`trusted_dispatch_request` is built solely from trusted controller context and contains role/stage/task/repository/ref/revision/candidate identity plus safe Worker input references.

The first implementation may adapt existing gh-aw dispatch/collector surfaces. Target repositories cannot select provider/model/profile/worker beyond already-approved trusted routing policy.

The controller always commits `dispatch.launch.authorized` before calling `launch()`.

## 12. Trusted Result Envelope

Collected Worker output is wrapped by trusted collector code:

```json
{
  "trusted_context": {
    "operation_id": "...",
    "operation_generation": 1,
    "semantic_effect_key": "...",
    "external_dispatch_key": "...",
    "dispatch_id": "...",
    "target_repository": "owner/repo",
    "feature_id": "F-...",
    "expected_revision": 12,
    "target_ref": "feature/...",
    "task_id": "...",
    "role": "reviewer",
    "candidate_pr_number": 123,
    "candidate_head_sha": "...",
    "runtime_receipt_identity": "...",
    "worker_identity": "trusted-worker-id"
  },
  "worker_payload": { ... }
}
```

Translator-driving fields come from `trusted_context`; duplicate fields in Worker payload are rejected or ignored only when schema proves they are non-authoritative.

## 13. Role-specific Worker Result schemas

Use JSON schemas under:

```text
spec/operator/vertical/developer-result.schema.json
spec/operator/vertical/reviewer-result.schema.json
spec/operator/vertical/qa-result.schema.json
```

All use `additionalProperties: false`.

### Developer payload

Bounded fields:

```text
status = COMPLETED | BLOCKED | NEEDS_USER
artifacts[]
evidence[]
summary
candidate_head_sha?   # informational; must equal trusted/refetched head if used
```

No gate verdict.

### Reviewer payload

```text
verdict = PASS | REWORK | BLOCKED | NEEDS_USER
findings[] { severity, code, summary }
evidence[]
summary
```

No executable Event fields.

### QA payload

```text
verdict = PASS | REWORK | BLOCKED | NEEDS_USER
checks[]
evidence[]
summary
```

QA `REWORK` does not invent an arbitrary lifecycle transition; translator only emits a Feature Event shape already legal for Verification rework in existing lifecycle rules. If no approved lifecycle shape exists for the concrete state, controller stops BLOCKED rather than creating one.

## 14. Translator allow-lists

Each translator emits an in-memory bounded Event candidate. It does not commit directly.

### Developer completion

Allowed mutations are limited to the current implementation/remediation task's artifact/evidence/task completion and stage transitions already accepted by the repository lifecycle validator.

Developer may not emit any gate PASS.

### Reviewer PASS

Allowed:

- review evidence record;
- approve exact implementation artifact when lifecycle requires;
- `code-gate: PASS`;
- code-review DONE;
- verification READY.

Only after current Feature revision/candidate/ref are revalidated.

### Reviewer REWORK

Allowed:

- review evidence failure/rework record;
- one bounded remediation task whose role is Developer and whose source stage is code-review;
- code-review remains/re-enters WORKING as defined by existing lifecycle contract.

No arbitrary task type/stage/role.

### QA PASS

Allowed:

- verification evidence;
- `verification-gate: PASS`;
- verification DONE;
- acceptance READY.

Explicitly disallowed: release-gate mutation, acceptance DONE, Feature workflow DONE.

## 15. Identity independence policy

Introduce trusted `RoleIndependencePolicy` consuming identities from dispatch/collector receipts.

Minimum constraints for this profile:

- Code Reviewer identity must differ from the Developer identity for the candidate under review;
- remediation Developer identity must not satisfy re-review;
- re-review dispatch must be a fresh dispatch and its reviewer identity must differ from remediation Developer;
- QA identity must have role `qa` and differ from the active Developer and Reviewer identities when policy requires;
- Worker self-declared identity cannot satisfy any check.

The policy source is protected/default-branch or trusted installation configuration. Feature branch input may not weaken it.

Failure maps to `BLOCKED` or `NEEDS_USER` according to trusted policy classification.

## 16. Persist coordinator

`FeaturePersistGateway` protocol:

```text
inspect_feature(binding) -> FeatureSnapshot
persist_event(event_document, expected_feature_revision, target_ref) -> PersistReceipt
lookup_event(event_id) -> absent | applied(revision,digest) | conflict
```

Algorithm:

1. translate validated result to deterministic bounded Event document and ID;
2. append Operation `feature.event.translated` audit fact;
3. append `persist.requested` binding exact Event id/digest, expected Feature revision and candidate head;
4. re-fetch Feature state and candidate head;
5. verify generation/cancel/supersession and identity policy;
6. append `persist.linearized`;
7. call trusted Persist gateway;
8. on lost acknowledgement, `lookup_event(event_id)` and authoritative Manifest inspection determine whether it applied;
9. append `persist.confirmed` only after exact correlation.

No alternate direct Manifest writer exists in vertical-loop code.

## 17. Callback processing

Collector callback processing:

1. validate external dispatch key and runtime receipt against durable authorization;
2. append `worker.callback.recorded` idempotently;
3. build trusted Result Envelope from stored dispatch context + receipt, not Worker claims;
4. strict-validate role schema;
5. check role independence and exact Feature/candidate bindings;
6. append `worker.result.validated` or `worker.result.rejected`;
7. translate and Persist if allowed;
8. continue planner automatically from newly authoritative Feature state until next dispatch or stable stop.

Duplicate callback digest is a no-op/convergent result. Same callback id with different digest fails closed.

## 18. `operation.resume` backend

Add `VerticalLoopResumeBackend` to canonical composition only when:

- durable Store is writable/protected;
- Feature truth/Persist gateways are configured;
- supported role gateway/collector is configured;
- trusted independence policy is configured.

Invocation requires canonical expected Feature revision and operation id/generation context.

Resume loads durable state, verifies profile, reconciles outstanding dispatch/Persist facts, and invokes planner until it reaches one of:

```text
WAITING_EXTERNAL
BLOCKED
NEEDS_USER
DONE
CANCELLED
```

It does not wait synchronously for Worker completion.

Routine callbacks invoke the same `advance()` function automatically; explicit `operation.resume` is for suspended/recovery states, not normal polling.

## 19. Recovery matrix

| Durable situation | Recovery action |
|---|---|
| launch authorized; no local dispatch ack | lookup same external key; never infer not-launched |
| lookup NOT_LAUNCHED | current generation revalidates fences, then same key may launch |
| lookup LAUNCHED | adopt receipt; no relaunch |
| lookup UNKNOWN | remain BLOCKED; takeover inherits same reservation/key |
| callback recorded; local processing lost | rebuild envelope from receipt/context and idempotently reprocess |
| persist linearized; local ack lost | lookup exact Feature Event + Manifest revision; confirm or fail closed |
| CAS conflict | re-read Store + Feature and semantically re-plan |
| candidate changed | old result orphan/reject; new candidate requires new semantic task |
| generation superseded | old generation cannot make new decisions |
| NEEDS_USER | stop; no automatic resume until later Decision workstream supplies authority |

## 20. Security boundary

The vertical controller may receive trusted credentials necessary for Store and Feature Persist gateways, but these credentials are never passed to role workers.

Worker artifacts/results are untrusted data until strict schema + trusted-context validation.

No generic shell/event/Manifest mutation endpoint is exposed through canonical API, MCP, result payload, or role gateway.

## 21. Validation strategy

Add deterministic in-memory fixtures for Feature truth/Persist and role gateway, using the real Operation Store model/planners.

Required tests cover:

- happy path Developer → Reviewer PASS → QA PASS, ending Operation DONE while Feature acceptance READY;
- review REWORK → remediation → fresh re-review PASS → QA PASS;
- forbidden Worker Event/proposed-events payload rejection;
- developer/reviewer/QA identity independence failures;
- stale revision/stage/candidate before launch and before Persist;
- duplicate callback convergence/conflicting callback rejection;
- launch lost-ack lookup states;
- UNKNOWN takeover inheritance;
- cancellation ordering around launch/Persist linearization;
- Store CAS re-plan;
- Persist lost-ack reconciliation;
- restart reconstruction from fresh controller instance;
- NEEDS_USER stable stop without Decision fabrication;
- unsupported operation profile resume unavailable/invalid state;
- MCP adapter remains read-only;
- `operator.inbox`, Decisions and Notifications remain unavailable;
- all existing protocol/security/public-runtime validators.

## 22. Migration/backward compatibility

- Existing Store Operations without the vertical profile remain valid for status/cancel and are not auto-resumed.
- Existing status response schema already accepts string status, so `NEEDS_USER` requires no canonical API schema version change.
- Existing MCP adapter tool surface remains unchanged.
- Store Event schema version remains `ai-sdlc.operation-event/v1`; new event types are additive but reducer validation remains fail-closed for unknown types.

## 23. Non-goals reaffirmed

No Decision objects, Notification Outbox, complete inbox, Product Acceptance automation, Requirement/Design/Plan automation, project takeover, release publication, or second AI adapter is introduced here.
