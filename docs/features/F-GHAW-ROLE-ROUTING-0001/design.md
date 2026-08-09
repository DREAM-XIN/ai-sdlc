# Design — Trusted role-aware gh-aw profile routing

Feature: `F-GHAW-ROLE-ROUTING-0001`

Issue: `#200`

Revision intent: Design v2 after DR-MAJOR-1 remediation.

## Architecture

```text
trusted lifecycle context (role, stage)
        |
        v
runtimes/gh-aw/profile-routing.yaml
        |
        +---- validated Provider Registry
        |
        +---- generated profile readiness booleans
        |
        v
scripts/gh_aw_profile_routing.py
        |
        +---- selected exact registered worker
        +---- deterministic non-secret audit
        |
        v
existing gh-aw dispatch gateway
```

Routing changes profile selection only. It does not change lifecycle authority, autonomous-role eligibility, Safe Output, Feature Event/Gate semantics, merge authority, or release authority.

## 1. Trusted routing policy

Add `runtimes/gh-aw/profile-routing.yaml`:

```yaml
version: 0.1.0
default_profile: copilot
rules:
  - id: implementation-developer
    match: {role: developer, stage: implementation}
    candidates: [codex, copilot]
    allow_experimental: false
  - id: code-review-reviewer
    match: {role: reviewer, stage: code-review}
    candidates: [claude, copilot]
    allow_experimental: false
  - id: verification-qa
    match: {role: qa, stage: verification}
    candidates: [gemini, copilot]
    allow_experimental: false
```

Strict validation rejects duplicate YAML keys, unknown fields, unsupported version, duplicate rule ids, duplicate role/stage matches, empty/duplicate candidates, unknown profiles, invalid default profile, malformed role/stage ids, and experimental candidates when `allow_experimental` is false.

Policy is trusted control-plane data. Target repositories cannot supply the path, candidates, candidate order, or experimental opt-in.

## 2. Credential-source metadata — DR-MAJOR-1 remediation

The Provider Registry must describe not only credential identity but also **credential source semantics**.

Extend the validated `EngineProfile` contract with:

```yaml
credential: OPENAI_API_KEY
credential_source: secret
```

Allowed v1 values are exactly:

- `secret`
- `github-token`

All existing profiles receive an explicit source in the Registry:

- Copilot: `credential_source: github-token`
- Codex, Claude, Gemini, DeepSeek, Qwen, GLM, MiniMax: `credential_source: secret`

This is capability metadata, not a provider-name routing branch.

### Validation rules

- `credential_source` is required for every profile after migration.
- unknown source values fail closed;
- `secret` permits primary `credential` plus Registry-approved `credential_aliases`;
- `github-token` requires a single canonical credential identity and forbids `credential_aliases` in v1;
- source metadata participates in immutable Registry validation and synthetic extension fixtures;
- source metadata does not contain any secret value.

This eliminates the need for `if profile == "copilot"` or equivalent logic in readiness generation.

## 3. Generated readiness expressions

Extend `scripts/render_gh_aw_profile_surfaces.py` or add a narrow companion renderer to generate **presence-only** workflow expressions from `credential_source` capability.

Pseudo-rendering by capability:

```text
credential_source == secret
    -> primary/aliases use `${{ secrets.NAME != '' }}`

credential_source == github-token
    -> canonical identity uses `${{ github.token != '' }}`
```

The implementation branch is on bounded source capability (`secret` vs `github-token`), never profile/provider identity.

Generated workflow wiring reduces all allowed identities for a profile to one boolean readiness value. Secret values themselves are never passed to Python.

Positive/negative fixtures must prove:

- secret primary present => ready;
- secret alias present => ready;
- no secret identity present => not ready;
- github-token present => ready;
- unsupported source => Registry invalid;
- github-token + alias => Registry invalid;
- generated surfaces contain source-appropriate expressions without literal secret values.

## 4. Routing library

Add `scripts/gh_aw_profile_routing.py`.

Immutable structures:

- `RoutingRule`
- `RoutingPolicy`
- `CandidateDecision`
- `RoutingResolution`

Key functions:

```python
load_routing_policy(path, registry) -> RoutingPolicy
resolve_route(policy, registry, role, stage, readiness_by_profile) -> RoutingResolution
```

The resolver receives only profile-level booleans, never secret names/values.

Algorithm:

1. require exactly one validated `(role, stage)` rule;
2. iterate candidates in configured order;
3. require exact Registry profile identity;
4. enforce rule maturity permission;
5. require an explicit boolean readiness entry for that profile;
6. false => record `MISSING_CREDENTIAL` and continue;
7. true => validate exact registered compiled worker identity and select first ready candidate;
8. none ready => fail closed `NO_READY_CANDIDATE`.

No provider HTTP call occurs.

## 5. Routing audit contract

Every resolution emits deterministic non-secret JSON containing:

