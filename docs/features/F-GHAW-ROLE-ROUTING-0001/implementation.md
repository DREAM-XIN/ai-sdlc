# Implementation Evidence — F-GHAW-ROLE-ROUTING-0001

Feature: `F-GHAW-ROLE-ROUTING-0001`

Issue: `#200`

PR: `#201`

Implementation candidate: `3a946632697e6338f77298d6a1ca7e2f41fb3531`

## Result

Developer implementation is complete for the approved Requirement, Design, Design remediation, and Plan. This evidence records implementation and deterministic validation only; it does not self-approve Code Review or `code-gate`.

## WU-1 — Provider Registry credential-source contract

Completed.

- Added required validated `credential_source` metadata to trusted `EngineProfile`.
- Supported sources are exactly `secret` and `github-token`.
- Migrated all eight registered profiles explicitly.
- Copilot uses `github-token`; Codex, Claude, Gemini, DeepSeek, Qwen, GLM, and MiniMax use `secret`.
- `github-token` profiles cannot define credential aliases.
- Codex primary/alias readiness remains supported through Registry metadata.
- Unsupported source and invalid alias/source combinations fail closed.

This closes the Design Review MAJOR without introducing `if profile == "copilot"` or equivalent provider/profile-name routing branches.

## WU-2 — Trusted routing policy and loader

Completed.

Added `runtimes/gh-aw/profile-routing.yaml` with the approved default policy:

- Developer / implementation: `codex` → `copilot`.
- Reviewer / code-review: `claude` → `copilot`.
- QA / verification: `gemini` → `copilot`.

Default rules do not allow experimental profiles.

`scripts/gh_aw_profile_routing.py` validates duplicate YAML keys, policy version, unknown fields, rule identity, unique role/stage matches, candidate ordering/uniqueness, Registry membership, Boolean `allow_experimental`, and maturity restrictions.

## WU-3 — Readiness surface generation

Completed.

Registry-derived workflow generation now maps credential source capabilities to presence-only expressions:

- `secret` → `secrets.<IDENTITY> != ''`;
- `github-token` → `github.token != ''`.

Generated readiness surfaces cover preflight, same-repository routed dispatch, and cross-repository routed dispatch. Python receives booleans only; secret values are not serialized into resolver input/output.

The generic renderer branches on validated `credential_source`, not profile/provider identity.

## WU-4 — Role-aware resolver and audit

Completed.

The resolver:

1. resolves exactly one trusted rule from role/stage;
2. requires a complete Boolean readiness map for every candidate in that rule before selecting;
3. evaluates candidates in deterministic policy order;
4. skips only static credential non-readiness;
5. validates the selected candidate's registered compiled worker;
6. fails closed on malformed policy/context/readiness or no ready candidate.

Audit output records selection mode, policy/rule/context, evaluated candidate decisions, selected profile/engine/provider/protocol/model/worker/maturity, fallback state/reason, and `entitlement_verified: false`.

No runtime/provider HTTP probe occurs during routing.

## WU-5 — Developer dispatch integration

Completed.

Normal autonomous same-repository and cross-repository gh-aw gateways now derive trusted role/stage from Commander output and resolve the worker through the trusted routing policy when no explicit trusted diagnostic override is supplied.

A Developer implementation therefore attempts Codex first and falls back to Copilot only when Codex is statically not ready and Copilot is ready.

During Developer self-verification, the normal Issue Comment command path was found to still invoke the manual profile gateway whose default was Copilot. That path was corrected: `/ai-sdlc dispatch-gh-aw ...` now invokes the role-aware core gateway with an empty trusted worker override, so policy routing owns normal selection.

The manual `ai-sdlc-gh-aw-dispatch-profile.yml` workflow remains available only as a trusted operator diagnostic path and emits `selection_mode: manual-trusted-profile`.

Reviewer and QA remain manual runtime roles in `dispatch/gh-aw-developer.yaml`; this Feature does not make them autonomous.

## WU-6 — Boundary/security regression

Completed.

Target Issue Comment parsing does not expose or derive outputs for:

- engine profile;
- provider;
- model;
- credential;
- compiled worker;
- candidate order;
- `allow_experimental`;
- profile-routing policy override.

Normal target commands enter the role-aware core gateway, not the manual profile gateway.

The exact worker remains Registry-bound. Feature Manifest/Event/Gate, Safe Output, Runtime App, reviewer/QA independence, merge authority, and release authority were not changed.

Transport failure remains a failing trusted job; v1 does not guess a lifecycle `BLOCKED` transition or perform automatic live-provider failover.

## WU-7 — Compatibility and verification package

Completed on implementation code candidate `3a946632697e6338f77298d6a1ca7e2f41fb3531`.

Required GitHub Actions results:

- Validate AI-SDLC protocol — SUCCESS — run `31313659209`.
- Validate Public Runtime Distribution — SUCCESS — run `31313659196`.
- Validate AI-SDLC gh-aw Worker Compile — SUCCESS — run `31313659199`.
- Required PR Gate — SUCCESS — run `31313659202`.

The protocol run covers the routing regression in `scripts/validate.py`, lifecycle/Commander/cross-repository/security validation, shared Provider Registry validation, synthetic extension and anti-special-case checks, bounded worker materialization, generated surface drift, effective-model metadata, routing command boundary, credential-source-aware runtime preflight, and release-readiness validation.

The worker compile workflow preserves strict compilation coverage for all eight registered profiles.

## Deterministic regression coverage

Added/updated tests cover:

- Codex primary credential preferred selection;
- Codex alias readiness;
- Codex missing → Copilot static fallback;
- no ready candidate failure;
- incomplete readiness-map failure even when the preferred candidate is ready;
- unknown role/stage;
- duplicate/unknown/disallowed experimental policy candidates;
- trusted experimental opt-in parsing without changing the default policy;
- unsupported credential source;
- `github-token` alias rejection;
- metadata-driven preflight presence expectations;
- synthetic Registry extension with the new credential-source contract;
- provider/profile literal-branch guard covering routing/readiness modules;
- generated routed-dispatch credential surfaces;
- target command inability to select routing/execution identities.

## Security and authority statements

- No secret value is intentionally passed to or emitted by Python routing logic.
- Credential presence is static readiness only and never proves entitlement, billing, quota, model availability, endpoint health, rate-limit headroom, or successful inference.
- Experimental DeepSeek/Qwen/GLM/MiniMax profiles remain excluded from the default routing policy and retain `experimental` maturity.
- `runtimes/gh-aw/runtime.yaml` continues to keep Copilot as the global compatibility default.
- Automatic fallback after a live provider/runtime/inference failure is not implemented.
- Product, Architect, Reviewer, QA, and Acceptance autonomy is not expanded by this Feature.
- Developer does not approve this implementation artifact or pass `code-gate`; those remain independent Code Reviewer responsibilities.

## Known limits / follow-ups

- Reviewer and QA policy routes are deterministic/auditable but remain manual until a separately approved autonomous-role Feature.
- Experimental providers require a trusted policy change with explicit `allow_experimental: true`; target repositories cannot opt themselves in.
- Runtime inference failure retry/circuit breaking, cost/latency/quality scoring, and adaptive routing remain future Features.
