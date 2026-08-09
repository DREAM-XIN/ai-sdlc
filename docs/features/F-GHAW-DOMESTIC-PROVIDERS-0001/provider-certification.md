# Provider Certification Evidence — Qwen, GLM, and MiniMax

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

Observed provider facts: `2026-08-09`

## Certification meaning

This evidence distinguishes deterministic/static certification from live provider access.

`static certification` means the trusted Registry entry, deterministic worker source, pinned strict compiler output, compiled identity/model metadata, generated credential-presence plumbing, static preflight semantics, exact worker allowlisting, and repository security validators agree.

It does **not** establish provider subscription, entitlement, quota, billing state, endpoint health, rate-limit headroom, successful inference, or bounded autonomous dogfood. Those claims require separate live evidence.

## Qwen / Alibaba Cloud Model Studio

Trusted profile:

- profile/provider: `qwen`
- protocol: `openai-compatible`
- engine: `copilot`
- provider type: `openai`
- wire API: `completions`
- base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- network host: `dashscope.aliyuncs.com`
- model: `qwen3.7-plus`
- repository secret: `DASHSCOPE_API_KEY`
- maturity: `experimental`
- worker source: `.github/workflows/ai-sdlc-gh-aw-worker-qwen.md`
- worker lock: `.github/workflows/ai-sdlc-gh-aw-worker-qwen.lock.yml`

Official sources used by Requirement/Design:

- `https://help.aliyun.com/en/model-studio/base-url`
- `https://help.aliyun.com/en/model-studio/text-generation-model/`

Operational constraint: this trusted profile uses the Beijing shared endpoint. A configured key must be valid for that service region. Workspace-specific hosts, alternate regions, and target-controlled base URL overrides are intentionally unsupported.

Compiler evidence:

- schema: `v4`
- compiler: `v0.83.4`
- strict: `true`
- compiled agent id: `copilot`
- compiled agent model: `qwen3.7-plus`

Live status:

- entitlement: **not established by this Feature**
- bounded dogfood: **not established by this Feature**
- maturity: `experimental`

## GLM / Zhipu BigModel

Trusted profile:

- profile/provider: `glm`
- protocol: `openai-compatible`
- engine: `copilot`
- provider type: `openai`
- wire API: `completions`
- base URL: `https://open.bigmodel.cn/api/paas/v4`
- network host: `open.bigmodel.cn`
- model: `glm-5.2`
- repository secret: `ZHIPUAI_API_KEY`
- maturity: `experimental`
- worker source: `.github/workflows/ai-sdlc-gh-aw-worker-glm.md`
- worker lock: `.github/workflows/ai-sdlc-gh-aw-worker-glm.lock.yml`

Official sources used by Requirement/Design:

- `https://docs.bigmodel.cn/cn/api/introduction`
- `https://docs.bigmodel.cn/api-reference/模型-api/对话补全`

The general BigModel API endpoint is used. Coding Plan-specific endpoint/entitlement behavior is out of scope and is not a fallback route.

Compiler evidence:

- schema: `v4`
- compiler: `v0.83.4`
- strict: `true`
- compiled agent id: `copilot`
- compiled agent model: `glm-5.2`

Live status:

- entitlement: **not established by this Feature**
- bounded dogfood: **not established by this Feature**
- maturity: `experimental`

## MiniMax

Trusted profile:

- profile/provider: `minimax`
- protocol: `openai-compatible`
- engine: `copilot`
- provider type: `openai`
- wire API: `completions`
- base URL: `https://api.minimaxi.com/v1`
- network host: `api.minimaxi.com`
- model: `MiniMax-M2.7`
- repository secret: `MINIMAX_API_KEY`
- maturity: `experimental`
- worker source: `.github/workflows/ai-sdlc-gh-aw-worker-minimax.md`
- worker lock: `.github/workflows/ai-sdlc-gh-aw-worker-minimax.lock.yml`

Official source used by Requirement/Design:

- `https://platform.minimaxi.com/docs/api-reference/text-openai-api`

This Feature certifies only the direct OpenAI-compatible Chat Completions path. Other MiniMax compatibility surfaces are not fallback routes.

Compiler evidence:

- schema: `v4`
- compiler: `v0.83.4`
- strict: `true`
- compiled agent id: `copilot`
- compiled agent model: `MiniMax-M2.7`

Live status:

- entitlement: **not established by this Feature**
- bounded dogfood: **not established by this Feature**
- maturity: `experimental`

## Shared deterministic evidence

The three profiles are covered by the same generic paths as DeepSeek:

- full atomic Registry validation through `scripts/gh_aw_provider_registry.py`;
- deterministic worker generation through `scripts/render_gh_aw_workers.py`;
- bounded missing-source materialization proof through `scripts/validate_gh_aw_worker_materialization.py`;
- Registry-derived profile/credential surfaces through `scripts/render_gh_aw_profile_surfaces.py`;
- strict compile matrix derived from the validated Registry;
- compiled-lock validation through `scripts/gh_aw_compiled_worker.py`;
- effective-model audit through `scripts/validate_gh_aw_effective_model_metadata.py`;
- static preflight through `scripts/gh_aw_runtime_preflight.py`;
- exact cross-repository worker allowlisting through the validated Registry;
- command-boundary rejection of target-controlled provider/model/profile/credential/worker selectors.

The materialization-only Registry load relaxation is confined to renderer write mode. Normal Registry reads, renderer `--check`, resolver, preflight, audit, allowlisting, and security validation continue to require registered source files to exist.

## CI evidence

PR `#199` has already demonstrated the Registry-derived strict compile matrix on all registered profiles, including Qwen, GLM, and MiniMax, using the pinned `github/gh-aw v0.83.4` compiler. Final protocol/security/required-gate evidence is recorded in `implementation.md` only after the final candidate head is green.
