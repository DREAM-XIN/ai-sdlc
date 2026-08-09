# OpenAI-compatible providers

AI-SDLC keeps provider choice in the execution plane. The lifecycle protocol, Feature Manifest, revision rules, Gates, Evidence, Safe Outputs, Runtime App trust, persistence, merge authority, and release authority remain provider-neutral.

## Architectural boundary

AI providers are optional execution backends, not AI-SDLC control-plane dependencies.

```text
AI-SDLC control plane
  -> runtime adapter
  -> trusted provider profile
  -> optional provider
  -> optional model
```

`gh-aw` is a runtime. A provider/profile only supplies bounded inference configuration to that runtime. Adding a provider must never add provider-specific authority to Commander, Feature state, Gate evaluation, Safe Outputs, Feature Event persistence, or cross-repository target selection.

The trusted Registry is:

```text
runtimes/gh-aw/engine-profiles.yaml
```

Production consumers do not treat raw YAML as routing identity. The entire Registry is loaded and validated through:

```text
scripts/gh_aw_provider_registry.py
```

Only immutable validated profile objects may become engine, provider, model, credential, worker-source, or compiled-worker identities. Any malformed registered entry invalidates the Registry for every trusted consumer, even if another profile was requested.

## OpenAI-compatible profile contract

A compatible provider profile declares trusted metadata such as:

```yaml
engine: copilot
protocol: openai-compatible
provider: <provider-id>
provider_type: openai
wire_api: completions | responses
base_url: https://provider.example/api
network_host: provider.example
model: <model-id>
worker_source: .github/workflows/<worker>.md
worker_workflow: <worker>.lock.yml
credential: <REPOSITORY_SECRET_NAME>
maturity: experimental | reference
```

The Registry validator enforces the common and protocol-specific contract, including narrow identifier syntax, HTTPS-only endpoints, no embedded URL credentials, no query or fragment components, exact `network_host`/hostname agreement, repository-relative worker paths, bounded credential names, and global uniqueness of worker/credential identities.

Generic compatible-provider behavior branches on capabilities such as `protocol`, `engine`, `provider_type`, and `wire_api`. It must not branch on provider/profile names such as `deepseek`, `qwen`, `glm`, or `minimax`.

## Registered compatible provider cohort

The current direct OpenAI-compatible cohort is intentionally conservative and remains `experimental`.

| Profile | Provider | Base URL | Model | Repository secret | Maturity |
| --- | --- | --- | --- | --- | --- |
| `deepseek` | DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | `DEEPSEEK_API_KEY` | `experimental` |
| `qwen` | Alibaba Cloud Model Studio / Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.7-plus` | `DASHSCOPE_API_KEY` | `experimental` |
| `glm` | Zhipu BigModel / GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.2` | `ZHIPUAI_API_KEY` | `experimental` |
| `minimax` | MiniMax | `https://api.minimaxi.com/v1` | `MiniMax-M2.7` | `MINIMAX_API_KEY` | `experimental` |

Provider facts for Qwen, GLM, and MiniMax were reviewed on `2026-08-09` for `F-GHAW-DOMESTIC-PROVIDERS-0001`. Durable provider-specific evidence is in:

```text
docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/provider-certification.md
```

### Qwen operational constraint

The trusted Qwen profile uses the Beijing shared DashScope endpoint. `DASHSCOPE_API_KEY` must therefore be valid for that service region. Workspace-dedicated hosts, alternate regions, and target-controlled base URL/WorkspaceId overrides are not runtime fallbacks; any such migration requires a reviewed Registry change.

### GLM endpoint choice

The trusted GLM profile uses the general BigModel API endpoint. Coding Plan-specific endpoint and entitlement behavior is intentionally not the default trusted route.

### MiniMax compatibility choice

The trusted MiniMax profile certifies the direct OpenAI-compatible Chat Completions path. Other MiniMax compatibility surfaces are not automatic fallback routes.

Kimi and any additional provider remain outside this cohort until separately reviewed and registered.

