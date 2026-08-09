# Design — Qwen, GLM, and MiniMax trusted gh-aw provider profiles

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

Approved Requirement: `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/requirement.md`

Requirement Review: `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/requirement-review.md`

Design Review v1: `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/design-review.md`

## 1. Objective and invariants

Add Qwen, GLM, and MiniMax through the provider Registry/certification architecture delivered by `F-GHAW-PROVIDER-REGISTRY-0001`:

```text
trusted Registry metadata
  -> shared Registry validation
  -> deterministic worker materialization
  -> strict gh-aw compile
  -> compiled-lock validation
  -> static preflight
  -> effective-model audit
  -> exact worker allowlisting
```

No provider-specific production adapter or Python branch is introduced. Provider-specific facts exist only as trusted Registry metadata and durable documentation/evidence.

Unchanged invariants:

- Registry: `runtimes/gh-aw/engine-profiles.yaml`.
- Default trusted load validates the entire Registry atomically and requires registered worker sources to exist.
- Generic behavior branches only on capabilities such as protocol/engine/provider_type/wire_api.
- Provider workers inherit the canonical read-only/Safe Output/lifecycle contract.
- Static preflight performs no provider HTTP calls and never proves entitlement.
- Target repositories cannot select provider/model/profile/credential/worker identities.
- Feature Event, Gate, Verification, Acceptance, Runtime App, merge, and release authority are unchanged.
- `copilot` remains the default profile.
- Qwen, GLM, MiniMax, and DeepSeek remain `experimental`.

## 2. Registry entries

### Qwen

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

Observed 2026-08-09 from Alibaba Cloud Model Studio Base URL and text-model documentation. The trusted profile intentionally uses the Beijing shared domain instead of a workspace-specific hostname. `DASHSCOPE_API_KEY` must therefore be valid for the Beijing region. WorkspaceId/base URL is never target-controlled. A future region/domain/model change is a reviewed Registry migration.

### GLM

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

Observed 2026-08-09 from Zhipu BigModel general API and Chat Completions documentation. Coding Plan-specific endpoints are not used as the trusted default profile.

### MiniMax

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

Observed 2026-08-09 from MiniMax OpenAI-compatible API documentation. Other compatibility transports are not fallback routes in this Feature.

## 3. Registry boundary and source-materialization modes

`gh_aw_provider_registry.load_registry()` keeps its secure default:

```text
require_source_files=True
```

All routing, resolver, preflight, compiled-lock audit, cross-repository allowlisting, effective-model audit, and ordinary validation consumers continue using that default. A missing registered worker source therefore fails closed.

### 3.1 Bounded materialization mode

Design Review v1 found a generation deadlock: a newly registered profile points to a generated worker source that does not yet exist, while the renderer currently loads the Registry with source-existence enforcement before it can create that file.

The remediation is narrowly scoped to deterministic worker **write/materialization** mode:

```text
render_gh_aw_workers.py --write/default materialization
  -> load_registry(require_source_files=False)
  -> still validate every Registry field, identity, URL, host, model,
     credential, worker path syntax, uniqueness, protocol and maturity
  -> render the registered worker sources deterministically
```

This mode relaxes only the final filesystem-existence predicate for `worker_source`; it does not relax path canonicalization or any other Registry/security rule.

### 3.2 Check/read mode

Renderer `--check` must use:

```text
load_registry(require_source_files=True)
```

so a missing generated source is rejected.

All non-generator trusted consumers remain unchanged on the secure default. No caller-facing flag is added to resolver/preflight/routing code to opt out of source existence.

### 3.3 Materialization workflow discovery

`materialize-gh-aw-worker-lock.yml` needs to enumerate profiles before the new sources exist. Its pre-render discovery therefore uses the same bounded materialization Registry load (`require_source_files=False`) solely to obtain validated profile/source identities. After rendering, strict compile and later validation use normal source-existence enforcement.

`compile-gh-aw-worker.yml` runs on a PR candidate after generated sources have been committed, so its discovery uses normal `load_registry()` with source existence required.

This resolves `DR-MAJOR-1` without weakening execution trust.

## 4. Deterministic workers and generated workflow surfaces

`render_gh_aw_workers.py` generates three sources from the canonical worker:

- `.github/workflows/ai-sdlc-gh-aw-worker-qwen.md`
- `.github/workflows/ai-sdlc-gh-aw-worker-glm.md`
- `.github/workflows/ai-sdlc-gh-aw-worker-minimax.md`

Only compatible-provider engine frontmatter changes: model, base URL, API-key secret reference, provider type, wire API, and exact network host. Target binding, permissions, Safe Outputs, protected files, lifecycle instructions, Gate prohibitions, and merge/release prohibitions remain inherited from canonical source.

`render_gh_aw_profile_surfaces.py` remains the only generator for dispatch/preflight profile choices and boolean credential-presence plumbing. It adds explicit checks for `DASHSCOPE_API_KEY`, `ZHIPUAI_API_KEY`, and `MINIMAX_API_KEY`, but never exposes secret values.

## 5. Compatibility validators

The existing five-profile compatibility fixtures remain durable **legacy subset assertions**, not a closed provider inventory.

### Engine-profile validator

Change the exact-set assertion to:

```text
legacy five baseline keys ⊆ Registry profile ids
```

Preserve exact legacy engine/credential/alias/worker mappings. Then iterate every Registry profile generically for deterministic source validation.

### Runtime-preflight validator

Likewise preserve the five legacy identity/maturity checks as a subset. For every Registry profile, derive generic expected provider/protocol/maturity from the validated `EngineProfile` and test:

