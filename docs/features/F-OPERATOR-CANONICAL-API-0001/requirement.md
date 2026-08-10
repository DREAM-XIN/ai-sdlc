# Requirement — v0.3 canonical typed Operator API foundation

Feature: `F-OPERATOR-CANONICAL-API-0001`

Issue: `#208`

Profile: `standard-feature`

Immutable Release Spec baseline: `c1980bba3205062495e49e685f9501a248df8365`

Approved Release Spec source head: `2e1fd261d4f1142b6b1d6fdf1b86e0027254f0c4`

## Problem

AI-SDLC v0.2 has a deterministic lifecycle authority model and GitHub-native transports, but supported AI clients do not yet have one transport-independent, versioned Operator contract through which they can discover capabilities, inspect installed projects and Features, discover unfinished user work, and invoke bounded Operator operations.

Without a canonical boundary, later v0.3 orchestration work risks coupling semantics to one transport, encoding incompatible errors or identity rules in different adapters, or allowing an adapter to substitute arbitrary Manifest/Event/shell mutation for bounded Operator operations.

The frozen v0.3 Release Spec therefore places the canonical typed API first in the implementation order. This Feature establishes that contract and its deterministic validation/conformance foundation before durable Operation Store, dispatch, recovery, Decision/Notification backing behavior, or release dogfood is implemented.

## Goal

Implement a transport-independent canonical Operator API foundation identified as:

```text
ai-sdlc.operator/v1
```

The foundation must give every supported adapter the same version semantics, capability vocabulary, request/response envelopes, structured error model, authenticated identity propagation, lifecycle-sensitive revision binding, idempotency rules, and conformance tests while preserving existing AI-SDLC lifecycle authority.

The Feature must make later v0.3 work additive behind the canonical boundary rather than requiring each transport to invent its own control semantics.

## Required outcomes

1. **One canonical API identity.** Every canonical request and response identifies `ai-sdlc.operator/v1`; unsupported versions fail before semantic writes.
2. **Transport-independent contracts.** Canonical operation schemas/interfaces are independent of MCP, ChatGPT, CLI, HTTP, GitHub Issue syntax, or any other specific transport.
3. **Complete v0.3 capability vocabulary.** The canonical contract defines the frozen capability names and their typed request/response shapes:
   - `system.capabilities`
   - `project.inspect`
   - `feature.status`
   - `operator.inbox`
   - `operation.start`
   - `operation.status`
   - `operation.resume`
   - `operation.cancel`
   - `decision.list`
   - `decision.respond`
   - `notification.list`
   - `notification.ack`
4. **Honest capability availability.** A capability whose trusted backing workstream is not implemented in this Feature must be discoverable as unavailable or return the canonical `CAPABILITY_UNAVAILABLE` error. The implementation must not fabricate durable Operation, Decision, Notification, dispatch, recovery, or dogfood state.
5. **Structured errors.** Clients consume machine-readable error codes and details rather than parsing human prose.
6. **Identity is explicit and non-authoritative by itself.** Canonical calls distinguish human principal when represented, AI client adapter identity, trusted service/runtime identity, and requested capability. AI client identity alone never implies human authorization.
7. **Semantic writes are revision/idempotency aware.** Write requests carry or derive the exact target and lifecycle-sensitive context required by the frozen Release Spec, including repository, Feature, expected revision where applicable, idempotency key, authenticated identities, requested capability, and trusted authorization-policy context.
8. **Adapters cannot escape the canonical boundary.** The contract does not expose arbitrary Feature Manifest patches, arbitrary executable Feature Events, unrestricted shell execution, arbitrary Gate writes, or generic repository mutation as substitutes for canonical operations.
9. **Reusable conformance harness.** A deterministic harness can run the same canonical subset against two materially independent supported AI client adapters in later/parallel adapter Features without duplicating semantic assertions.
10. **Existing v0.2 authority remains unchanged.** Canonical API code may read or request trusted existing control paths, but it does not become Feature lifecycle authority.

## Canonical version contract

### API identifier

The only API version introduced by this Feature is:

```text
ai-sdlc.operator/v1
```

Every request and response must include or be unambiguously bound to that version.

