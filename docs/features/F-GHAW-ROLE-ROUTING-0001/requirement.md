# Requirement — Trusted role-aware gh-aw profile routing

Feature: `F-GHAW-ROLE-ROUTING-0001`

Issue: `#200`

Profile: `standard-feature`

## Problem

AI-SDLC now has a trusted gh-aw provider Registry with eight validated profiles, but normal autonomous execution still resolves through the global `copilot` default unless a trusted operator manually selects a profile. Multi-provider support therefore exists as capability but is not yet used as a deterministic execution policy.

The next step is to introduce a trusted routing layer that selects a registered profile from lifecycle role/stage policy and static readiness without allowing target repositories to choose provider/model/credential/worker identity.

## Goal

Add a deterministic, auditable, fail-closed role-aware profile routing policy and resolver. The first version must make current autonomous Developer dispatch capable of preferring Codex and falling back to Copilot when Codex is not statically ready, while defining auditable Reviewer and QA routes for later autonomous-role work without changing their current manual runtime authority.

## Required default policy

The trusted v1 policy must express the following ordered routes:

- `developer` + `implementation`: `codex`, then `copilot`;
- `reviewer` + `code-review`: `claude`, then `copilot`;
- `qa` + `verification`: `gemini`, then `copilot`.

The Reviewer and QA routes are policy data for deterministic validation/audit only in this Feature. Their current dispatch runtime remains manual until a separate autonomous-role Feature is approved.

The global compatibility default remains `copilot`.

## Experimental maturity policy

`deepseek`, `qwen`, `glm`, and `minimax` currently have `experimental` maturity and must not appear in the default v1 production routing candidate sets.

The routing policy format may support a trusted policy-level `allow_experimental` flag for bounded future dogfood, but:

- it defaults to `false`;
- target repositories, Issue Comments, Project Adapter fields, Feature Issues, and worker payloads must not set or override it;
- enabling it does not promote provider maturity and does not imply live entitlement;
- a later dogfood Feature must provide the evidence and trusted policy change before experimental profiles are used by default.

## Static readiness and fallback semantics

Routing v1 uses **pre-dispatch static readiness only**.

For each ordered candidate:

1. the profile must exist in the fully validated trusted Registry;
2. the profile maturity must be permitted by the trusted routing rule;
3. the required credential presence signal must indicate present;
4. the registered compiled worker identity must remain trusted and installed;
5. the first candidate satisfying those conditions is selected.

If a candidate is skipped because its required credential is absent, the resolver may continue to the next policy candidate.

Credential presence means only that the candidate is statically ready to attempt dispatch. It must never be represented as proof of provider subscription, entitlement, quota, billing state, current model availability, endpoint health, rate-limit headroom, or successful inference.

If no candidate is ready, routing fails closed. It must not silently revert to an unvalidated worker or arbitrary profile.

## Explicit non-semantics

This Feature does **not** implement runtime/inference failure retry. Once a selected provider worker is dispatched, errors such as authentication rejection, unavailable model, endpoint failure, quota exhaustion, rate limit, compiler/runtime failure, or model inference failure do not automatically cause the resolver to dispatch the next candidate.

Provider-runtime retry, circuit breaking, health scoring, cost/latency routing, adaptive quality routing, and telemetry-based selection require separate reviewed semantics.

## Trusted routing policy contract

The policy must be durable repository configuration under trusted control-plane ownership and must include enough information to validate:

- policy version;
- unique rule id;
- lifecycle role;
- lifecycle stage;
- ordered unique profile candidates;
- whether experimental maturity is allowed.

Validation must reject at least:

- unknown top-level or rule fields when schema strictness applies;
- duplicate rule ids;
- duplicate role/stage rule matches;
- empty candidate lists;
- duplicate candidates within a rule;
- unknown/unregistered profiles;
- malformed role/stage identifiers;
- unsupported maturity relaxation;
- a default policy containing experimental profiles while `allow_experimental` is false.

The resolver must consume a fully validated Provider Registry and a fully validated routing policy atomically. A malformed policy invalidates routing for all trusted consumers.

## Deterministic routing evidence

Every routing resolution must produce structured audit data containing only non-secret metadata:

- routing policy/version;
- rule id;
- lifecycle role and stage;
- ordered candidates;
- per-candidate readiness result and deterministic skip reason;
- selected profile;
- selected engine/provider/protocol/model;
- selected compiled worker workflow;
- selected profile maturity;
- whether a fallback occurred and why.

