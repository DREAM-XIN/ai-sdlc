# Design — Qwen, GLM, and MiniMax trusted gh-aw provider profiles

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

Approved Requirement: `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/requirement.md`

Requirement Review: `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/requirement-review.md`

## 1. Design goals

This design adds Qwen, GLM, and MiniMax to the trusted gh-aw execution inventory while preserving the architecture established by `F-GHAW-PROVIDER-REGISTRY-0001`:

```text
trusted Registry metadata
  -> atomic Registry validation
  -> deterministic worker generation
  -> strict compiler materialization
  -> compiled-lock identity validation
  -> static preflight
  -> effective-model audit
  -> exact worker allowlisting
```

The design intentionally does **not** create three new provider adapters or provider-specific control-plane branches. Provider-specific facts live only in trusted Registry entries and documentation/evidence.

## 2. Architectural invariants

The following invariants are unchanged and are treated as non-negotiable design constraints:

1. `runtimes/gh-aw/engine-profiles.yaml` remains the trusted provider/profile inventory.
2. `scripts/gh_aw_provider_registry.py` remains the single atomic fail-closed validation boundary before a profile or worker identity can be trusted.
3. Generic behavior branches on capabilities such as `protocol`, `engine`, `provider_type`, and `wire_api`, never on `provider == "qwen"`, `provider == "glm"`, or `provider == "minimax"`.
4. Provider workers inherit the canonical read-only/Safe Output/lifecycle authority contract from `.github/workflows/ai-sdlc-gh-aw-worker.md`.
5. Static preflight never performs provider HTTP calls and never claims entitlement.
6. Target repositories cannot supply provider/model/profile/credential/worker identities.
7. Feature Event, optimistic revision, Gate, Verification, Acceptance, merge, and release authority are unchanged.
8. `copilot` remains the default profile.
9. Qwen, GLM, MiniMax, and DeepSeek remain `experimental` after this Feature.

## 3. Provider Registry additions

Exactly three profiles are added.

### 3.1 Qwen

```yaml
qwen:
  engine: copilot
  provider: qwen
  protocol: openai-compatible
  provider_type: openai
  wire_api: completions
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  network_host: dashscope.aliyuncs.com
  model: qwen3.7-plus
  worker_source: .github/workflows/ai-sdlc-gh-aw-worker-qwen.md
  worker_workflow: ai-sdlc-gh-aw-worker-qwen.lock.yml
  credential: DASHSCOPE_API_KEY
  maturity: experimental
```

Provider-fact provenance, observed 2026-08-09:

- Alibaba Cloud Model Studio Base URL documentation: `https://help.aliyun.com/en/model-studio/base-url`
- Alibaba Cloud text-generation/model documentation: `https://help.aliyun.com/en/model-studio/text-generation-model/`

The Beijing shared domain is deliberately selected over a workspace-dedicated hostname. A dedicated hostname contains a workspace identity and therefore cannot be represented as one stable globally trusted Registry host without introducing per-user templating. Target repositories are not allowed to override this host.

Qwen API keys are region-coupled. `DASHSCOPE_API_KEY` used with this profile must therefore be valid for the China (Beijing) service region. A future region or dedicated-domain migration is a reviewed Registry change, not runtime fallback behavior.

`qwen3.7-plus` is a rolling provider model alias. This Feature pins the alias as observed on 2026-08-09; future replacement or version pinning requires a reviewed Registry change and regenerated lock/evidence.

### 3.2 GLM

```yaml
glm:
  engine: copilot
  provider: glm
  protocol: openai-compatible
  provider_type: openai
  wire_api: completions
  base_url: https://open.bigmodel.cn/api/paas/v4
  network_host: open.bigmodel.cn
  model: glm-5.2
  worker_source: .github/workflows/ai-sdlc-gh-aw-worker-glm.md
  worker_workflow: ai-sdlc-gh-aw-worker-glm.lock.yml
  credential: ZHIPUAI_API_KEY
  maturity: experimental
```

Provider-fact provenance, observed 2026-08-09:

- Zhipu BigModel API introduction: `https://docs.bigmodel.cn/cn/api/introduction`
- Zhipu BigModel Chat Completions reference: `https://docs.bigmodel.cn/api-reference/模型-api/对话补全`

The general API endpoint is selected. Coding Plan-specific endpoints/entitlements are intentionally not encoded in the trusted default profile.

### 3.3 MiniMax

```yaml
minimax:
  engine: copilot
  provider: minimax
  protocol: openai-compatible
  provider_type: openai
  wire_api: completions
  base_url: https://api.minimaxi.com/v1
  network_host: api.minimaxi.com
  model: MiniMax-M2.7
  worker_source: .github/workflows/ai-sdlc-gh-aw-worker-minimax.md
  worker_workflow: ai-sdlc-gh-aw-worker-minimax.lock.yml
  credential: MINIMAX_API_KEY
  maturity: experimental
```

Provider-fact provenance, observed 2026-08-09:

- MiniMax OpenAI-compatible API reference: `https://platform.minimaxi.com/docs/api-reference/text-openai-api`

This Feature intentionally certifies MiniMax through the generic OpenAI-compatible path. Other MiniMax compatibility surfaces do not become fallback routes.

## 4. Registry validation boundary

No new provider-specific validation code is introduced in `scripts/gh_aw_provider_registry.py`.

The existing validation already enforces the required contract for all three entries:

- normalized profile/provider ids;
- `engine: copilot` for compatible profiles;
- `provider_type: openai`;
- supported wire API;
- model syntax;
- HTTPS-only base URL;
- no URL credentials/query/fragment;
- exact `network_host`/hostname agreement;
- canonical repository-relative worker source path;
- registered worker source existence;
- globally unique worker source/workflow and credential names;
- `experimental|reference` maturity;
- atomic full-Registry failure semantics.

The new profiles therefore exercise the existing boundary rather than expanding it.

## 5. Deterministic worker generation

`scripts/render_gh_aw_workers.py` already renders every OpenAI-compatible profile from Registry metadata into the canonical worker contract.

For each new profile it generates only the engine/provider frontmatter delta:

```text
engine.id = copilot
engine.model = Registry model
COPILOT_PROVIDER_BASE_URL = Registry base_url
COPILOT_MODEL = Registry model
COPILOT_PROVIDER_API_KEY = secrets.<Registry credential>
COPILOT_PROVIDER_TYPE = openai
COPILOT_PROVIDER_WIRE_API = completions
network.allowed += exact Registry network_host
```

Everything else is inherited byte-for-byte from the canonical worker, including:

- target repository/ref binding;
- read-only permission posture;
- Runtime App/Safe Output configuration;
- protected files;
- Feature Manifest prohibition;
- Gate prohibition;
- merge/release prohibition;
- Worker Result collection semantics.

Committed generated source files are:

- `.github/workflows/ai-sdlc-gh-aw-worker-qwen.md`
- `.github/workflows/ai-sdlc-gh-aw-worker-glm.md`
- `.github/workflows/ai-sdlc-gh-aw-worker-minimax.md`

`python scripts/render_gh_aw_workers.py --all --check` remains the drift proof.

## 6. Generated workflow profile and credential surfaces

`scripts/render_gh_aw_profile_surfaces.py` remains the only generator for:

- preflight `workflow_dispatch` profile choices;
- dispatch profile choices;
- boolean secret-presence environment entries;
- profile-to-presence shell case mapping.

The three new credentials become explicit generated boolean expressions:

```text
secrets.DASHSCOPE_API_KEY != ''
secrets.ZHIPUAI_API_KEY != ''
secrets.MINIMAX_API_KEY != ''
```

Only booleans are passed to Python preflight; secret values never leave the trusted GitHub Actions secret substitution boundary.

No provider-specific handwritten shell branch is added.

## 7. Backward-compatibility validator generalization

Two validators currently contain a deliberate five-profile test-only compatibility snapshot. The snapshot is useful, but its current exact-set assertion makes it functionally closed to real Registry extension.

### 7.1 Engine profile compatibility baseline

Current behavior in `scripts/validate_gh_aw_engine_profiles.py`:

