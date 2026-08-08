# OpenAI-compatible providers

AI-SDLC keeps provider choice in the execution plane. The lifecycle protocol, Feature Manifest, revision rules, Gates, Evidence, Safe Outputs, and persistence remain provider-neutral.

## Architectural boundary

AI providers are optional execution backends, not AI-SDLC control-plane dependencies.

The control plane owns durable lifecycle state and authority. A runtime adapter executes bounded work. A provider supplies inference to a runtime when that runtime needs a model. These are distinct layers:

```text
AI-SDLC control plane
  -> runtime adapter
  -> optional provider
  -> optional model
```

Examples:

- `ChatGPT Web` can be used as a manual runtime without AI-SDLC directly calling an OpenAI API.
- `gh-aw` is a runtime; `deepseek` is a provider profile used by that runtime; `deepseek-chat` is the selected model.
- A future IDE runtime may use Cursor, Windsurf, Copilot, Claude, or another agent while preserving the same Task Package, Evidence, and lifecycle contracts.
- A human can also satisfy a bounded worker contract without any model provider.

Provider integrations exist primarily to enable unattended execution and routing by capability, availability, cost, and policy. Adding a provider must not give that provider lifecycle authority or introduce provider-specific dependencies into Commander, Feature state, Gate evaluation, or persistence.

## Reference architecture

The first OpenAI-compatible reference profile is `deepseek`. It reuses the pinned `github/gh-aw` Copilot engine in BYOK mode instead of adding provider-specific HTTP calls to Commander or Runtime Router.

```text
AI-SDLC control plane
  -> gh-aw runtime
  -> trusted provider profile
  -> Copilot BYOK adapter
  -> OpenAI-compatible provider
```

The profile registry is `runtimes/gh-aw/engine-profiles.yaml`. A compatible provider profile declares:

- `engine: copilot`
- `protocol: openai-compatible`
- provider/model metadata
- an HTTPS `base_url`
- an exact `network_host`
- a provider-specific repository secret name
- a dedicated rendered worker source and compiled lock
- a maturity level

The deterministic renderer maps these fields to gh-aw BYOK variables such as `COPILOT_PROVIDER_BASE_URL`, `COPILOT_MODEL`, `COPILOT_PROVIDER_API_KEY`, `COPILOT_PROVIDER_TYPE`, and `COPILOT_PROVIDER_WIRE_API`. The provider credential stays provider-specific in repository configuration; it is not stored as `OPENAI_API_KEY` merely because the wire protocol is compatible.

## DeepSeek reference profile

The initial profile uses:

```yaml
profile: deepseek
engine: copilot
provider: deepseek
protocol: openai-compatible
model: deepseek-chat
credential: DEEPSEEK_API_KEY
maturity: experimental
```

`experimental` is intentional. Static validation and strict compilation prove the trusted workflow shape, not live inference entitlement or complete agent compatibility.

Before live use:

1. configure the repository secret `DEEPSEEK_API_KEY`;
2. run the AI-SDLC gh-aw preflight for `deepseek`;
3. run the bounded DeepSeek entitlement probe;
4. only after a successful probe, run the same bounded autonomous dogfood contract used by the reference workers;
5. promote maturity only after Draft PR, Worker Result, Feature Event persistence, revision advancement, and the next Commander handoff are observed.

## Provider failure classification

Static preflight does not call providers. Live probes should distinguish at least:

- `READY`
- `AUTH_FAILED`
- `QUOTA_OR_BILLING_BLOCKED`
- `RATE_LIMITED`
- `MODEL_OR_ENDPOINT_UNAVAILABLE`
- `FAILED`

This prevents a provider-capacity failure from being misclassified as an AI-SDLC protocol or Safe Output defect.

## Security boundary

OpenAI compatibility does not grant lifecycle authority. Provider workers retain the same bounded contract as native profiles:

- repository permissions remain read-only by default;
- edits remain limited to the worker's `allowed-files` scope;
- Safe Outputs own GitHub writes;
- workers may not write authoritative Feature state or Feature Events directly;
- workers may not pass/waive Gates, self-approve review, merge, or release;
- the deterministic conclusion job verifies the Draft PR and constructs the Worker Result.

Do not add direct DeepSeek/Qwen/GLM HTTP calls to `commander.py` or runtime routing logic. Additional compatible providers should be introduced as trusted profiles and validated through the same renderer/compiler/probe pipeline.