Secret values must never be serialized into resolver arguments, JSON output, Task Packages, logs, summaries, artifacts, or Evidence. Readiness input must be boolean/presence-only.

## Dispatch integration

For autonomous gh-aw dispatch, trusted control-plane code must derive the selected profile/worker from lifecycle context and routing policy. The target repository must not supply profile/provider/model/credential/worker/candidate order or experimental opt-in values.

The existing trusted manual profile-dispatch workflow may remain available for diagnostics and operator-controlled testing. Its existence must not create a target-controlled bypass around routing policy.

The Feature must preserve current cross-repository exact worker allowlisting and compiled worker validation.

## Runtime authority boundary

This Feature changes profile selection only. It must not change:

- which lifecycle roles are currently autonomous versus manual;
- Feature Manifest authority;
- Feature Event event-sourcing;
- optimistic revision semantics;
- Requirement/Design/Code/Verification/Release Gate authority;
- independent reviewer/QA separation;
- Safe Output semantics;
- Runtime App trust;
- merge or release authority.

Provider workers remain unable to self-approve Gates or directly edit authoritative Feature state.

## Compatibility requirements

All eight registered profiles must remain valid and strictly compilable:

- `copilot`
- `codex`
- `claude`
- `gemini`
- `deepseek`
- `qwen`
- `glm`
- `minimax`

`copilot` remains `default_engine_profile` and the global compatibility fallback. Existing direct trusted profile resolution remains backward compatible.

## Acceptance criteria

1. A strict trusted routing policy maps lifecycle role/stage to ordered registered profile candidates without provider-name-specific Python control branches.
2. `developer` + `implementation` selects `codex` when Codex's trusted credential-presence signal is true and required trusted worker metadata is valid.
3. The same Developer route deterministically falls back to `copilot` when Codex credential presence is false and Copilot is statically ready.
4. `reviewer` + `code-review` resolves `claude → copilot`, and `qa` + `verification` resolves `gemini → copilot`, but neither role becomes autonomous in this Feature.
5. Experimental profiles are absent from the default candidate sets and cannot resolve unless trusted policy explicitly allows experimental maturity.
6. Target repository Issue Comments, Project Adapter inputs, Feature payloads, and worker payloads cannot select provider/model/profile/credential/worker/candidate order or `allow_experimental`.
7. Missing credentials produce deterministic static skip/non-readiness reasons; credential presence is never represented as live entitlement success.
8. Unknown role/stage, malformed policy, duplicate rules/candidates, unregistered profiles, disallowed maturity, or no ready candidate fails closed.
9. Routing audit output records policy/rule/context/candidates/skip reasons/selected profile-engine-provider-model-worker/maturity/fallback status without secret values.
10. Current autonomous Developer dispatch consumes trusted routing resolution rather than blindly using the global Copilot default when role routing is enabled.
11. Existing manual trusted profile dispatch and direct resolver remain backward compatible, while `copilot` stays the global runtime default/fallback.
12. Feature Manifest/Event/Gate, Safe Output, Runtime App, cross-repository allowlist, independent review/QA, merge, and release authority remain unchanged.
13. Existing eight-profile Registry/render/preflight/effective-model/security/strict-compile regressions remain green.
14. New routing-policy validation, resolution, boundary, fallback, and audit tests pass together with final protocol/security/public-runtime/strict-worker-compile required CI on the final lifecycle candidate.

## Evidence expected for completion

- approved Requirement and Design evidence;
- trusted routing policy and strict validator tests;
- deterministic resolver tests for preferred selection, credential fallback, no-ready failure, experimental exclusion and explicit trusted opt-in;
- command/project/payload boundary tests proving target-controlled selectors remain forbidden;
- dispatch integration evidence proving Developer uses resolved worker identity;
- routing audit examples containing no secret values;
- existing 8-profile compile/Registry/preflight/effective-model regressions;
- final required CI results;
- documentation explaining static-readiness fallback versus runtime-failure retry.

## Non-goals

- No new providers.
- No provider maturity promotion.
- No live-runtime retry/failover/circuit breaker.
- No cost, latency, quality, telemetry, or ML/adaptive routing.
- No autonomous Product, Architect, Reviewer, QA, or Acceptance roles.
- No target-controlled engine/provider/model/profile/credential/worker selection.
- No changes to lifecycle/Gate/merge/release authority.