```text
set(registry profiles) == set(existing five-profile baseline)
```

New behavior:

```text
existing five-profile baseline keys must be a subset of Registry profile ids
```

For those five legacy profiles, exact engine/credential/alias/worker mappings remain asserted unchanged.

All Registry profiles — including Qwen, GLM, and MiniMax — continue through generic renderer/source validation.

This keeps the old snapshot as a backward-compatibility assertion rather than runtime or extension authority.

### 7.2 Runtime-preflight compatibility baseline

Current behavior in `scripts/validate_gh_aw_runtime_preflight.py` also requires the Registry profile set to equal the five-profile snapshot and uses the snapshot to derive expected provider/protocol/maturity for every profile.

New behavior:

1. Existing five baseline profiles must remain present with their old compatibility identity/maturity.
2. Every Registry profile is exercised generically for missing/present credential semantics.
3. Generic expected provider/protocol/maturity comes from the validated `EngineProfile` itself.
4. The old five-profile snapshot is checked only for backward compatibility.

Result: adding a future valid Registry provider does not require adding it to a generic preflight identity map.

## 8. Effective-model audit

`scripts/validate_gh_aw_effective_model_metadata.py` requires no provider-specific architectural change. It already iterates all `openai-compatible` Registry profiles.

After this Feature it must automatically audit:

- `deepseek`
- `qwen`
- `glm`
- `minimax`

For each profile it verifies one model identity across:

- Registry model;
- rendered `engine.model`;
- rendered `COPILOT_MODEL`;
- compiled `GH_AW_INFO_MODEL`;
- compiled `GH_AW_ENGINE_MODEL`;
- compiled `agent_model` metadata.

The same code path must cover all four.

## 9. Static runtime preflight

`scripts/gh_aw_runtime_preflight.py` remains static and non-networked.

For every profile:

```text
invalid Registry / unknown profile / invalid lock -> fail closed
missing credential -> MISSING_CREDENTIAL
valid lock + credential-present boolean -> READY_FOR_ENTITLEMENT_PROBE
entitlement_verified -> always false
```

No live HTTP probe is added to this script or workflow.

This repository currently has no generic trusted live entitlement-probe implementation. Therefore the expected Feature evidence for the three new providers is:

```text
static certification: passed (if all deterministic checks pass)
live entitlement: not established
bounded dogfood: not established
maturity: experimental
```

If a trusted live probe appears independently before Acceptance, its evidence may be recorded, but this Feature does not depend on or create such a mechanism.

This explicitly resolves `RQ-MINOR-1`.

## 10. Strict compile workflow becomes Registry-derived

The current PR compile workflow hard-codes a five-profile matrix and has a DeepSeek-specific path filter. That is test orchestration, not runtime authority, but leaving it closed would make every future provider require another hard-coded compile edit.

### 10.1 Discovery job

`.github/workflows/compile-gh-aw-worker.yml` will gain a small read-only discovery job:

1. checkout candidate PR head;
2. set up Python;
3. install PyYAML;
4. load the full Registry through `scripts/gh_aw_provider_registry.py`;
5. emit the validated ordered profile id list as JSON.

The compile job uses:

```text
strategy.matrix.profile = fromJSON(needs.discover.outputs.profiles)
```

The matrix therefore contains all registered profiles without a provider-name list in workflow YAML.

### 10.2 Identity resolution

The compile job will stop parsing raw `engine-profiles.yaml` with `yaml.safe_load` to obtain source/lock identities. It will resolve the selected validated `EngineProfile` through the shared Registry helper.

This reinforces the trusted Registry boundary rather than creating a second raw-YAML identity consumer.

### 10.3 Path filters

Provider-worker source path triggers become generic:

```text
.github/workflows/ai-sdlc-gh-aw-worker.md
.github/workflows/ai-sdlc-gh-aw-worker-*.md
```

plus Registry/renderer/compile/materialization dependencies.

No DeepSeek-only trigger remains.

## 11. Lock materialization workflow

