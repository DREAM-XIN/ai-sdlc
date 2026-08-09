# Acceptance — F-GHAW-ROLE-ROUTING-0001

## Verdict

PASS

## Product outcome

The approved goal was to make the existing multi-provider gh-aw capability actually participate in normal autonomous execution through a trusted, deterministic role-aware routing layer, without transferring provider/model/worker choice to target repositories or expanding lifecycle authority.

That outcome is achieved.

## Acceptance assessment

1. **Autonomous Developer now uses trusted routing.** Normal Issue Comment → gh-aw dispatch enters the core role-aware gateway. For `developer` + `implementation`, the approved candidate order is `codex → copilot` rather than blindly using the global Copilot default.
2. **Fallback semantics match the approved product contract.** Codex is preferred when statically ready; Copilot is selected only when Codex is statically not ready and Copilot is ready. No live-runtime retry/failover behavior was added.
3. **Global compatibility remains stable.** `copilot` remains the global `default_engine_profile` and compatibility fallback. Existing trusted manual profile selection remains available for operator diagnostics.
4. **Reviewer/QA scope did not expand.** `reviewer → claude → copilot` and `qa → gemini → copilot` exist as trusted policy data, but Reviewer and QA do not become autonomous as part of this Feature.
5. **Experimental providers remain bounded.** `deepseek`, `qwen`, `glm`, and `minimax` are absent from default production routes and require trusted explicit experimental opt-in for bounded future use; their maturity is unchanged.
6. **Target repositories cannot control routing identity.** Target Issue Comments and ordinary project inputs cannot select provider, model, profile, credential, worker, candidate order, routing policy, or `allow_experimental`.
7. **Auditability is sufficient for operations and later dogfood.** Routing evidence records policy/rule/context, complete ordered candidates, evaluated readiness decisions, selected profile/engine/provider/model/worker/maturity, fallback status/reason, and explicitly does not claim entitlement verification.
8. **Credential semantics are safe and understandable.** Readiness is presence-only metadata, with source semantics driven by trusted Registry metadata. Secret values are not serialized into routing evidence.
9. **Failure remains fail-closed.** Unknown role/stage, malformed policy, invalid candidate sets, incomplete readiness, disallowed maturity, missing/invalid worker metadata, and no-ready cases do not silently fall back to arbitrary execution.
10. **Authority boundaries remain unchanged.** Feature Manifest/Event/Gate authority, Safe Output, Runtime App trust, independent review/QA, merge and release authority are unchanged.
11. **Eight-profile compatibility is preserved.** Copilot, Codex, Claude, Gemini, DeepSeek, Qwen, GLM and MiniMax remain validated/strictly compilable.

## Final candidate evidence

Acceptance-stage PR head reviewed: `8f3a06031b72f915ae185dc71da357168022805d`.

All required workflows succeeded on that head:

- Validate AI-SDLC protocol — SUCCESS — run `31314391076`
- Validate Public Runtime Distribution — SUCCESS — run `31314391078`
- Validate AI-SDLC gh-aw Worker Compile — SUCCESS — run `31314391075`
- Required PR Gate — SUCCESS — run `31314391092`

Earlier lifecycle evidence also records a real independent Code Review REWORK and bounded Developer remediation before Code Gate PASS, followed by independent QA PASS.

## Decision

The Feature satisfies the approved Requirement and acceptance criteria. No product-level blocker remains.

Acceptance therefore PASSes and supports `release-gate: PASS`, subject to trusted Feature Event persistence. Merge/release remains a separate authority action after the authoritative Manifest reaches DONE and the final PR candidate remains green and mergeable.