`system.capabilities` must expose the supported canonical API versions and capability availability for the caller context.

A request for an unsupported API version must return:

```text
UNSUPPORTED_API_VERSION
```

before any semantic write is attempted.

Backward-incompatible contract changes require a different canonical API version; they must not silently alter `ai-sdlc.operator/v1` semantics.

## Capability contract

### Read/discovery capabilities

The API foundation must define typed contracts for:

- `system.capabilities`
- `project.inspect`
- `feature.status`
- `operator.inbox`
- `operation.status`
- `decision.list`
- `notification.list`

The canonical conformance subset required by the frozen Release Spec is:

```text
system.capabilities
feature.status
operator.inbox
operation.status
decision.list
notification.list
```

`project.inspect` is also a required canonical capability even though it is outside that minimum two-adapter conformance subset.

`operator.inbox` must be modeled as the new-session discovery primitive. Its contract must be capable of representing unfinished Operations, `NEEDS_USER`/`BLOCKED` Operations, pending Decisions, and unread Notifications without requiring the caller to already know an `operation_id`.

This Feature does not need to create those durable records; until the corresponding trusted stores exist, availability and errors must remain honest.

### Write/recovery capability shapes

The API foundation must define typed request/response contracts for:

- `operation.start`
- `operation.resume`
- `operation.cancel`
- `decision.respond`
- `notification.ack`

Defining a typed contract is not evidence that the durable operation is implemented. If the backing capability is unavailable, the canonical result must be `CAPABILITY_UNAVAILABLE` with no semantic side effect.

`operation.resume` must be described as explicit policy-permitted recovery from a suspended state such as `BLOCKED`; routine external callbacks are not modeled as user-driven resume commands.

## Request and response envelope requirements

Canonical envelopes must have deterministic schemas and validation rules.

At minimum, requests must support the trusted system deriving or carrying, when applicable:

- canonical API version;
- request/correlation identity;
- requested capability;
- target repository;
- Feature id;
- Operation id and generation;
- expected Feature revision for lifecycle-sensitive writes;
- idempotency key for semantic writes;
- authenticated human principal when represented;
- AI client adapter identity;
- trusted service/runtime identity;
- trusted authorization-policy reference/context.

Fields not applicable to a capability may be absent, but a transport must not invent weaker semantics by dropping fields that are required for that capability.

Responses must have deterministic typed success/error envelopes and retain enough correlation data for an adapter to associate the result with the canonical request without parsing human text.

## Structured error model

The canonical error vocabulary must include at least:

```text
INVALID_REQUEST
UNSUPPORTED_API_VERSION
CAPABILITY_UNAVAILABLE
UNAUTHORIZED
POLICY_DENIED
STALE_REVISION
ALREADY_CLAIMED
ALREADY_APPLIED
SUPERSEDED_GENERATION
CANCELLED_OPERATION
EXTERNAL_WAIT
NEEDS_USER
BLOCKED
TRANSIENT_FAILURE
INTERNAL_FAILURE
```

Requirements:

- unknown or malformed request fields fail deterministically as `INVALID_REQUEST` unless a more specific canonical error applies;
- unsupported versions fail as `UNSUPPORTED_API_VERSION` before semantic execution;
- capabilities lacking trusted backing behavior fail as `CAPABILITY_UNAVAILABLE`, not as a fabricated success;
- errors have machine-readable code plus bounded structured details;
- adapters must not be required to parse English message text to determine program behavior;
- error details must not leak credentials, tokens, secret values, or unrestricted internal exception data.

## Identity and authorization boundary

The contract must distinguish at least:

- represented human principal, when present;
- AI client adapter identity;
- trusted service/runtime identity;
- requested capability.

The following invariants are mandatory:

1. AI client identity alone is not human approval or Acceptance evidence.
2. An adapter cannot self-assert a trusted service/runtime identity.
3. Feature-branch or target-controlled input cannot expand trusted authorization policy.
4. Canonical request fields do not grant lifecycle authority merely because they are present.
5. Authorization decisions remain the responsibility of trusted policy/control components introduced or reused by the appropriate workstream.

This Feature may define the typed authorization context required by later workstreams, but it must not implement a weaker ad-hoc authorization model just to make an early adapter succeed.

