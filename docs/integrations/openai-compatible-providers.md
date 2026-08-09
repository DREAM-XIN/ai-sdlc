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

Generic compatible-provider behavior branches on capabilities such as `protocol`, `engine`, `provider_type`, and `wire_api`. It must not branch on `provider == "deepseek"` or another provider/profile name.

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

A future compatible provider follows this ordered path:

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

### 3. Deterministic render

Render the registered worker source and generated workflow surfaces, then use their `--check` modes to prove no drift remains.

### 4. Strict compile

Compile the registered worker with the repository-pinned `github/gh-aw` compiler/runtime dependency and strict mode. Do not hand-edit `.lock.yml` output.

The compiled lock must retain the reviewed compiler/schema/strict metadata and match the Registry engine/model identity. A wrong or stale compiled worker is not accepted merely because its filename is registered.

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

## Synthetic extension and anti-special-case proof

The repository includes a deterministic synthetic extension validator:

```text
python scripts/validate_gh_aw_registry_extension.py
```

It derives synthetic provider/profile identities from generic-module digests, injects them only into a temporary workspace, and exercises Registry validation, rendering, resolution, static preflight, exact worker allowlisting, effective-model audit, and generated workflow surfaces.

The proof also checks that generic production-module hashes remain unchanged and uses an AST guard to reject provider/profile literal branches while allowing capability constants and the explicit test-only compatibility baseline.

## Effective model audit

Every registered OpenAI-compatible profile is audited by:

```text
python scripts/validate_gh_aw_effective_model_metadata.py
```

The Registry model must agree with the rendered `engine.model`, rendered `COPILOT_MODEL`, compiled run metadata (`GH_AW_INFO_MODEL`), compiled telemetry model (`GH_AW_ENGINE_MODEL`), and compiled `agent_model` metadata.

The audit is generic across compatible profiles and must not contain a DeepSeek-only source/lock path or hard-coded provider fallback model.

## DeepSeek reference profile

DeepSeek remains the current OpenAI-compatible reference profile:

```yaml
profile: deepseek
engine: copilot
provider: deepseek
protocol: openai-compatible
model: deepseek-chat
credential: DEEPSEEK_API_KEY
maturity: experimental
```

`experimental` is intentional. This Registry refactor does not promote DeepSeek and does not add Qwen, GLM, MiniMax, Kimi, or another production provider.

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

Do not add direct DeepSeek/Qwen/GLM/MiniMax provider HTTP calls to `commander.py`, Feature persistence, or trusted routing logic. Future compatible providers must enter through the same Registry/render/compile/preflight/probe/dogfood/certification path.
