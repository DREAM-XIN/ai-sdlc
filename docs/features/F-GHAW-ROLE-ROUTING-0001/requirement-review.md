# Requirement Review — F-GHAW-ROLE-ROUTING-0001

Role: independent Requirement Reviewer

Verdict: **PASS_WITH_NOTES**

Severity summary: 0 BLOCKER / 0 MAJOR / 2 MINOR

## Review scope

Reviewed the proposed role-aware gh-aw routing Requirement against the current trusted Provider Registry/runtime baseline, existing static preflight semantics, manual profile dispatch surface, lifecycle authority boundaries, and the stated non-goals.

## What is sound

- The problem is real and bounded: eight profiles are registered while normal autonomous execution still has a global Copilot default.
- v1 separates pre-dispatch static-readiness fallback from runtime/inference retry; this avoids accidentally introducing retry/circuit-breaker semantics without evidence.
- Default policy is conservative: Codex/Claude/Gemini reference profiles are preferred by role, while experimental DeepSeek/Qwen/GLM/MiniMax are excluded from production defaults.
- Reviewer and QA policy mappings are explicitly audit-only in this Feature and do not expand autonomous role authority.
- Target repositories remain unable to choose provider/model/profile/credential/worker/candidate order or experimental opt-in.
- Routing evidence is required to be deterministic and non-secret.
- Fail-closed cases are sufficiently enumerated to be testable.
- Existing manual direct profile dispatch is preserved for compatibility without redefining lifecycle authority.

## MINOR-1 — credential readiness source semantics must be explicit in Design

The Requirement treats readiness as a boolean credential-presence signal, which is appropriate, but current Registry profiles do not all have identical credential semantics. Codex has a primary `OPENAI_API_KEY` plus trusted alias `CODEX_API_KEY`, while Copilot uses `COPILOT_GITHUB_TOKEN`, which may be derived from trusted GitHub runtime context rather than a normal repository secret.

Design must define a single trusted readiness abstraction that:

- derives acceptable credential identities from validated Registry metadata;
- handles approved aliases without exposing secret values;
- handles system-provided trusted runtime tokens explicitly rather than pretending they are repository secrets;
- produces only boolean/presence readiness inputs for the routing resolver;
- fails closed on ambiguous or unsupported credential semantics.

This is a Design detail rather than a Requirement blocker because the Requirement already prohibits secret values and requires trusted readiness signals.

## MINOR-2 — manual trusted profile dispatch boundary must be explicit

The Requirement preserves `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` for operator diagnostics/testing while introducing automatic role routing. Design must specify that:

- normal autonomous lifecycle dispatch consumes trusted routing policy;
- target-controlled paths cannot reach or populate manual profile selection;
- manual `engine_profile` selection is an explicit trusted operator action and is not a policy fallback mechanism;
- routing audit evidence must identify whether resolution came from role policy or an explicit trusted diagnostic/manual invocation when applicable.

This is non-blocking because the Requirement already prohibits target-controlled selectors, but the operational precedence must be unambiguous before implementation.

## Acceptance-testability assessment

All 14 Acceptance Criteria are objectively testable. In particular, AC2/AC3 define preferred/fallback behavior, AC4 prevents accidental autonomous-role expansion, AC5/AC6 enforce maturity and target-boundary controls, AC7/AC8 cover readiness/fail-closed behavior, AC9 requires durable audit data, and AC13/AC14 retain the existing regression/CI envelope.

## Decision

Requirement may pass `requirement-gate` with the two MINOR notes carried into Design and independently checked at Design Review.