- credential absent -> `MISSING_CREDENTIAL`;
- credential-present boolean -> `READY_FOR_ENTITLEMENT_PROBE`;
- `entitlement_verified` remains false;
- strict compiler metadata remains pinned.

Adding a future provider therefore does not require another generic identity-map branch.

## 6. Registry-derived strict compile and lock materialization

### PR compile workflow

`compile-gh-aw-worker.yml` becomes Registry-derived:

1. read-only discovery job checks out the PR candidate;
2. installs PyYAML;
3. loads normal validated Registry with source files required;
4. outputs ordered profile ids as JSON;
5. compile job uses `fromJSON(...)` as its matrix;
6. selected worker source/lock identities are resolved through `EngineProfile`, not raw YAML;
7. each profile is rendered/checked and strict-compiled with pinned `github/gh-aw v0.83.4`.

Provider-worker path filters become generic `ai-sdlc-gh-aw-worker-*.md` rather than DeepSeek-specific.

### Materialization workflow

`materialize-gh-aw-worker-lock.yml`:

1. on bounded `gh-aw/compile-*` branch, loads Registry in materialization mode for validated identities;
2. runs deterministic renderer write mode;
3. reloads normal Registry/source validation after rendering;
4. strict-compiles every registered profile using the pinned compiler;
5. commits only registered generated worker sources and registered lock files.

Lock files are compiler output and are never hand-authored.

## 7. Effective-model, preflight and allowlisting behavior

`validate_gh_aw_effective_model_metadata.py` requires no provider-specific logic. It must automatically audit DeepSeek, Qwen, GLM, and MiniMax through one compatible-profile loop, matching Registry model to rendered and compiled audit metadata.

Static preflight remains non-networked:

```text
invalid Registry / unknown profile / invalid lock -> fail closed
missing credential -> MISSING_CREDENTIAL
valid static prerequisites + credential presence -> READY_FOR_ENTITLEMENT_PROBE
entitlement_verified -> false
```

The repository currently has no generic trusted live entitlement probe. Expected evidence for each new provider is therefore:

```text
static certification: passed
live entitlement: not established
bounded dogfood: not established
maturity: experimental
```

unless a separate trusted probe produces real evidence before Acceptance.

Cross-repository exact worker allowlisting remains derived from the fully validated Registry. Target Issue Comment grammar remains unchanged and exposes no provider/model/profile/credential/worker selector.

## 8. Fail-closed and materialization tests

Implementation must add deterministic coverage for the Design Review remediation:

### Positive materialization fixture

1. create a temporary valid compatible profile whose registered worker source does not exist;
2. normal `load_registry()` must reject it;
3. bounded materialization load with `require_source_files=False` must validate all metadata and return the profile;
4. renderer write mode must generate the deterministic source;
5. normal `load_registry()` and renderer `--check` must then pass.

### Negative trusted-read fixture

With that valid Registry entry but generated source deleted:

- normal Registry load fails;
- renderer `--check` fails;
- any resolver/preflight/allowlist test using normal Registry load fails closed.

Also preserve existing negative coverage for malformed unrelated entries, duplicate credentials/workers, unsafe URLs/hosts/paths, unknown profiles, and unregistered workers.

## 9. Provider-fact durability, migration and rollback

Official source URLs and observation date (`2026-08-09`) are stored in Requirement/Design/Implementation Evidence. External provider changes never cause implicit runtime substitution.

Future endpoint, region, network host, model, credential identity, provider type, or wire-API changes require a reviewed Registry change and regenerated source/locks/evidence.

Before release, one incompatible new profile can be rolled back by removing its Registry entry and regenerated artifacts, then rerunning the full Registry/static/compile suite. After release, removal/migration should be a separate Feature because consumers may rely on the profile id.

## 10. Test and CI matrix

Final deterministic validation covers:

- shared Registry/fail-closed validation;
- positive and negative missing-source materialization tests;
- renderer `--all --check`;
- generated workflow-surface `--check`;
- legacy five-profile compatibility subset;
- generic static preflight over all Registry profiles;
- effective-model audit over all compatible profiles;
- exact cross-repo worker allowlisting;
- command-boundary/security validation;
- public runtime distribution;
- Required PR Gate;
- Registry-derived strict compile for all registered profiles.

At Feature completion the Registry is expected to contain eight profiles (existing five plus Qwen/GLM/MiniMax), but this count is Verification evidence, not a hard-coded runtime/workflow authority.

## 11. Requirement Review closure

`RQ-MINOR-1` is resolved by the explicit evidence states: static certification, live entitlement, bounded dogfood, maturity. Static readiness is never described as entitlement success.

`RQ-MINOR-2` is resolved by source/date provenance, explicit Qwen Beijing key/region coupling, no target/workspace host override, and reviewed endpoint/model migration semantics.

## 12. Implementation boundaries

Expected changes:

- three Registry entries;
- renderer materialization-mode source-existence handling;
- three generated worker sources and compiler-generated locks;
- generated dispatch/preflight surfaces;
- generic compile/materialization discovery and path filters;
- compatibility validator closed-set removal;
- deterministic materialization tests;
- provider documentation/evidence.

Not expected:

- provider-name-specific branches in Registry/resolver/preflight/audit/allowlist;
- provider HTTP calls in control plane/static preflight;
- Commander/Persist/Gate authority changes;
- default-profile changes;
- maturity promotion;
- Kimi or another provider.

## 13. Conclusion

The Feature remains a Registry extension, not a new adapter architecture. The only shared behavior change is a narrowly bounded generator materialization mode that can create a newly registered worker source before it exists; every normal trusted consumer and renderer check mode continues to require that source and fail closed when it is absent. This removes the Design Review bootstrap deadlock while preserving the Provider Registry security boundary.
