# Acceptance Evidence — F-GHAW-DOMESTIC-PROVIDERS-0001

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

PR: `#199`

Role: independent Product / Acceptance

Verdict: **PASS**

## Product acceptance decision

The Feature is accepted against the approved Requirement.

The intended product outcome was to add a bounded first cohort of real domestic providers through the generic trusted gh-aw Registry/certification path, prove their deterministic/static integration without re-introducing provider-name-specific trusted control logic, and keep live entitlement/maturity claims evidence-based.

That outcome is satisfied.

## Accepted provider cohort

The trusted Registry now includes:

- Qwen: `qwen3.7-plus`, Beijing DashScope OpenAI-compatible endpoint, `DASHSCOPE_API_KEY`, `experimental`;
- GLM: `glm-5.2`, BigModel general OpenAI-compatible endpoint, `ZHIPUAI_API_KEY`, `experimental`;
- MiniMax: `MiniMax-M2.7`, direct MiniMax OpenAI-compatible endpoint, `MINIMAX_API_KEY`, `experimental`.

Existing Copilot, Codex, Claude, Gemini, and DeepSeek profiles remain valid. `copilot` remains the default profile. DeepSeek remains `experimental`.

Kimi or another fourth provider was not added.

## Acceptance of static certification boundary

Product accepts the following evidence classification exactly as approved in the Requirement:

| Provider | Static certification | Live entitlement | Bounded dogfood | Maturity |
| --- | --- | --- | --- | --- |
| Qwen | PASS | Not established | Not established | `experimental` |
| GLM | PASS | Not established | Not established | `experimental` |
| MiniMax | PASS | Not established | Not established | `experimental` |

The absence of live entitlement/dogfood evidence is **not** treated as a failed acceptance criterion because the approved Requirement explicitly allows static certification to complete when repository credentials or a trusted bounded live-probe mechanism are unavailable, provided the limitation is stated durably and no maturity claim is inflated.

No statement in this Acceptance Evidence should be read as a claim that provider subscription, billing, quota, endpoint health, current model availability, rate-limit headroom, or successful live inference was proven.

## Requirement outcome acceptance

Accepted outcomes:

1. Qwen, GLM, and MiniMax use the shared validated Registry contract and deterministic generated/compiled artifacts.
2. Generic trusted behavior remains Registry/capability-driven rather than provider-name-driven.
3. Static certification passes full Registry validation, deterministic generation/drift checks, strict compile, compiled identity/model checks, static preflight semantics, effective-model audit, exact worker allowlisting, command/security checks, and public-runtime validation.
4. Static preflight does not overclaim entitlement.
5. Existing five profiles remain backward compatible and Copilot remains the default.
6. DeepSeek/Qwen/GLM/MiniMax remain `experimental`.
7. Target Issue Comment syntax does not expose arbitrary provider/model/profile/credential/worker identity selectors.
8. Feature Manifest, Feature Event, Gate, Safe Output, Runtime App, independent review/QA, merge, and release authority remain outside provider worker authority.

## Verification acceptance

Independent QA returned PASS with all 13 approved Acceptance Criteria covered.

QA lifecycle candidate `f0de1a100531513ab155f1ecba28df1efdf12b93` passed:

- Validate AI-SDLC protocol — `31311501170` — SUCCESS;
- Validate Public Runtime Distribution — `31311501167` — SUCCESS;
- Validate AI-SDLC gh-aw Worker Compile — `31311501168` — SUCCESS;
- Required PR Gate — `31311501172` — SUCCESS.

The Registry-derived compile matrix includes all eight profiles and the new Qwen, GLM, and MiniMax jobs complete successfully under pinned strict gh-aw compilation.

## Review note disposition

Code Review returned PASS_WITH_NOTES with `CR-MINOR-1`: add dedicated future negative fixtures around the generalized compiler-generated-lock security exception.

Product accepts this as non-blocking follow-up hardening because:

- it does not represent an unmet approved Acceptance Criterion;
- exact validated Registry worker identity is required;
- strict/schema/compiler metadata are directly enforced;
- unregistered candidate locks fail closed;
- current generated artifacts carry the expected attestation;
- independent Code Review and QA both accepted the boundary;
- final protocol/security CI passed.

The note should remain visible for future test-hardening work and must not be interpreted as permission to weaken the boundary.

## Release Gate recommendation

All required Requirement, Design, Code Review, and Verification Gates have passed with durable Evidence. The approved scope is complete and accepted.

`release-gate` may PASS. After trusted persistence confirms the Gate and Acceptance DONE state, PR #199 may be merged under normal repository authority.