## Idempotency and lifecycle-sensitive writes

Every semantic write contract must require or deterministically derive an idempotency key.

The API foundation must define the difference between:

- retrying the same logical request with the same idempotency identity;
- submitting a different semantic request;
- replaying a lifecycle-sensitive request after the authoritative Feature revision changed.

Where a write is lifecycle-sensitive, the request must bind to the expected Feature revision and the future implementation must be able to return `STALE_REVISION` rather than silently rebasing the user's intent.

This Feature must not claim to provide the v0.3 external semantic-effect reservation, dispatch claim, launch linearization, Persist linearization, or recovery guarantees. Those remain separate frozen workstreams.

## Capability discovery

`system.capabilities` must provide a deterministic representation of at least:

- supported canonical API versions;
- known canonical capability identifiers;
- availability of each capability in the current trusted runtime/installation context;
- enough structured reason information to distinguish unsupported/unavailable capability from an authorization failure without leaking secrets.

Capability discovery must not falsely advertise a write as available merely because its schema exists.

## Conformance harness

The Feature must provide transport-neutral deterministic conformance assertions that later adapters can execute against a common canonical fixture/surface.

The harness must be capable of proving for two materially independent supported AI client adapters:

- API version negotiation;
- `system.capabilities` behavior;
- `feature.status` request/response semantics;
- `operator.inbox` request/response semantics;
- `operation.status` request/response semantics;
- `decision.list` request/response semantics;
- `notification.list` request/response semantics;
- structured error semantics;
- identity propagation;
- unsupported-capability behavior.

At least one supported adapter must eventually prove release-slice writes end-to-end, but completion of that adapter/dogfood is not required to mark this contract-foundation Feature implemented unless the later approved Design/Plan intentionally includes it.

A second adapter cannot satisfy the release requirement by merely renaming or thinly wrapping the same transport implementation. The harness must make adapter identity/transport boundary visible enough for later release evidence to prove material independence.

## Validation requirements

Deterministic tests for this Feature must cover at minimum:

1. valid `ai-sdlc.operator/v1` request and response schemas;
2. unsupported API version rejection with no semantic write hook invoked;
3. missing/invalid required request fields;
4. unknown capability rejection or canonical unavailable behavior as specified;
5. stable machine-readable structured error codes;
6. authenticated identity propagation without client-controlled trusted-identity escalation;
7. semantic write idempotency-key requirement;
8. lifecycle-sensitive expected-revision requirement where applicable;
9. duplicate equivalent request behavior at the contract/idempotency boundary without inventing backing-store guarantees;
10. `system.capabilities` reporting contract-defined but not-yet-backed capabilities as unavailable;
11. no arbitrary Manifest/Event/Gate/shell/repository-mutation capability in the canonical public operation vocabulary;
12. common conformance fixtures/assertions are reusable by two independent adapter implementations.

## Compatibility requirements

- Existing v0.2 Feature Manifest, Feature Event, Event Inbox, Persist, Gate, Safe Output, Runtime App, gh-aw worker, cross-repository transport, and release-authority semantics remain unchanged unless a separately approved later v0.3 workstream changes them.
- Existing public/private repository trust boundaries must not be weakened.
- Existing clients/workflows that do not use the new Operator API must continue to function.
- The default released version remains v0.2.0 during this Feature; this Feature must not change `VERSION` or create the final `release/v0.3.0.yaml` manifest.
- The frozen `release/v0.3.0-draft.yaml` remains planning/release-tracking state and its unresolved implementation/dogfood blockers must not be marked resolved without evidence from the corresponding workstreams.

## Security requirements

- Canonical schemas accept only bounded documented fields and reject unexpected mutation surfaces.
- Structured errors and capability discovery do not expose credentials, tokens, secret values, or unbounded exception data.
- Client-supplied identity cannot overwrite trusted service/runtime identity.
- Target-controlled/Feature-branch data cannot expand authorization policy through canonical request fields.
- No operation grants arbitrary shell execution, arbitrary repository write, arbitrary Feature Event construction, direct Manifest modification, Gate PASS/WAIVE, merge, or release authority.
- No adapter-specific convenience field may bypass canonical validation.
- Read capabilities must still honor trusted repository/installation authorization; defining a read schema is not permission to read every repository.

