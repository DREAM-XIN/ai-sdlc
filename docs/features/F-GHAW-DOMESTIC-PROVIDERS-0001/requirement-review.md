# Requirement Review — F-GHAW-DOMESTIC-PROVIDERS-0001

## Verdict

**PASS_WITH_NOTES**

Severity summary:

- BLOCKER: 0
- MAJOR: 0
- MINOR: 2
- SUGGESTION: 0

The Requirement is sufficiently complete and testable to proceed to Design. The two MINOR findings are Design/Documentation obligations and do not require Requirement rework.

## Review basis

Reviewed independently against:

- Issue `#198`;
- `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/requirement.md`;
- `gates/review-rubrics.yaml` requirement dimensions;
- the merged provider-registry/certification contract from `F-GHAW-PROVIDER-REGISTRY-0001`;
- current official provider documentation for Alibaba Cloud Model Studio/Qwen, Zhipu BigModel/GLM, and MiniMax.

## Requirement rubric

### Problem and goal

PASS — The Requirement identifies the concrete gap left after Provider Registry generalization: only one real non-native direct compatible provider has exercised the generic path. The goal is bounded to three additional real providers and does not absorb autonomous-role or maturity-promotion work.

### Scope and non-goals

PASS — Qwen, GLM, and MiniMax are in scope; Kimi and other providers are explicitly excluded. Direct provider integrations are distinguished from Alibaba-hosted third-party models. Workspace-id/base-URL target overrides, Coding Plan endpoints, lifecycle authority changes, and compiler/runtime repinning are excluded.

### User/runtime scenarios and business rules

PASS — The Requirement defines the static integration path, credential-presence semantics, live-entitlement boundary, maturity rules, existing-profile compatibility, exact worker allowlisting, and target-command restrictions.

### Acceptance criteria

PASS — All 13 criteria are objectively verifiable through Registry inspection, deterministic renderer checks, strict compiler output, static preflight, effective-model audit, command-boundary/security validators, documentation inspection, and final PR CI.

### Edge cases and constraints

PASS — Unknown profiles/workers, malformed Registry entries, duplicate identities, unsafe URLs/paths, missing credentials, provider secret leakage, target-controlled execution selectors, and live-evidence absence are explicitly bounded.

## Provider fact review

### Qwen

The selected Beijing shared OpenAI-compatible base URL is officially documented as:

`https://dashscope.aliyuncs.com/compatible-mode/v1`

Alibaba Cloud states that this shared DashScope domain remains available while workspace-dedicated domains are recommended for production. This makes the shared domain suitable for one deterministic trusted Registry entry without introducing a workspace-id template or target-controlled host.

The selected model `qwen3.7-plus` is currently recommended by Alibaba Cloud for coding tools as a balanced model with tool calling.

### GLM

Zhipu documents the general API base URL:

`https://open.bigmodel.cn/api/paas/v4`

and Chat Completions at `/chat/completions` with Bearer API-key authentication. `glm-5.2` is currently listed as the flagship/default text model in the official API documentation. The Requirement correctly excludes the separate Coding Plan endpoint from the default profile.

### MiniMax

MiniMax documents direct OpenAI-compatible usage with:

`https://api.minimaxi.com/v1`

and `/chat/completions`, with `MiniMax-M2.7` in the supported model set. Choosing the OpenAI-compatible route is consistent with this Feature's purpose even though MiniMax also offers/recommends Anthropic compatibility for some coding-tool scenarios.

## Findings

### RQ-MINOR-1 — Static certification terminology must remain explicit

**Severity:** MINOR

The Requirement intentionally allows lifecycle Acceptance after static Registry/render/compile/preflight/audit certification even when live credentials are unavailable. This is acceptable for an `experimental` profile, but downstream Design, Implementation Evidence, and Acceptance must not shorten this to an ambiguous claim such as "provider certified" without qualification.

Required follow-through:

- use explicit states such as `static certification passed`, `live entitlement not established`, and `bounded dogfood not established` where applicable;
- do not treat `READY_FOR_ENTITLEMENT_PROBE` as live readiness;
- do not promote maturity from static evidence alone.

### RQ-MINOR-2 — Provider source freshness and region/key coupling must be durable

**Severity:** MINOR

The selected provider facts are current external service contracts and can change independently of this repository. Qwen also couples API keys to regions, and the chosen shared Beijing domain is a deliberate portability/stability tradeoff rather than Alibaba Cloud's preferred production dedicated-domain form.

Required follow-through in Design/Implementation Evidence:

- record the official source page and observation date for each endpoint/model choice;
- state Qwen Beijing-region API-key coupling explicitly;
- retain the ban on target/workspace-controlled base-URL overrides;
- treat a future endpoint/model migration as a reviewed Registry change rather than an implicit runtime substitution.

## Security and authority review

PASS — The Requirement preserves the trusted atomic Registry boundary, HTTPS/host/path/credential validation, exact worker allowlisting, Safe Output ownership of GitHub writes, Feature Event/Persist authority, Gate independence, and merge/release authority. It does not introduce provider HTTP calls into Commander or persistence and does not expand target Issue Comment selectors.

## Compatibility review

PASS — Existing `copilot`, `codex`, `claude`, `gemini`, and `deepseek` profiles are explicitly preserved; `copilot` remains the default; DeepSeek remains `experimental`; and the full Registry must continue to fail closed on any malformed entry.

## Conclusion

No BLOCKER or MAJOR finding prevents the Requirement Gate from passing. The Requirement may be approved with the two MINOR follow-through obligations above. Design should now define the exact Registry entries, generated artifacts, strict-compile matrix expansion, provider-fact evidence format, live-evidence status model, migration implications, and deterministic regression strategy.

## Official source references reviewed

- Alibaba Cloud Model Studio — Base URL overview: `https://help.aliyun.com/en/model-studio/base-url`
- Alibaba Cloud Model Studio — Text generation models: `https://help.aliyun.com/en/model-studio/text-generation-model/`
- Zhipu BigModel — API quick start: `https://docs.bigmodel.cn/cn/api/introduction`
- Zhipu BigModel — Chat Completions API: `https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8`
- MiniMax — OpenAI API compatibility: `https://platform.minimaxi.com/docs/api-reference/text-openai-api`
