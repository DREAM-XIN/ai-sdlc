# Design v2 — F-OPERATOR-VERTICAL-LOOP-0001

This design supersedes `design-v1` for implementation. It preserves the vertical-loop architecture and closes both MAJOR findings from `design-review.md`.

## 1. Objective and authority

Implement a durable trusted controller for:

`Implementation → independent Code Review → remediation → fresh Re-review → Verification QA → Operation DONE`

Feature Manifest + trusted Feature Event/Persist remain the only Feature lifecycle authority. Operation state is orchestration metadata only. QA PASS may leave the Feature at `acceptance: READY / release-gate: PENDING`; Operation `DONE` never means Feature workflow DONE.

## 2. Trusted composition

Production composition owns all authority-bearing inputs:

```text
TrustedVerticalLoopConfig
  repository
  target_ref
  manifest_path
  operation_profile = vertical-implementation-review-qa/v1
  trusted_role_policy
  collector_namespace_policy
  store configuration
  feature truth/Persist gateway
  role dispatch gateway
```

None of `operation_profile`, role policy, collector namespace, Store ref, credentials, provider routing or trusted candidate binding can be supplied or overridden by canonical client payload, target Feature files, Worker payload or chat input.

## 3. MAJOR-1 closure: immutable trusted Operation profile

### 3.1 Profile provenance

The supported profile constant is:

```text
VERTICAL_PROFILE = vertical-implementation-review-qa/v1
```

A profile-bound production start backend is constructed only from `TrustedVerticalLoopConfig`. Canonical `operation.start` remains schema-compatible and contains no profile selector.

The canonical caller asks to start an Operation; trusted runtime composition decides which backed profile, if any, is available for that installed runtime.

### 3.2 Store planner extension

Extend trusted Store planner API to:

```python
plan_operation_start(
    snapshot,
    *,
    target_repository,
    feature_id,
    expected_revision,
    idempotency_key,
    occurred_at,
    trusted_context_digest,
    operation_profile: str | None = None,
)
```

`operation_profile` is a trusted function argument, not request data.

New vertical start composition always passes `VERTICAL_PROFILE`. Legacy/general Store callers may pass `None` and retain prior behavior.

### 3.3 Immutable journal binding

`operation.started` payload adds:

```text
operation_profile: string|null
```

That value is immutable because the start Event itself is immutable. No later Event may modify profile identity.

Projection rebuild adds:

```text
operation_profile
```

and takes it only from the unique `operation.started` Event.

### 3.4 Equivalent-start compatibility

Existing-active-operation convergence requires all of:

```text
target_repository
feature_id
expected_feature_revision
operation_profile
```

to be compatible.

If a Feature already has a nonterminal Operation whose profile differs from the trusted requested profile, start fails closed with a bounded state/claim error; it never silently adopts or rewrites the other profile.

Idempotency for a compatible same-profile start returns the existing Operation.

### 3.5 Resume authorization

`VerticalLoopResumeBackend` requires projection `operation_profile == VERTICAL_PROFILE`.

- exact supported profile → may reconcile/resume;
- `None` legacy profile → readable/status/cancellable, not vertical-resumable;
- different/unknown profile → `CAPABILITY_UNAVAILABLE` or bounded invalid-state error;
- no migration rewrites a legacy Operation profile.

Tests prove canonical request fields cannot inject `operation_profile` and that a profile conflict cannot converge.

## 4. Feature truth model

`FeatureSnapshot` is a trusted read model obtained from target repository/ref and contains:

```text
repository
feature_id
target_ref
revision
manifest_digest
current_stage
stage statuses
gates
active remediation tasks
candidate_pr_number?
candidate_head_sha?
```

Every `advance()` iteration re-reads Feature truth and Store truth. No cached chat/session data is authoritative.

## 5. Durable vertical steps and stable stops

Deterministic steps:

```text
IMPLEMENTATION_WORK
CODE_REVIEW
CODE_REMEDIATION
CODE_REREVIEW
VERIFICATION_QA
```