## Non-goals

- Do not implement the durable append-only Operation Journal/Store in this Feature unless an independently reviewed Design demonstrates a minimal contract-supporting component is unavoidable.
- Do not implement generation fencing, semantic-effect reservation, dispatch claim, `dispatch.launch.authorized`, external launch receipt recovery, UNKNOWN takeover, Persist linearization, or cancellation race semantics here.
- Do not implement the full Developer → Reviewer → Remediation → Re-review → QA durable vertical loop.
- Do not claim Decision or Notification persistence/outbox completion merely because their API schemas exist.
- Do not complete MCP or a second AI client adapter unless later Design/Plan deliberately includes bounded adapter work; adapter completion can remain separate Features.
- Do not automate Requirement/Design/Acceptance stages as part of this Feature.
- Do not implement Project Takeover/install/upgrade.
- Do not add a custom web dashboard.
- Do not change `VERSION`, create `release/v0.3.0.yaml`, or mark v0.3 release-candidate/release-ready.
- Do not resolve v0.3 dogfood blockers without actual dogfood evidence.

## Acceptance criteria

1. `ai-sdlc.operator/v1` is represented by one transport-independent canonical contract with deterministic request/response validation.
2. All twelve frozen v0.3 capability identifiers are present in the canonical vocabulary with typed request/response shapes and documented availability semantics.
3. `system.capabilities` reports supported API versions and capability availability without advertising unimplemented backing behavior as ready.
4. Unsupported API versions return `UNSUPPORTED_API_VERSION` before any semantic write callback/hook executes.
5. The minimum frozen structured error vocabulary is machine-readable and deterministic; adapters do not need to parse prose to branch on errors.
6. Canonical identity context distinguishes represented human, AI client adapter, trusted runtime/service, and requested capability, and client input cannot self-promote to trusted identity.
7. Semantic write contracts require/derive an idempotency key and lifecycle-sensitive writes bind expected Feature revision where applicable.
8. Canonical public operations contain no arbitrary Feature Manifest patch, arbitrary executable Feature Event, unrestricted Gate mutation, generic shell execution, or unrestricted repository-write escape hatch.
9. Unimplemented Operation/Decision/Notification backing behavior returns honest availability/errors and is not represented as durable success.
10. A reusable transport-neutral conformance harness covers the common two-AI-client subset, version semantics, structured errors, identity propagation, and unsupported-capability behavior.
11. Deterministic negative tests prove malformed requests, identity escalation attempts, missing idempotency/revision bindings, unsupported versions, and unavailable capabilities fail closed.
12. Existing v0.2 protocol/security/control-plane validation relevant to changed files remains green.
13. No change grants the Operator, AI client adapter, or worker direct Feature lifecycle/Gate/merge/release authority.
14. Completion evidence explicitly states which adapter/backend/dogfood release blockers remain unresolved; this Feature alone is never presented as v0.3 release readiness.

## Evidence expected for completion

- canonical schema/interface validation output;
- deterministic conformance-harness tests and reusable adapter fixture evidence;
- negative version/schema/identity/idempotency/revision/error tests;
- regression evidence for existing lifecycle/security/control-plane validators affected by the implementation;
- implementation documentation mapping canonical capabilities to implemented versus unavailable backing behavior;
- final implementation PR required checks on the exact reviewed head;
- explicit unresolved-release-blocker statement covering adapters, durable Operation Store, concurrency/recovery, vertical-loop dogfood, Decision/Notification persistence, and release publication.

## Follow-up boundary

After this Feature passes its own lifecycle, separate v0.3 Features may implement:

1. the first supported AI client adapter such as MCP on top of this canonical contract;
2. a second materially independent supported AI client adapter;
3. the protected append-only Operation Store, projection, claims/reservations and callback correlation;
4. the durable Developer → Reviewer → Remediation → Re-review → QA loop;
5. recovery, Decision/Authorization and Notification Outbox;
6. required conformance and dogfood evidence for release readiness.

Those Features must consume the frozen Release Spec and the approved canonical API contract; they must not infer release readiness from this Feature's completion alone.
