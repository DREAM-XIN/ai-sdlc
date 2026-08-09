# Design — Trusted role-aware gh-aw profile routing

Feature: `F-GHAW-ROLE-ROUTING-0001`

Issue: `#200`

## Design intent

Introduce a trusted, deterministic routing layer between lifecycle dispatch context and the existing gh-aw Provider Registry. Routing chooses an ordered profile candidate based on trusted `role` + `stage`, applies static readiness checks, and emits auditable non-secret evidence. It does not change lifecycle authority, autonomous-role eligibility, or runtime failure semantics.

## Current baseline

- `runtimes/gh-aw/runtime.yaml` defines `default_engine_profile: copilot`.
- `runtimes/gh-aw/engine-profiles.yaml` is the authoritative trusted profile Registry.
- `scripts/resolve_gh_aw_engine.py` resolves one explicitly supplied trusted profile to worker identity.
- `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` is an explicit trusted operator workflow with manual `engine_profile` selection.
- `dispatch/gh-aw-developer.yaml` makes Developer autonomous through gh-aw while Product/Architect/Reviewer/QA remain manual.
- `scripts/gh_aw_runtime_preflight.py` validates one trusted profile and accepts only a boolean `--credential-present` signal.

The new routing layer must sit above explicit profile resolution and must not weaken any of those boundaries.

## Architecture overview

```text
trusted lifecycle context
  role + stage
       |
       v
runtimes/gh-aw/profile-routing.yaml
       |
       v
strict routing policy loader/validator
       |
       +----> validated Provider Registry
       |
       +----> trusted readiness map (boolean only)
       |
       v
role-aware route resolver
       |
       +---- selected profile + worker identity
       +---- candidate decisions / skip reasons
       +---- audit JSON
       |
       v
existing trusted gh-aw dispatch gateway
```

## 1. Trusted policy file

Add:

`runtimes/gh-aw/profile-routing.yaml`

Proposed schema:

```yaml
version: 0.1.0
default_profile: copilot
rules:
  - id: implementation-developer
    match:
      role: developer
      stage: implementation
    candidates: [codex, copilot]
    allow_experimental: false
  - id: code-review-reviewer
    match:
      role: reviewer
      stage: code-review
    candidates: [claude, copilot]
    allow_experimental: false
  - id: verification-qa
    match:
      role: qa
      stage: verification
    candidates: [gemini, copilot]
    allow_experimental: false
```

The file is trusted control-plane configuration. Target repositories do not supply or override it.

### Validation rules

The loader must fail closed on:

- duplicate YAML mapping keys;
- unsupported policy version;
- unknown top-level/rule/match fields;
- duplicate rule ids;
- duplicate `(role, stage)` matches;
- empty candidate list;
- duplicate candidate profile ids;
- unregistered profiles;
- invalid role/stage syntax;
- `default_profile` not registered;
- experimental candidate while `allow_experimental: false`;
- any maturity value not recognized by the Provider Registry contract.

The policy is validated atomically against a fully validated Provider Registry.

## 2. Routing library

Add `scripts/gh_aw_profile_routing.py` as the shared trusted library and CLI.

Primary immutable structures:

- `RoutingRule`
- `RoutingPolicy`
- `CandidateReadiness`
- `CandidateDecision`
- `RoutingResolution`

Key APIs:

```python
load_routing_policy(path=DEFAULT_ROUTING_POLICY, registry=None) -> RoutingPolicy
resolve_route(policy, registry, role, stage, readiness) -> RoutingResolution
```

The resolver branches only on policy fields, Registry metadata, maturity, and readiness state. It must not contain provider-name-specific `if profile == ...` control branches.

## 3. Trusted credential-readiness abstraction

Requirement Review MINOR-1 is resolved through a dedicated adapter rather than teaching the resolver about secret names.

### Input contract

The resolver receives a mapping keyed by **profile id**:

```json
{
  "copilot": true,
  "codex": false,
  "claude": true
}
```