Stable Operation statuses:

```text
WAITING_EXTERNAL
BLOCKED
NEEDS_USER
DONE
CANCELLED
```

Store projection adds `NEEDS_USER`; canonical status schema already accepts an open string and therefore requires no v1 API version change.

`operation.needs-user` records only bounded stop metadata and does not fabricate a Decision object.

## 6. Operation DONE vs Feature DONE

After trusted QA PASS Persist is confirmed:

```text
Feature:
  verification: DONE
  verification-gate: PASS
  acceptance: READY
  release-gate: PENDING

Operation:
  status: DONE
```

The QA translator is structurally incapable of changing `release-gate`, `acceptance` to DONE, or Feature workflow status. Product Acceptance remains outside this Feature.

## 7. Semantic task identity and dispatch

Task identities remain deterministic:

```text
vertical:implementation:<feature-revision>
vertical:code-review:<candidate-head>
vertical:code-remediation:<task-id>:<candidate-head>
vertical:code-rereview:<task-id>:<candidate-head>
vertical:verification:<candidate-head>
```

They feed the existing generation-independent semantic-effect reservation and stable external dispatch key.

Every dispatch binds exact repository, Feature, expected revision/stage, role, task, generation and candidate head when applicable. `dispatch.launch.authorized` remains the durable launch linearization point.

## 8. Trusted callback envelope

Worker callback transport is normalized by trusted collector code into:

```json
{
  "trusted_context": {
    "operation_id": "...",
    "operation_generation": 1,
    "operation_profile": "vertical-implementation-review-qa/v1",
    "semantic_effect_key": "...",
    "external_dispatch_key": "...",
    "dispatch_id": "...",
    "runtime_receipt_identity": "...",
    "target_repository": "owner/repo",
    "target_ref": "feature/...",
    "feature_id": "F-...",
    "expected_revision": 12,
    "feature_stage": "code-review",
    "task_id": "...",
    "role": "reviewer",
    "candidate_pr_number": 123,
    "candidate_head_sha": "...",
    "worker_identity": "trusted-worker-id",
    "collector_identity": "trusted-collector-id"
  },
  "collected_outputs": [],
  "worker_payload": {}
}
```

All authority-bearing context is collector/runtime derived. Worker self-declared duplicates cannot override it.

## 9. MAJOR-2 closure: collector-owned artifact/evidence provenance

### 9.1 Worker payload cannot name authoritative repository outputs

Role Worker schemas prohibit fields that could directly select an authoritative record or repository location, including:

```text
uri
path
repository_path
artifact_id
evidence_id
event
events
proposed_events
manifest_patch
gate
expected_revision
trusted_context
collected_outputs
```

Worker may return bounded logical output descriptions such as:

```json
{
  "outputs": [
    {"label": "implementation-summary", "kind": "artifact"},
    {"label": "test-evidence", "kind": "evidence"}
  ]
}
```

These are recommendations/labels only and are never directly registered in the Feature Manifest.

### 9.2 Trusted materialization

The trusted collector materializes actual files/artifacts into a bounded namespace selected by trusted policy, conceptually:

```text
docs/features/<feature-id>/worker-runs/<dispatch-id>/<collector-generated-name>
```

The Worker cannot choose `<feature-id>`, `<dispatch-id>` or repository path.

Collector rejects path traversal, symlinks/out-of-root targets and duplicate conflicting logical labels.

### 9.3 CollectedOutputReceipt

For every materialized output the collector emits an immutable trusted receipt:

```text
output_id                 # collector-generated stable id
label                     # bounded Worker logical label
kind = artifact|evidence
media_type
trusted_uri               # collector-selected bounded URI/path
sha256
size_bytes
operation_id
operation_generation
operation_profile
semantic_effect_key
external_dispatch_key
dispatch_id
worker_role
worker_identity
target_repository
feature_id
expected_revision
candidate_head_sha?
collector_identity
collected_at
```

