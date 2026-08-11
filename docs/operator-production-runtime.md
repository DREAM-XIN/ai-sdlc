# Trusted Operator production runtime composition

Issue: #244

## Purpose

Supported AI-client adapters should translate protocol messages, not rebuild Operator authority or storage wiring. `scripts/operator_production_runtime.py` provides one server-owned composition boundary for canonical Project/Feature reads, durable Operation Store reads, Inbox/Decision/Notification reads, and Store-backed canonical operations.

The MCP adapter consumes this bundle while continuing to register only its accepted seven read-only tools. A future approved write-capable adapter can reuse the same trusted runtime/configuration boundary for canonical write capabilities without receiving GitHub tokens, Store checkout credentials, policy authority, or Feature Persist authority in model-visible input.

## Trust-domain separation

Production configuration distinguishes three authority surfaces.

### Target repository truth

`target_repository` identifies the installed project whose `.ai-sdlc/project.yaml` and Feature Manifests are read. `feature_refs` is a trusted server-side map from Feature id to exact Feature branch/ref.

`AI_SDLC_OPERATOR_TARGET_READ_TOKEN` is used only for target-repository GitHub Contents API reads.

### Control/Store repository

`store_repository`, `state_ref`, `store_checkout`, and `store_remote_name` identify the durable Operator Store in the trusted control repository.

`AI_SDLC_OPERATOR_STORE_TOKEN` is used only for Store protection inspection by the concrete GitHub runtime. Git authentication for `store_checkout` is configured separately by the trusted launcher/installation layer and is not taken from client requests.

The target repository and Store repository may be the same repository, but the runtime does not assume that. Cross-repository operation is an explicit supported shape.

### Adapter/principal scope

`principal` and the adapter id are supplied by trusted process composition. The resulting `TrustedContextProvider` ignores client target data when constructing trusted scope. Client `target` is later checked against the configured target repository and Feature allowlist; it cannot widen that allowlist.

## Trusted runtime configuration

Production MCP backing is opt-in. If `AI_SDLC_OPERATOR_RUNTIME_CONFIG` is absent, MCP preserves the accepted fail-closed behavior: seven read tools remain registered, but unbacked canonical capabilities report `CAPABILITY_UNAVAILABLE`.

When configured, the YAML file must use `ai-sdlc.operator-runtime-config/v1` and is expected to be owned by trusted installation/service configuration, not by a Feature branch or model-generated artifact.

Example:

```yaml
version: ai-sdlc.operator-runtime-config/v1
target_repository: example-org/product-repo
store_repository: example-org/control-repo
installation_ref: main
store_checkout: /srv/ai-sdlc/control
principal: operator-service
feature_refs:
  F-EXAMPLE-0001: feature/F-EXAMPLE-0001
state_ref: refs/heads/ai-sdlc-operator-state
store_remote_name: origin
operator_app_slug: ai-sdlc-operator
```

Unknown configuration keys are rejected rather than ignored. Feature/ref bindings must be one-to-one.

## MCP boundary

MCP now transports optional canonical `context` in addition to `api_version`, `target`, and `payload`. This is necessary for capabilities such as `operation.status`, whose `operation_id` is part of the canonical request context.

Canonical context is not trusted identity. MCP still provides no tool field for:

- trusted principal;
- trusted authorization policy;
- Store repository/ref selection;
- GitHub credentials;
- adapter identity override;
- lifecycle Gate authority;
- Feature Persist authority.

Even if the shared backend bundle contains `operation.start` and `operation.cancel`, MCP does not register those capabilities as tools. Transport exposure and backend availability remain separate boundaries.

## Relationship to other v0.3 work

- Issue #241 supplies the personal-repository ruleset proof/provisioning path needed for the protected Store ref in user-owned control repositories.
- Issue #232 / PR #233 is responsible for the second materially independent write-capable OpenAI Responses adapter.
- Issue #239 defines real dogfood evidence and prerequisite preflight.
- Issue #221 retains release-level external-effect fault-injection evidence.

This composition work is deterministic implementation evidence only and does not itself constitute release dogfood or v0.3 readiness.
