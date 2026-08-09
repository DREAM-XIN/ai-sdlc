# Requirement — Trusted gh-aw provider registry generalization

Feature: `F-GHAW-PROVIDER-REGISTRY-0001`

Issue: `#195`

Profile: `standard-feature`

## Problem

AI-SDLC already separates runtime, provider, and model selection and already has a registry-driven OpenAI-compatible worker renderer. DeepSeek proves that a non-native provider can run through the trusted gh-aw BYOK path without receiving lifecycle authority.

However, the provider extension path is not yet fully registry-driven. Several trusted validators and workflow surfaces still enumerate the current profiles or special-case DeepSeek. As a result, adding another compatible provider can require edits across trusted control-plane code and workflow YAML instead of being expressed primarily as trusted registry metadata plus generated/compiled worker artifacts.

This creates avoidable maintenance cost, makes provider certification inconsistent, and increases the risk that a new provider is accidentally added to one trust surface but omitted from another.

## Goal

Make the gh-aw provider/profile extension path registry-driven and deterministic so a future trusted OpenAI-compatible provider can be registered, rendered, strictly compiled, statically preflighted, audited for its effective model, bounded by the existing worker security contract, and admitted to trusted routing without adding provider-name-specific control branches.

The Feature must preserve current behavior for Copilot, Codex, Claude, Gemini, and DeepSeek and must not change the default execution profile.

## Required outcomes

1. **Registry is the trusted provider inventory.** Trusted validation and worker allowlisting derive applicable profiles from `runtimes/gh-aw/engine-profiles.yaml` instead of maintaining separate exhaustive provider-name sets.
2. **OpenAI-compatible validation is generic.** Provider metadata validation is based on protocol/capability fields such as `protocol`, `provider`, `base_url`, `network_host`, `model`, `credential`, `wire_api`, and `maturity`, not on provider names.
3. **Effective-model audit is generic.** Every applicable registered OpenAI-compatible profile is checked for the same authoritative model pin and compiled runtime/audit metadata invariants currently proven for DeepSeek.
4. **Static preflight is generic and non-invasive.** Preflight resolves trusted profile metadata, verifies the compiled lock and credential-presence signal, and reports static readiness without claiming live provider entitlement, quota, billing, model availability, or rate-limit capacity.
5. **Unknown identities fail closed.** Unknown profile ids, invalid registry entries, and unregistered worker workflow names remain rejected.
6. **Target repositories cannot choose arbitrary execution identities.** Issue Comment commands must not gain provider/model/credential/worker selectors. Provider selection remains a trusted control-plane concern.
7. **Existing security boundaries remain unchanged.** Provider workers remain read-only by default, use Safe Outputs for GitHub writes, cannot edit authoritative Feature state directly, cannot self-approve Gates, and cannot merge or release.
8. **Future provider onboarding is documented as a certification pipeline.** The documentation must distinguish static registration/validation from live entitlement, bounded dogfood, and maturity promotion.

## Scope

### Registry and renderer contract

- Keep `runtimes/gh-aw/engine-profiles.yaml` as the trusted profile registry.
- Preserve the current deterministic renderer in `scripts/render_gh_aw_workers.py` as the source for provider-specific worker materialization.
- Validate the common OpenAI-compatible contract without adding provider-specific branches.
- Preserve unique trusted `worker_source`, `worker_workflow`, and credential mappings.

### Validator generalization

Generalize at least the following trusted validation paths:

- `scripts/validate_gh_aw_engine_profiles.py`
- `scripts/validate_gh_aw_runtime_preflight.py`
- `scripts/validate_gh_aw_effective_model_metadata.py`

The implementation may introduce a shared helper if that reduces duplicate registry parsing/validation and keeps trusted behavior deterministic.

### Workflow selection and preflight

Review and generalize the provider enumeration currently present in:

- `.github/workflows/ai-sdlc-gh-aw-preflight.yml`
- `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml`

Any replacement must still resolve the selected profile through trusted registry validation before a compiled worker can be used.

Credential handling must not expose secret values. A dynamic credential mechanism is acceptable only if GitHub Actions can enforce it safely and deterministic validation covers the behavior; otherwise a bounded trusted mapping generated from registry metadata is acceptable. The implementation must not trade away secret-boundary clarity merely to remove visible YAML enumeration.

### Regression and extension tests

Add deterministic regression coverage proving that a fixture OpenAI-compatible profile can pass registry/renderer/validator logic without introducing provider-name-specific Python branches.

Tests must also prove rejection of malformed or unregistered profiles/workers.

### Documentation

Update the OpenAI-compatible provider integration documentation to define:

`registry entry → worker render → strict compile → static preflight → live entitlement probe → bounded dogfood → maturity promotion`

The documentation must make clear that `experimental` or `reference` maturity is evidence-backed lifecycle metadata and not implied by API compatibility alone.

## Compatibility requirements

The Feature must preserve all current registered profiles and their trusted worker workflow mappings:

- `copilot`
- `codex`
- `claude`
- `gemini`
- `deepseek`

The default profile remains `copilot` unless a separate future Feature changes trusted routing policy.

DeepSeek must remain `experimental` unless separate live/dogfood evidence justifies promotion; this refactor alone is not promotion evidence.

Existing cross-repository Runtime App behavior, exact-target worker allowlisting, Safe Output boundaries, Feature Event persistence, optimistic revisions, and Gate authority rules must continue to work unchanged.

## Security requirements

- Registry values that become filenames, model identifiers, credentials, URLs, or network allowlist entries must continue to be syntactically validated.
- OpenAI-compatible provider endpoints must remain HTTPS and must not embed credentials.
- `network_host` must continue to match the provider base URL hostname exactly.
- Provider credentials remain repository secrets referenced by trusted configuration; secret values must not be serialized into Task Packages, logs, artifacts, or registry files.
- Target Issue Comment syntax must continue to reject arbitrary `provider`, `model`, `engine_profile`, `credential`, or `worker_workflow` selectors.
- Trusted worker workflows must continue to be accepted only when present in the trusted registry.
- Provider integration must not introduce direct provider HTTP calls into Commander, lifecycle transitions, Gate evaluation, or persistence.

## Non-goals

- Do not add Qwen, GLM, MiniMax, Kimi, or another new provider in this Feature.
- Do not change the current model for any existing provider unless required to preserve an existing verified invariant.
- Do not promote DeepSeek from `experimental` solely because the registry implementation becomes generic.
- Do not add autonomous Product, Architect, Reviewer, QA, or Acceptance workers.
- Do not introduce intelligent/provider-scoring routing.
- Do not expose provider/model selection to target repositories.
- Do not change Feature Manifest authority, Event Inbox/event sourcing, revision semantics, Gate semantics, Safe Outputs, Runtime App trust, or branch/write protection.
- Do not replace or unpin the currently reviewed gh-aw compiler/runtime dependency as part of this Feature.

## Acceptance criteria

1. A registered OpenAI-compatible profile is validated from trusted registry metadata without adding its provider name to an exhaustive Python profile/provider set.
2. Provider-specific Python conditionals such as `if provider == "deepseek"` are not required for the generic validation, preflight, effective-model audit, or trusted worker allowlist path.
3. `validate_gh_aw_effective_model_metadata.py` or its replacement validates every registered applicable OpenAI-compatible provider worker and compiled lock using the same effective-model invariants.
4. Runtime preflight resolves provider/protocol/model/maturity/credential/worker information from the trusted registry and remains explicitly non-invasive.
5. Missing credentials produce a non-ready credential state; credential presence alone never claims live entitlement.
6. Unknown profile ids and unregistered worker workflow names fail closed.
7. Existing Copilot, Codex, Claude, Gemini, and DeepSeek deterministic tests remain green and their worker mappings do not drift unexpectedly.
8. `dispatch-gh-aw` Issue Comment syntax does not accept provider/model/profile/credential/worker selectors.
9. A deterministic fixture test demonstrates that one additional OpenAI-compatible provider can be introduced through registry metadata and generated worker artifacts without modifying provider-specific Python control logic.
10. All repository validation/security workflows relevant to `runtimes/**`, `scripts/**`, and `.github/workflows/**` pass.
11. Documentation defines the complete provider certification path and clearly separates static compatibility from live inference entitlement and dogfood maturity.
12. No change in this Feature grants an AI provider or worker direct lifecycle/Gate/merge/release authority.

## Evidence expected for completion

- deterministic validator output covering registry-driven extension and malformed-profile rejection;
- strict compiled worker metadata checks for all applicable OpenAI-compatible profiles;
- preflight regression evidence for existing profiles;
- command-boundary/security validation proving target repositories cannot inject provider/model/worker selections;
- repository CI required checks passing on the implementation PR;
- documentation review confirming the provider certification sequence and unchanged authority boundaries.

## Follow-up boundary

After this Feature is accepted, separate Features may:

1. register and certify Qwen, GLM, and MiniMax as new `experimental` provider profiles;
2. introduce role-aware autonomous worker/output contracts;
3. run a mixed-provider full-lifecycle dogfood and use its durable evidence to decide maturity promotion.
