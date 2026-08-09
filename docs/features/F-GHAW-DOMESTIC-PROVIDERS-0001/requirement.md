# Requirement — Qwen, GLM, and MiniMax trusted gh-aw provider profiles

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

Profile: `standard-feature`

## Problem

`F-GHAW-PROVIDER-REGISTRY-0001` made the trusted gh-aw provider path registry-driven, but the only non-native direct OpenAI-compatible provider currently registered is DeepSeek. The repository therefore has not yet proven that the new provider Registry/certification contract can be used repeatedly for multiple real providers without re-introducing provider-name-specific control logic.

The next step is to add a bounded first cohort of domestic providers through the new generic path and certify their static integration while keeping live entitlement and maturity claims evidence-based.

## Goal

Register Qwen, GLM, and MiniMax as trusted `experimental` OpenAI-compatible gh-aw profiles and prove that each profile can be validated, rendered, strictly compiled, statically preflighted, audited for effective model identity, and admitted to exact worker allowlisting without changing generic provider-control logic.

The Feature must preserve all existing profiles and security/lifecycle authority boundaries. It must not claim live provider access unless a bounded live probe actually succeeds with repository credentials.

## Provider facts to pin

The implementation must use official provider documentation current at implementation time and record the selected API facts in durable documentation.

### Qwen / Alibaba Cloud Model Studio

Required initial profile facts:

- profile/provider id: `qwen`
- protocol: `openai-compatible`
- engine: `copilot`
- provider type: `openai`
- wire API: `completions`
- Beijing shared OpenAI-compatible base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- network host: `dashscope.aliyuncs.com`
- initial model pin: `qwen3.7-plus`
- trusted repository credential name: `DASHSCOPE_API_KEY`
- maturity: `experimental`

Rationale: Alibaba Cloud documents the Beijing DashScope OpenAI-compatible shared domain as still supported, while recommending workspace-dedicated domains for production. The shared domain is selected because the trusted Registry requires a deterministic hostname and cannot safely encode a per-user workspace id. Alibaba Cloud currently recommends `qwen3.7-plus` for coding tools as a balanced model with tool calling.

### GLM / Zhipu BigModel

Required initial profile facts:

- profile/provider id: `glm`
- protocol: `openai-compatible`
- engine: `copilot`
- provider type: `openai`
- wire API: `completions`
- base URL: `https://open.bigmodel.cn/api/paas/v4`
- network host: `open.bigmodel.cn`
- initial model pin: `glm-5.2`
- trusted repository credential name: `ZHIPUAI_API_KEY`
- maturity: `experimental`

Rationale: Zhipu documents the general endpoint `https://open.bigmodel.cn/api/paas/v4` and `chat/completions` with Bearer API-key authentication. `glm-5.2` is the current flagship model in the official API documentation. The Coding Plan endpoint is explicitly out of scope because it has product-specific entitlement semantics.

### MiniMax

Required initial profile facts:

- profile/provider id: `minimax`
- protocol: `openai-compatible`
- engine: `copilot`
- provider type: `openai`
- wire API: `completions`
- base URL: `https://api.minimaxi.com/v1`
- network host: `api.minimaxi.com`
- initial model pin: `MiniMax-M2.7`
- trusted repository credential name: `MINIMAX_API_KEY`
- maturity: `experimental`

Rationale: MiniMax documents direct OpenAI-compatible Chat Completions at `https://api.minimaxi.com/v1` and currently lists `MiniMax-M2.7` as a supported model. Anthropic compatibility may be recommended for some MiniMax use cases, but this Feature intentionally certifies the existing generic OpenAI-compatible Registry path.

## Required outcomes

1. **Three real provider profiles use the generic Registry contract.** Qwen, GLM, and MiniMax are expressed primarily as Registry metadata plus deterministic generated/compiled worker artifacts.
2. **No provider-name-specific trusted control branches.** Generic renderer, resolver, preflight, effective-model audit, compiled-lock validation, cross-repository allowlisting, command boundary, and workflow-surface generation continue to branch only on capabilities/metadata.
3. **Static certification is complete.** Each profile passes full Registry validation, deterministic rendering, generated-surface drift checks, strict compile, compiled-lock identity/model validation, static preflight semantics, and effective-model audit.
4. **Live entitlement remains separately evidenced.** Missing repository credentials must produce a missing-credential/non-ready static state. Credential presence may only advance to an entitlement-probe-ready state; it may not imply entitlement, quota, billing, model availability, endpoint health, rate-limit headroom, inference success, or dogfood success.
5. **Existing profiles remain backward compatible.** `copilot`, `codex`, `claude`, `gemini`, and `deepseek` remain valid, their trusted worker identities do not drift unexpectedly, and the default profile remains `copilot`.
6. **Maturity remains conservative.** Qwen, GLM, MiniMax, and DeepSeek remain `experimental` unless separate live/dogfood evidence and an explicit reviewed maturity change justify promotion.
7. **Target repositories remain unable to select arbitrary execution identity.** No provider/model/profile/credential/worker selector is added to Issue Comment commands.
8. **Authority boundaries remain unchanged.** Provider workers remain read-only by default, use Safe Outputs for GitHub writes, cannot edit authoritative Feature state directly, cannot self-pass Gates, and cannot merge or release.

## Scope

### Registry entries

Add exactly three profiles to `runtimes/gh-aw/engine-profiles.yaml` using the facts above. The profile-specific Registry metadata is expected and is not considered a provider-specific control branch.

### Worker materialization

Use the existing generic `scripts/render_gh_aw_workers.py` path to materialize:

- `.github/workflows/ai-sdlc-gh-aw-worker-qwen.md`
- `.github/workflows/ai-sdlc-gh-aw-worker-glm.md`
- `.github/workflows/ai-sdlc-gh-aw-worker-minimax.md`

and strictly compile their corresponding `.lock.yml` files using the repository-pinned gh-aw compiler/runtime dependency. Generated worker/lock output must not be hand-maintained as an alternative to the renderer/compiler pipeline.

### Generated profile/credential surfaces

Use `scripts/render_gh_aw_profile_surfaces.py` so workflow-dispatch choices and boolean credential-presence plumbing are derived from the fully validated Registry. Secret values must never be serialized into Python arguments, artifacts, Task Packages, summaries, or logs.

### Validation and certification

The final implementation must exercise, at minimum:

- `scripts/validate_gh_aw_provider_registry.py`
- `scripts/validate_gh_aw_registry_extension.py`
- `scripts/validate_gh_aw_engine_profiles.py`
- `scripts/validate_gh_aw_effective_model_metadata.py`
- `scripts/validate_gh_aw_runtime_preflight.py`
- `scripts/validate_gh_aw_command_boundary.py`
- deterministic renderer/surface `--check` modes
- repository protocol/security/public-runtime validation
- strict worker compile matrix for all registered profiles

Existing fixed compatibility snapshots may be extended only where they are explicitly test-only compatibility assertions; they must not become runtime authority.

### Live evidence

If `DASHSCOPE_API_KEY`, `ZHIPUAI_API_KEY`, or `MINIMAX_API_KEY` is configured in the repository and a trusted bounded live-probe mechanism exists, the Feature may collect provider-specific live evidence. If any credential or probe mechanism is unavailable, static certification may still pass but Acceptance/Documentation must state that live entitlement and bounded dogfood were not established for that provider.

No provider may be promoted above `experimental` in this Feature.

## Security requirements

- All new base URLs must be HTTPS.
- URLs must not contain embedded credentials, query strings, or fragments.
- `network_host` must exactly match the base URL hostname.
- New worker sources must remain repository-relative canonical paths without traversal or non-canonical segments.
- Credential identities must remain globally unique in the Registry.
- New provider credentials must be repository secrets referenced only from trusted generated workflow/worker configuration.
- Cross-repository runtime must accept only exact registered compiled worker workflow names.
- Commander, lifecycle transition, Gate evaluation, persistence, and Safe Output code must not make direct provider HTTP calls.
- Target Issue Comment syntax must continue to reject provider/model/profile/credential/worker selectors.

## Compatibility requirements

The following existing profile identities must remain present and valid:

- `copilot`
- `codex`
- `claude`
- `gemini`
- `deepseek`

`copilot` remains the default execution profile. DeepSeek remains `experimental`.

The addition of three new profiles must not relax Registry atomic fail-closed validation: one malformed entry must still invalidate the Registry for all trusted consumers.

## Non-goals

- Do not add Kimi or another fourth provider in this Feature.
- Do not use Alibaba-hosted third-party GLM/MiniMax profiles as substitutes for the direct provider integrations defined above.
- Do not use GLM Coding Plan or Alibaba Coding Plan endpoints as the default profile endpoint.
- Do not introduce workspace-id templating or arbitrary target-controlled base URL overrides.
- Do not add autonomous Product, Architect, Reviewer, QA, or Acceptance workers.
- Do not expose provider/model/profile/credential/worker selectors to target repositories.
- Do not change Feature Manifest authority, Feature Event sourcing, revision semantics, Gate semantics, Safe Output semantics, Runtime App trust, merge authority, or release authority.
- Do not replace or unpin the reviewed gh-aw compiler/runtime dependency.

## Acceptance criteria

1. The trusted Registry contains `qwen`, `glm`, and `minimax` profiles with the required endpoint/model/credential metadata and `experimental` maturity.
2. All three profiles are consumed by generic trusted Registry/render/resolution/preflight/audit/allowlist code without new provider-name-specific Python branches.
3. All three deterministic worker sources are generated by the existing renderer and pass `--check` drift validation.
4. Generated workflow profile choices and credential-presence plumbing include the new profiles and pass `--check` drift validation without exposing secret values.
5. Qwen, GLM, and MiniMax workers strictly compile using the pinned compiler/runtime dependency; compiled engine/model metadata matches the Registry for each profile.
6. Effective-model audit applies the same invariants to DeepSeek and all three new OpenAI-compatible profiles.
7. Static preflight reports missing credentials as non-ready and credential presence only as readiness for a separate entitlement probe.
8. Unknown profiles, unregistered worker workflow names, malformed entries, duplicate identities, and unsafe URLs/paths continue to fail closed.
9. Existing Copilot, Codex, Claude, Gemini, and DeepSeek deterministic/security/compile tests remain green and `copilot` remains the default.
10. Issue Comment command syntax continues to reject arbitrary provider/model/profile/credential/worker selectors.
11. Documentation records the official provider facts selected by this Feature and clearly distinguishes static certification from live entitlement/dogfood evidence.
12. Final PR required protocol/security/public-runtime/worker-compile checks pass on the final lifecycle candidate.
13. No provider is promoted above `experimental`, and no new lifecycle/Gate/merge/release authority is introduced.

## Evidence expected for completion

- approved Requirement and Design evidence;
- Registry diff showing only trusted metadata additions for the three providers;
- deterministic renderer and generated-surface drift evidence;
- strict compile results for all registered profiles;
- effective-model and static-preflight regression evidence for all OpenAI-compatible profiles;
- fail-closed and command-boundary/security validator output;
- final PR required CI results;
- documentation with official provider source references and explicit live-entitlement/dogfood evidence status.