## Deterministic worker materialization

The deterministic renderer is:

```text
scripts/render_gh_aw_workers.py
```

For `protocol: openai-compatible`, validated Registry metadata is rendered into the canonical worker security/lifecycle contract using:

- `engine.id = copilot`;
- `engine.model = Registry model`;
- `COPILOT_PROVIDER_BASE_URL`;
- `COPILOT_MODEL`;
- `COPILOT_PROVIDER_API_KEY = secrets.<registered credential>`;
- `COPILOT_PROVIDER_TYPE`;
- `COPILOT_PROVIDER_WIRE_API`;
- an exact provider `network_host` allowlist entry.

The renderer may change only engine/provider frontmatter owned by the trusted profile. It must not change target identity, lifecycle instructions, protected files, Safe Outputs, Feature Event authority, Gate authority, merge authority, or release authority.

### Bounded missing-source bootstrap

Normal Registry reads require every registered worker source to exist. That remains the fail-closed contract for resolver, preflight, effective-model audit, cross-repository allowlisting, runtime routing, security validation, and renderer `--check`.

The renderer's write/materialization path alone may load a structurally valid full Registry with `require_source_files=False` so a newly registered generated source can be created before it exists. Immediately after generation, normal strict Registry loading must succeed. The deterministic proof is:

```text
python scripts/validate_gh_aw_worker_materialization.py
```

Use the drift check before certification:

```text
python scripts/render_gh_aw_workers.py --all --check
```

## Generated workflow profile/credential surfaces

GitHub Actions `workflow_dispatch` profile choices and bounded secret-presence checks are presentation/plumbing, not trust authority. They are generated from the validated Registry by:

```text
scripts/render_gh_aw_profile_surfaces.py
```

The generated YAML keeps secret references explicit and reviewable. Only boolean credential presence reaches Python preflight code; secret values are never passed through arguments, JSON artifacts, summaries, Task Packages, or Registry files.

Check for drift with:

```text
python scripts/render_gh_aw_profile_surfaces.py --check
```

Credential aliases used for backward compatibility are Registry metadata rather than provider-specific shell branches. For example, Codex keeps its existing `OPENAI_API_KEY` primary credential with `CODEX_API_KEY` as an explicit compatibility alias.

## Provider registration and certification path

A compatible provider follows this ordered path:

```text
Registry entry
  -> shared Registry validation
  -> deterministic worker render
  -> strict gh-aw compile
  -> compiled-lock validation
  -> static runtime preflight
  -> live entitlement probe
  -> bounded autonomous dogfood
  -> durable evidence
  -> maturity promotion
```

### 1. Registry entry

Add only trusted metadata to `runtimes/gh-aw/engine-profiles.yaml`. Do not add provider-name-specific branches to Python routing/validation logic. Do not expose provider/model/profile/credential/worker selectors to target Issue Comment commands.

### 2. Shared validation

Run the Registry and profile checks:

```text
python scripts/validate_gh_aw_provider_registry.py
python scripts/validate_gh_aw_engine_profiles.py
```

An unknown profile, malformed unrelated entry, duplicate worker, duplicate credential identity, invalid source path, invalid model/host/URL, or unregistered worker must fail closed.

Legacy compatibility assertions protect the existing profile mappings as a required subset/prefix; they must not make the Registry an exact closed set that blocks a future valid extension.

### 3. Deterministic render

Render the registered worker source and generated workflow surfaces, then use their `--check` modes to prove no drift remains.

### 4. Strict compile

Compile every registered worker with the repository-pinned `github/gh-aw` compiler/runtime dependency and strict mode. The PR compile matrix is derived from the validated Registry rather than a provider-name list.

Do not hand-edit `.lock.yml` output. The compiled lock must retain reviewed compiler/schema/strict metadata and match the Registry engine/model identity. A wrong or stale compiled worker is not accepted merely because its filename is registered.