- `selection_mode: policy`;
- policy version;
- rule id;
- role/stage;
- ordered candidates;
- per-candidate readiness and skip reason;
- selected profile/engine/provider/protocol/model/worker/maturity;
- fallback boolean and reason;
- `entitlement_verified: false`.

No secret values or raw environment values are serialized.

## 6. Dispatch integration

Normal autonomous lifecycle dispatch derives profile selection from trusted role/stage policy before passing a `worker_workflow` to the existing dispatch gateway.

Current runtime authority remains unchanged:

- Developer/implementation is the only gh-aw autonomous lifecycle route currently enabled by `dispatch/gh-aw-developer.yaml`;
- Reviewer/code-review and QA/verification rules are resolvable/testable policy data only;
- Product, Architect, Orchestrator, Reviewer, QA and Acceptance do not become autonomous in this Feature.

For Developer:

- Codex ready => select Codex;
- Codex not ready + Copilot ready => select Copilot and record fallback;
- neither ready => fail closed.

## 7. Manual trusted profile dispatch boundary

`.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` remains a trusted operator diagnostic/manual workflow.

It is **not** called by normal policy routing and is not a policy fallback mechanism.

Target repository commands/project inputs cannot set `engine_profile`, provider/model/credential/worker, candidate order, policy path, or `allow_experimental`.

Manual workflow summaries identify `selection_mode: manual-trusted-profile`; automatic routing evidence uses `selection_mode: policy`.

This closes Requirement Review MINOR-2.

## 8. Experimental maturity

Default policy contains only reference profiles plus Copilot fallback.

A future trusted rule may list an experimental profile only with `allow_experimental: true`. This field is trusted policy data and cannot be target-controlled. It does not promote maturity or imply live entitlement.

## 9. Backward compatibility

- `default_engine_profile: copilot` remains in `runtime.yaml` as global compatibility default/fallback.
- existing `resolve_gh_aw_engine.py <profile>` remains valid;
- manual trusted profile dispatch remains valid;
- eight profile ids, models, endpoints and worker identities remain unchanged;
- only credential-source capability metadata is added to Registry entries;
- strict worker compilation and exact allowlisting remain Registry-derived.

## 10. Validation strategy

### Registry credential-source tests

- all eight profiles have valid source metadata;
- unknown source rejected;
- github-token alias rejected;
- synthetic secret-backed and github-token-backed fixtures validated;
- source selection has no profile-name AST branch.

### Policy tests

- valid default policy;
- duplicate ids/matches/candidates rejected;
- empty candidate list rejected;
- unknown profile rejected;
- experimental candidate rejected without opt-in and accepted with trusted opt-in;
- unknown fields/version/default profile rejected.

### Resolution tests

- Developer Codex preferred;
- Developer Copilot fallback;
- no-ready failure;
- Reviewer Claude preferred;
- QA Gemini preferred;
- unknown role/stage failure;
- missing readiness key failure;
- selected worker equals exact Registry workflow.

### Readiness tests

- Codex primary and alias semantics;
- Copilot github-token semantics;
- secret values absent from resolver args/output;
- generated readiness surfaces drift check.

### Boundary tests

Target command/project/dispatch inputs continue rejecting profile/provider/model/credential/worker/policy/candidate/experimental selectors.

### Regression envelope

All existing Registry/render/preflight/effective-model/security/cross-repo validations and eight-profile strict compile CI must remain green, plus protocol/public-runtime/Required PR Gate.

## 11. Security and failure properties

- Registry + routing policy are atomically trusted inputs.
- unsupported credential source fails closed.
- no target-controlled provider identity enters routing.
- no secret value enters Python.
- no provider network probe occurs during selection.
- missing readiness never implies ready.
- selected worker remains exact registered compiled workflow.
- routing grants no lifecycle/Gate/merge/release authority.

## 12. Migration and rollback

Migration is additive:

1. add explicit `credential_source` to all Registry profiles;
2. extend Registry validator and generated surface renderer;
3. add routing policy/resolver;
4. integrate only autonomous Developer dispatch.

Rollback can remove Developer policy integration and retain the global Copilot default. No Feature Manifest migration is required.

## 13. Implementation work units

1. Registry credential-source contract + migration + fixtures.
2. Routing policy + strict validator.
3. Metadata-driven readiness renderer/adapter.
4. Routing resolver + audit output.
5. Developer dispatch integration.
6. Manual diagnostic/boundary hardening.
7. Regression/CI/documentation evidence.

## Review note disposition

- RR-MINOR-1: resolved by explicit validated `credential_source` metadata and profile-level boolean readiness generation.
- RR-MINOR-2: resolved by separate `policy` and `manual-trusted-profile` selection modes.
- DR-MAJOR-1: resolved; both `secret` and `github-token` are bounded capability values, aliases are source-constrained, and generated readiness branches on source capability rather than profile identity.