`.github/workflows/materialize-gh-aw-worker-lock.yml` already iterates every Registry profile for render/compile, but currently uses raw YAML snippets for identity enumeration and has a DeepSeek-specific path filter.

The workflow will be aligned with the same boundary:

- generic worker-source path filter;
- use validated Registry helper for worker source and lock enumeration;
- render all profiles;
- strict-compile every profile using pinned `github/gh-aw v0.83.4`;
- commit only registered generated worker sources and registered lock files.

Generated `.lock.yml` files are compiler output and must never be hand-authored.

Materialization still occurs on bounded `gh-aw/compile-*` branches. The generated artifacts are then transferred to the Feature branch as compiler-produced content before final CI/Review.

## 12. Compiled-lock validation

`scripts/gh_aw_compiled_worker.py` remains generic and unchanged in authority. For each Registry profile it validates:

- expected lock path;
- metadata schema version;
- strict flag;
- pinned compiler version;
- compiled agent id vs Registry engine;
- optional engine version when configured;
- compiled agent model vs Registry model when configured.

All three new compatible profiles therefore require their compiled metadata to pin the selected model.

## 13. Cross-repository worker allowlisting

`scripts/gh_aw_cross_repo_runtime.py` already obtains exact trusted worker workflows from the full validated Registry.

Adding the three Registry entries automatically adds exactly these allowed workflow identities:

- `ai-sdlc-gh-aw-worker-qwen.lock.yml`
- `ai-sdlc-gh-aw-worker-glm.lock.yml`
- `ai-sdlc-gh-aw-worker-minimax.lock.yml`

No wildcard worker execution and no target-controlled worker selector is introduced.

## 14. Command boundary

No command grammar changes are needed.

`/ai-sdlc dispatch-gh-aw` remains bounded to trusted target repository/ref/Manifest context. Tests continue rejecting target-controlled:

- provider;
- model;
- engine profile;
- credential;
- worker workflow/policy selectors.

Provider selection remains a control-repository workflow decision.

## 15. Provider-fact durability and migration policy

Provider external contracts are mutable independently of AI-SDLC, so the Feature records a durable observation date (`2026-08-09`) and official source URLs.

A future change to any of these requires a reviewed Registry migration:

- endpoint/base URL;
- region;
- network host;
- model alias/version;
- credential identity;
- wire API;
- provider type.

There is no runtime fallback to a different region, host, model, or vendor gateway.

For Qwen specifically:

- the trusted profile is Beijing shared-domain only;
- the key must match that region;
- workspace-dedicated domains are not dynamically substituted;
- target repositories cannot inject WorkspaceId/base URL.

This explicitly resolves `RQ-MINOR-2`.

## 16. Rollback

Rollback is a reviewed Registry/artifact change, not runtime failover.

If one new profile proves incompatible before release:

1. remove only that profile Registry entry;
2. regenerate profile/credential workflow surfaces;
3. remove its generated worker source and compiler-generated lock;
4. rerun all Registry/static/compile/compatibility checks.

Atomic Registry validation means a malformed profile cannot be left registered while other profiles continue silently.

After a profile is released, removal/endpoint/model migration should be handled as a separate reviewed Feature because consumers may rely on its trusted profile id.

## 17. Test strategy

### 17.1 Registry and fail-closed checks

Existing Registry validation remains mandatory. Add/adjust deterministic checks only where needed to prove:

- all three new entries load through the shared boundary;
- source/worker/credential identities are globally unique;
- malformed unrelated entries still invalidate all lookups;
- unknown profiles/workers remain rejected.

No test should encode provider-name-specific production branching.

### 17.2 Legacy compatibility

The original five profile mappings remain exact backward-compatibility fixtures as a subset of the Registry.

Assertions:

- Copilot/Codex/Claude/Gemini mappings unchanged;
- DeepSeek mapping and `experimental` maturity unchanged;
- default remains Copilot.

### 17.3 Render drift

Run:

```text
python scripts/render_gh_aw_workers.py --all --check
python scripts/render_gh_aw_profile_surfaces.py --check
```

### 17.4 Static preflight

For every Registry profile:

- credential absent -> `MISSING_CREDENTIAL`;
- credential present boolean -> `READY_FOR_ENTITLEMENT_PROBE`;
- entitlement remains false;
- strict compiler metadata verified.

For legacy five profiles, old identity/maturity fixtures remain unchanged.

### 17.5 Effective model

One generic test must enumerate all compatible profiles and report DeepSeek + Qwen + GLM + MiniMax without provider-specific branches.

### 17.6 Compile matrix

Final PR compile workflow must discover all Registry profile ids and produce successful strict compile jobs for eight profiles:

- copilot
- codex
- claude
- gemini
- deepseek
- qwen
- glm
- minimax

The fact that eight are expected is an Acceptance/Verification observation, not a hard-coded runtime or workflow authority list.

### 17.7 Security/regression

Required final checks include:

- repository protocol validation;
- GitHub workflow/action security;
- provider Registry/extension tests;
- command boundary;
- cross-repository runtime checks;
- public runtime distribution;
- required PR gate;
- strict compile matrix.

## 18. Implementation boundaries

Expected production/configuration changes:

- `runtimes/gh-aw/engine-profiles.yaml`
- generated worker sources for Qwen/GLM/MiniMax
- compiler-generated lock files for Qwen/GLM/MiniMax
- generated preflight/dispatch profile surfaces
- compile/materialization workflow generic discovery/path filters
- compatibility-test generalization in engine-profile/runtime-preflight validators
- provider integration documentation/evidence

Not expected:

- provider-specific branch changes in Registry/renderer/preflight/effective-model/cross-repo runtime;
- direct provider HTTP client code;
- Commander/Persist/Gate changes;
- Runtime App/Safe Output authority changes;
- default-profile changes;
- maturity promotion.

## 19. Risks and mitigations

### Provider API/model contract changes

Risk: external provider aliases/endpoints can change.

Mitigation: source/date evidence, explicit reviewed migration policy, no runtime fallback.

### Qwen regional mismatch

Risk: a non-Beijing DashScope key may fail against the selected endpoint.

Mitigation: document key/region coupling; preflight only proves credential presence; live entitlement is not claimed.

### Strict compiler/provider compatibility

Risk: a syntactically OpenAI-compatible profile may still fail gh-aw strict compile or runtime semantics.

Mitigation: all profiles must strict-compile on the pinned compiler; failure blocks implementation completion. Runtime/live behavior remains experimental until separate evidence.

### CI matrix growth

Risk: adding three profiles increases compile time.

Mitigation: fail-fast remains disabled to collect independent evidence; matrix discovery is deterministic and bounded by trusted Registry size.

### False certification language

Risk: users interpret static compatibility as live provider support.

Mitigation: standardized evidence status explicitly distinguishes static certification, live entitlement, bounded dogfood, and maturity.

## 20. Requirement traceability

| Requirement/AC | Design mechanism |
| --- | --- |
| AC1 Registry entries | Section 3 |
| AC2 no provider-specific control branches | Sections 2, 4, 7–14 |
| AC3 deterministic workers | Section 5 |
| AC4 generated workflow surfaces | Section 6 |
| AC5 strict compile/model metadata | Sections 10–12 |
| AC6 effective-model audit | Section 8 |
| AC7 preflight semantics | Section 9 |
| AC8 fail closed | Sections 4, 17.1 |
| AC9 existing compatibility/default | Sections 7, 17.2 |
| AC10 command boundary | Section 14 |
| AC11 provider facts/static-vs-live docs | Sections 3, 9, 15 |
| AC12 required final CI | Section 17.7 |
| AC13 experimental maturity/authority | Sections 2, 9, 18 |
| RQ-MINOR-1 | Section 9 + evidence terminology |
| RQ-MINOR-2 | Sections 3.1 and 15 |

## 21. Design conclusion

The new providers can be added without extending provider-specific production control logic. The only reusable framework changes are to remove closed-set assumptions from test orchestration/compatibility validation and make strict compile discovery consume the validated Registry. Static integration can therefore be independently certified while live entitlement/dogfood remains explicitly unproven and all four direct compatible providers remain `experimental`.