Security handling for compiler-generated locks also uses exact Registry `worker_workflow` identities plus strict pinned compiler metadata; a lookalike filename is not trusted.

### 5. Static preflight

Run **AI-SDLC gh-aw Runtime Preflight** for the registered profile.

Static preflight verifies only trusted Registry resolution, registered compiled-lock validity, compiler/strict metadata, and boolean credential presence. `READY_FOR_ENTITLEMENT_PROBE` means only that these static prerequisites are present.

It explicitly does **not** prove:

- provider subscription or entitlement;
- quota or billing state;
- current model availability;
- current endpoint health;
- rate-limit capacity/headroom;
- successful inference;
- autonomous task success.

### 6. Live entitlement probe

Use a separate bounded live probe with the minimum provider permissions and request scope needed to classify real access. Keep live provider calls out of static preflight and out of Commander/control-plane routing.

Live failures should distinguish at least:

- `READY`;
- `AUTH_FAILED`;
- `QUOTA_OR_BILLING_BLOCKED`;
- `RATE_LIMITED`;
- `MODEL_OR_ENDPOINT_UNAVAILABLE`;
- `FAILED`.

Qwen, GLM, and MiniMax are statically integrated by `F-GHAW-DOMESTIC-PROVIDERS-0001`; live entitlement and bounded dogfood are not claimed without separate durable evidence.

### 7. Bounded dogfood

After live entitlement is proven, run the same bounded autonomous Developer contract used by existing gh-aw workers:

- exact trusted target repository/ref;
- read-only repository permissions by default;
- bounded allowed files;
- Safe Outputs for GitHub writes;
- Draft PR to the Feature branch;
- structured Worker Result;
- trusted collector/Feature Event persistence;
- no Gate self-approval, merge, or release authority.

### 8. Maturity promotion

Static compatibility and successful compilation do not promote maturity. Promotion requires separate durable evidence from live entitlement and bounded dogfood behavior. A maturity change is an explicit reviewed change, not a side effect of adding Registry metadata.

DeepSeek, Qwen, GLM, and MiniMax remain `experimental` until such evidence exists and an explicit reviewed maturity change is approved.

## Synthetic extension and anti-special-case proof

The repository includes a deterministic synthetic extension validator:

```text
python scripts/validate_gh_aw_registry_extension.py
```

It derives synthetic provider/profile identities from generic-module digests, injects them only into a temporary workspace, and exercises Registry validation, rendering, resolution, static preflight, exact worker allowlisting, effective-model audit, and generated workflow surfaces.

The proof also checks that generic production-module hashes remain unchanged and uses an AST guard to reject provider/profile literal branches while allowing capability constants and explicit test-only compatibility baselines.

## Effective model audit

Every registered OpenAI-compatible profile is audited by:

```text
python scripts/validate_gh_aw_effective_model_metadata.py
```

The Registry model must agree with the rendered `engine.model`, rendered `COPILOT_MODEL`, compiled run metadata (`GH_AW_INFO_MODEL`), compiled telemetry model (`GH_AW_ENGINE_MODEL`), and compiled `agent_model` metadata.

The audit is generic across compatible profiles and must not contain provider-specific source/lock fallbacks.

## Security and authority boundary

OpenAI compatibility never grants lifecycle authority. Provider workers retain the same bounded contract as native profiles:

- repository permissions remain read-only by default;
- Safe Outputs own GitHub writes;
- workers may not write authoritative Feature state or Feature Events directly;
- workers may not pass or waive Gates;
- workers may not self-approve independent review or Verification;
- workers may not merge or release;
- target repositories and Issue Comment commands may not choose arbitrary provider/model/profile/credential/compiled-worker values;
- exact worker allowlisting comes from the fully validated trusted Registry;
- Runtime App exact-target and cross-repository authority boundaries remain unchanged.

Do not add direct provider HTTP calls to `commander.py`, Feature persistence, static preflight, or trusted routing logic. Future compatible providers must enter through the same Registry/render/compile/preflight/probe/dogfood/certification path.