It does not receive secret values or raw environment variable values.

### Readiness adapter

Add a helper/CLI, either inside `gh_aw_profile_routing.py` or a narrow companion module, that derives per-profile boolean readiness from trusted presence-only signals generated from Registry credential metadata.

For each profile, readiness is true when any approved credential identity is present:

- primary `credential`;
- any Registry-approved `credential_aliases`.

This preserves native Codex alias behavior generically.

System-provided credentials are handled by trusted workflow wiring, not special-cased in the resolver. For Copilot, the workflow may map availability of the trusted GitHub runtime token to the profile-level boolean readiness signal. The resolver sees only `copilot: true/false`.

Unsupported/ambiguous readiness input fails closed.

### No secret serialization

Secret values must never be passed to Python. Workflow expressions produce booleans first. The resolver receives only booleans.

## 4. Routing algorithm

Given trusted `role`, `stage`, validated policy, Registry, and readiness map:

1. Find exactly one rule matching `(role, stage)`.
2. Iterate candidates in policy order.
3. For each candidate:
   - resolve exact profile from Registry;
   - verify maturity allowed by the rule;
   - verify profile readiness boolean exists;
   - if false, emit `MISSING_CREDENTIAL` candidate decision and continue;
   - validate registered compiled worker through existing compiled-worker/trusted worker boundary;
   - first ready candidate is selected.
4. If no candidate is selected, return fail-closed `NO_READY_CANDIDATE`.
5. Emit deterministic audit result.

No live provider HTTP request occurs during routing.

## 5. Audit/result contract

Successful JSON shape:

```json
{
  "status": "SELECTED",
  "policy_version": "0.1.0",
  "rule_id": "implementation-developer",
  "role": "developer",
  "stage": "implementation",
  "candidates": [
    {"profile":"codex","ready":false,"reason":"MISSING_CREDENTIAL"},
    {"profile":"copilot","ready":true,"reason":"SELECTED"}
  ],
  "selected": {
    "profile":"copilot",
    "engine":"copilot",
    "provider":"copilot",
    "protocol":"native",
    "model":null,
    "worker_workflow":"ai-sdlc-gh-aw-worker.lock.yml",
    "maturity":"reference"
  },
  "fallback": true,
  "fallback_reason": "PREFERRED_CANDIDATE_NOT_READY",
  "entitlement_verified": false
}
```

Failure JSON must be equally deterministic and must not expose secrets.

The exact provider field follows normalized Registry semantics rather than being inferred from profile id in downstream code.

## 6. Dispatch integration

### Normal autonomous lifecycle path

The existing autonomous Developer dispatch path must call the role-aware resolver before choosing `worker_workflow`.

Trusted lifecycle inputs already contain `stage` and `role`; these become the routing context. The workflow constructs profile readiness booleans from trusted credential-presence expressions, invokes routing, then passes only the selected registered `worker_workflow` into the existing dispatch gateway.

Cross-repository target inputs continue to lack engine/profile/model/provider/credential/worker selectors.

### Manual trusted diagnostic path

Requirement Review MINOR-2 is resolved by retaining `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` as a separate **operator-explicit diagnostic/manual selection** workflow.

Rules:

- normal autonomous lifecycle routing never delegates profile choice to this workflow;
- target repository caller inputs cannot invoke arbitrary `engine_profile` selection;
- manual diagnostic invocation remains trusted-control-plane-only;
- its summary should identify selection mode as `manual-trusted-profile`, while role-routing audit identifies `selection_mode: policy`;
- manual selection is not treated as a fallback mechanism and does not mutate routing policy.

## 7. Autonomous-role boundary

`dispatch/gh-aw-developer.yaml` continues to define only Developer as autonomous.

Reviewer and QA routes are validated by policy tests and may be resolved by the routing CLI for audit, but no workflow in this Feature changes their runtime from `chatgpt-web/manual` to gh-aw autonomous.