The receipt itself is part of trusted callback context, not Worker payload.

### 9.4 Translator consumption

A role translator may register Feature artifact/evidence records only by converting a `CollectedOutputReceipt` that passes all bindings:

- Operation/generation/profile match durable authorization;
- semantic/external dispatch key and dispatch id match;
- role/worker identity match trusted receipt;
- repository/Feature/revision match current translation context;
- candidate head matches when candidate-bound;
- `trusted_uri` is inside trusted collector namespace;
- referenced bytes exist and re-hash to exact `sha256` when repository materialization is used;
- receipt has not been consumed under conflicting semantics.

Translator generates Manifest artifact/evidence IDs itself from role/task/output identity. Worker never chooses them.

Missing output, digest mismatch, stale revision/candidate, namespace mismatch or receipt-binding mismatch rejects the result and stops fail-closed.

## 10. Strict role Worker schemas

Schemas:

```text
spec/operator/vertical/developer-result.schema.json
spec/operator/vertical/reviewer-result.schema.json
spec/operator/vertical/qa-result.schema.json
spec/operator/vertical/collected-output-receipt.schema.json
```

All Worker payload schemas use `additionalProperties: false`.

Developer:

```text
status = COMPLETED | BLOCKED | NEEDS_USER
outputs[] {label, kind}
summary
candidate_head_sha? # advisory only; must equal trusted refetch if present
```

Reviewer:

```text
verdict = PASS | REWORK | BLOCKED | NEEDS_USER
findings[] {severity, code, summary}
outputs[] {label, kind=evidence}
summary
```

QA:

```text
verdict = PASS | REWORK | BLOCKED | NEEDS_USER
checks[]
outputs[] {label, kind=evidence}
summary
```

No role payload accepts authoritative output URI/id/path.

## 11. Role independence

Trusted `RoleIndependencePolicy` consumes only collector/runtime identities.

- Reviewer differs from Developer identity for candidate.
- remediation Developer cannot satisfy fresh re-review.
- re-review is a new dispatch and reviewer identity differs from remediation Developer.
- QA has role `qa` and satisfies configured separation from active Developer/Reviewer identities.
- ambiguity fails closed to BLOCKED/NEEDS_USER.

Feature branch content cannot weaken this policy.

## 12. Role-specific translators

Translators receive only:

```text
current FeatureSnapshot
trusted dispatch context
validated Worker payload
validated CollectedOutputReceipt[]
trusted role policy
```

They return one deterministic bounded Feature Event candidate.

Developer translator may register trusted collected implementation/remediation artifacts/evidence and complete allowed implementation/remediation tasks/stages, but cannot PASS code/verification gates.

Reviewer PASS translator may register collected review evidence, approve the exact implementation artifact, PASS code-gate, mark code-review DONE and verification READY.

Reviewer REWORK translator may register review evidence and create one bounded Developer remediation task using trusted-generated id/feedback. No arbitrary role/stage/task type is accepted from Worker.

QA PASS translator may register verification evidence, PASS verification-gate, mark verification DONE and acceptance READY. It cannot change release-gate or acceptance DONE.

QA REWORK is permitted only when an existing repository lifecycle transition/translator contract explicitly supports the concrete current state; otherwise controller stops BLOCKED instead of inventing authority.

## 13. Persist coordinator

Feature mutation path is exclusively:

```text
validated trusted envelope
→ role translator
→ deterministic bounded Event
→ Operation persist.requested
→ refetch Feature + candidate + identity/fence checks
→ Operation persist.linearized
→ trusted FeaturePersistGateway
→ exact Event lookup/reconciliation
→ Operation persist.confirmed
```

No direct Manifest writer exists in vertical controller code.

Lost Persist acknowledgement is reconciled by exact Event ID/digest and authoritative Feature revision, never by blind replay.

## 14. Callback and automatic continuation

Callback flow:

1. correlate callback to existing launch authorization;
2. durable idempotent callback record;
3. construct trusted envelope;
4. validate role payload and collected-output receipts;
5. enforce role independence and exact Feature/candidate binding;
6. translate and Persist if authorized;
7. refetch authoritative truth;
8. call the same deterministic `advance()` planner automatically.

Normal callback-driven continuation therefore does not require user `continue` messages.

Duplicate identical callbacks converge; conflicting callback identity/digest fails closed.

## 15. `operation.resume`

Vertical resume composition is enabled only when all trusted dependencies exist:

- writable protected Store;
- profile-bound start/resume backend;
- Feature truth/Persist gateway;
- role dispatch/lookup/collector gateway;
- role independence policy;
- collector namespace policy.

Resume validates projection profile before any action. It reconciles outstanding launch/Persist facts and advances only until WAITING_EXTERNAL, BLOCKED, NEEDS_USER, DONE or CANCELLED.

Routine external callbacks invoke `advance()` automatically; resume is for suspended/recovery invocation, not polling.

## 16. Store additions

Additive Store changes:

- `VALID_STATUSES += NEEDS_USER`;
- projection field `operation_profile`;
- `operation.started.operation_profile` optional for backwards compatibility;
- new accepted audit/status events:
  - `operation.needs-user`;
  - `loop.step.selected`;
  - `worker.result.validated`;
  - `worker.result.rejected`;
  - `feature.event.translated`;
  - `loop.stable-stop`.

Unknown event types remain rejected. These facts never mutate Feature lifecycle state.

## 17. Recovery

Recovery keeps the Store Feature's fail-closed rules:

- authorized launch + missing ack → lookup same external key;
- NOT_LAUNCHED → revalidate fences then retry same key;
- LAUNCHED → adopt receipt;
- UNKNOWN → no speculative relaunch, survives takeover;
- callback durable + handler lost → reconstruct trusted envelope from stored context/collector receipt;
- Persist linearized + ack lost → exact Event/Manifest lookup;
- CAS conflict → re-read Store + Feature + policy and semantically re-plan;
- stale candidate → reject old result, new candidate gets new semantic task;
- superseded generation → no new decisions from old generation;
- NEEDS_USER → stable stop, no fake Decision.

## 18. Verification requirements

Deterministic validation must prove:

1. profile is selected only by trusted composition, not canonical/Worker input;
2. profile is immutable/rebuildable and profile-conflicting start fails closed;
3. legacy unprofiled Operation cannot vertical-resume;
4. Worker URI/id/path/event/proposed-events fields are schema-rejected;
5. collector receipt namespace/digest/binding validation;
6. translator can register only collector-owned receipts;
7. happy Developer → Reviewer PASS → QA PASS path;
8. review REWORK → remediation → fresh re-review PASS → QA path;
9. Operation DONE while Feature remains acceptance READY/release-gate PENDING;
10. Reviewer/QA independence failure;
11. stale revision/stage/candidate before launch/Persist;
12. duplicate/conflicting callback;
13. lost launch/callback/Persist acknowledgements;
14. UNKNOWN takeover inheritance;
15. cancellation order around launch/Persist;
16. Store CAS re-plan;
17. fresh controller/process reconstruction without chat history;
18. NEEDS_USER stable stop;
19. unsupported profile resume fails honestly;
20. MCP remains read-only and inbox/Decision/Notification availability remains honest;
21. all existing protocol/lifecycle/security/cross-repo/public-runtime regressions.

## 19. Backward compatibility

Canonical API version remains `ai-sdlc.operator/v1`.

Existing Operation Store callers using no profile keep working. Existing Operations rebuild with `operation_profile = null` and remain status/cancel compatible. They are not silently upgraded to the vertical profile.

MCP production tool list is unchanged.

## 20. Non-goals

No Product Acceptance automation, Decision persistence, Notification Outbox, complete inbox, Requirement/Design/Plan automation, project takeover, second AI adapter, dogfood or release publication is introduced by this Feature.