A later `F-GHAW-AUTONOMOUS-ROLES-0001` may consume these routes after independent lifecycle approval.

## 8. Experimental profile handling

Default policy contains no experimental profiles.

If a future trusted rule contains an experimental candidate, validation requires `allow_experimental: true` on that rule. This is a trusted policy property only.

No Issue Comment, Project Adapter, Feature Manifest, Task Package, or target repository field may override it.

The resolver emits maturity in audit evidence but never upgrades it.

## 9. Backward compatibility

- `runtimes/gh-aw/runtime.yaml` retains `default_engine_profile: copilot`.
- `scripts/resolve_gh_aw_engine.py <profile>` remains supported.
- direct manual profile dispatch remains supported for trusted diagnostics.
- all eight Provider Registry profiles remain unchanged.
- existing generated worker sources/locks remain unchanged unless another independent reason requires regeneration.
- compiled worker exact allowlisting remains Registry-derived.

## 10. Validation strategy

Add deterministic validator coverage for:

### Policy validation

- valid default three-rule policy;
- duplicate rule id;
- duplicate role/stage match;
- duplicate candidate;
- empty candidates;
- unknown profile;
- experimental candidate without opt-in;
- experimental candidate with trusted opt-in;
- malformed/unknown fields;
- invalid default profile.

### Resolution

- Developer: Codex ready => Codex selected;
- Developer: Codex missing + Copilot ready => Copilot selected with fallback evidence;
- Developer: both missing => fail closed;
- Reviewer: Claude preferred;
- QA: Gemini preferred;
- unknown role/stage => fail closed;
- missing readiness key => fail closed rather than assume false/true;
- selected worker must match Registry exact worker identity.

### Credential semantics

- Codex primary present => ready;
- Codex alias present => ready;
- neither present => not ready;
- Copilot system-token boolean wiring => ready without secret value entering Python;
- no emitted JSON contains known fixture secret strings.

### Boundary tests

Extend command/project/dispatch boundary validators to reject target-controlled:

- `engine_profile`
- `provider`
- `model`
- `credential`
- `worker_workflow`
- candidate ordering
- `allow_experimental`
- routing policy path override where target-controlled.

### Compatibility regressions

Run existing:

- Provider Registry validation;
- renderer/surface drift checks;
- runtime preflight tests;
- effective-model audit;
- command/security boundary validation;
- cross-repository runtime allowlist tests;
- 8-profile strict compile CI;
- protocol/public-runtime/Required PR Gate.

## 11. Security properties

- Policy and Registry are trusted default-branch control-plane data.
- Routing never accepts provider HTTP URLs or credentials from the target.
- Routing makes no provider network call.
- Secret values never enter resolver input/output.
- Unknown policy/role/stage/profile/readiness fails closed.
- Selected worker remains exact Registry-registered compiled workflow identity.
- Routing selection grants no lifecycle/Gate/merge/release authority.

## 12. Migration

No migration is required for existing Features.

Before dispatch integration is enabled, behavior remains global Copilot default. After integration, only autonomous Developer dispatch uses policy selection, with Copilot as fallback. Existing manual trusted profile dispatch remains available.

The routing policy is additive and can be rolled back by reverting dispatch integration while leaving Provider Registry entries intact.

## 13. Implementation work units

1. Routing policy schema/file + strict loader.
2. Deterministic resolver + audit model.
3. Registry-derived readiness adapter including alias/system-token wiring.
4. Autonomous Developer dispatch integration.
5. Manual diagnostic selection-mode labeling and boundary hardening.
6. Validator/negative-fixture suite.
7. Documentation and verification evidence.

## Design decisions carried from Requirement Review

- RR-MINOR-1 resolved: readiness is profile-level boolean data derived generically from Registry primary/alias credential identities; system tokens are translated by trusted workflow wiring before Python.
- RR-MINOR-2 resolved: automatic policy routing and manual trusted profile dispatch are separate modes; manual selection is diagnostic/operator-explicit and cannot be supplied by target repositories.
