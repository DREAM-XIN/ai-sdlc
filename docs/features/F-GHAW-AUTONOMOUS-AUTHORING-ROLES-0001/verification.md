# Verification Evidence — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Verification identity

Role: **independent QA / Verification**  
Feature: `F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001`  
Issue: `#204`  
PR: `#207`  
Manifest revision at verification start: `19`  
Frozen production-code candidate: `7a9029ae8f48416c477f32df05ff530ed86891b5`  
Verification-start user commit: `bc7319946e3d60c79fe87608c49762997dee4d73`  
Trusted lifecycle materialization head inspected: `741ce81ec9e1ad5938057c0bd6215725a325a4dd`

This verification is independent of Implementation and Code Review. Code Review v1 REWORK and the subsequent remediation are retained as history; QA verifies the remediated candidate and the final acceptance-criteria behavior rather than inheriting the Review verdict.

## Verdict

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0
- NOTE: GitHub reported `action_required` rather than test execution for checks attached directly to the trusted Persist bot materialization commit `741ce81e...`. This is not treated as a passing test. Candidate traceability instead uses (a) the fully green reviewed production candidate and (b) the fully green verification-start user commit, plus a commit comparison proving that no production code changed between the frozen candidate and the current verification lifecycle head.

`verification-gate` is eligible for PASS only after this durable Verification Evidence-bearing head itself passes the required PR checks.

## Candidate traceability / environment validity

`7a9029ae... -> 741ce81e...` contains no production-code changes. The delta consists only of:

- Code Review v2 durable evidence;
- Code Review/remediation/verification Feature Events;
- trusted Feature Manifest materialization.

Therefore the behavior under QA is the same production code that passed independent Code Review v2.

The verification-start user commit `bc731994...` passed all four required PR checks:

| Required check | Run | Result |
|---|---:|---|
| Validate AI-SDLC protocol | `31323539166` | SUCCESS |
| Validate AI-SDLC gh-aw Worker Compile | `31323539157` | SUCCESS — complete 18-target matrix |
| Required PR Gate | `31323539169` | SUCCESS |
| Validate Public Runtime Distribution | `31323539162` | SUCCESS |

The strict compile run discovered workers from the validated trusted registries, checked deterministic generated sources, installed the repository-pinned compiler `github/gh-aw@v0.83.4`, and ran `gh aw compile --strict`. The inspected Orchestrator/Codex job completed with `0 error(s), 0 warning(s)` and verified the expected lock path. The same matrix run completed successfully for all discovered trusted profile/role targets.

## Trusted role/profile metadata verified

Authoring registry identities:

- `requirement-product-claude` -> Product / requirement / Claude
- `requirement-product-copilot` -> Product / requirement / Copilot
- `design-architect-claude` -> Architect / design / Claude
- `design-architect-copilot` -> Architect / design / Copilot
- `plan-orchestrator-codex` -> Orchestrator / plan / Codex
- `plan-orchestrator-copilot` -> Orchestrator / plan / Copilot

Trusted production routing order:

- Product / requirement: `claude -> copilot`
- Architect / design: `claude -> copilot`
- Orchestrator / plan: `codex -> copilot`
- existing Developer / implementation: `codex -> copilot`
- existing Reviewer / code-review: `claude -> copilot`
- existing QA / verification: `gemini -> copilot`

Every production rule has `allow_experimental: false`. Reference profile metadata remains trusted-registry controlled. Gemini currently pins `engine_version: 0.52.0` and `model: gemini-3.5-flash-lite`; shared effective-model validation covers the provider/profile workers without accepting target-controlled model selection.

## Acceptance-criteria verification

### AC1 — exact dispatch and manual review/release boundaries: PASS

Deterministic routing validates autonomous dispatch only for `product+requirement`, `architect+design`, and `orchestrator+plan`. `product+acceptance`, Requirement Review, and Design Review resolve through manual/trusted paths. Role-only expansion is not used for the new authoring routes.

### AC2 — deterministic non-experimental profile routing: PASS

Trusted routing contains exactly the approved preferred/fallback orders and all production rules exclude experimental profiles. Registry-derived readiness testing covers preferred selection, Copilot fallback, no-ready failure, incomplete readiness failure, duplicate candidates, unknown profiles, experimental opt-in rejection, and duplicate role/stage rules.

### AC3 — closed role-worker registry: PASS

The six authoring worker identities are explicitly registered by role/stage/profile/source/compiled-lock. Resolver validation is fail-closed for unknown or ambiguous identities.

### AC4 — deterministic source/lock generation and strict compile: PASS

The complete trusted matrix passes deterministic source checks and strict compilation with pinned `github/gh-aw@v0.83.4`. The matrix includes the six authoring role workers plus the existing trusted role/profile workers, for 18 compile targets total.

### AC5 — closed authoring result semantics: PASS

The authoring result schema is closed and role/stage constrained. Adapter regression proves Product/requirement resolves to the specialized authoring worker and `ai-sdlc-gh-aw-authoring-result-v0.1`, rather than generic Developer completion semantics.

### AC6 — trusted collector provenance validation: PASS

The collector validates the source Actions run, workflow identity/ref, trusted task, Feature/revision, role/stage, repository/ref, and contract before translation. The remediated translator consumes the already-validated source-run identity and persists exact run provenance durably.

### AC7 — Product result boundedness: PASS

Deterministic translation permits only requirement draft registration, `requirement: DONE`, and `requirement-review: READY`. It emits no Gate mutation.

### AC8 — Architect result boundedness: PASS

Deterministic translation permits only design draft registration, `design: DONE`, and `design-review: READY`. It emits no design-gate mutation.

### AC9 — Orchestrator result boundedness: PASS

Deterministic translation permits only plan draft registration, `plan: DONE`, and `implementation: READY`. No Gate mutation is emitted.

### AC10 — design-remediation task boundedness: PASS

Remediation identity/status must match the trusted task. A completed remediation may produce the next draft/superseding design candidate and complete only the remediation task; independent Design Review authority is not changed.

### AC11 — bounded repository write paths: PASS

Canonical destinations are derived from trusted code for requirement/design/plan. Explicit negative fixtures reject:

- traversal (`../outside.md`);
- unrelated `docs/**`;
- `state/**`;
- `.github/**`;
- traversal through the Feature id supplied to canonical path construction.

The closed worker result contains no accepted authoritative path field, and authoring workers do not receive direct Manifest/Event write authority.

### AC12 — target-controlled routing/path escalation rejected: PASS

Trusted policy/registry resolution owns provider, model, profile, worker, candidate order, experimental opt-in, workflow and canonical path choice. Shared routing, adapter, command-boundary and workflow-security tests remain green.

### AC13 — exact provenance and replay fail-closed: PASS

Safe Output comment/file presence alone is insufficient. Durable authoring evidence points to the exact validated Actions run. `source_run_id` is included in deterministic evidence/Event identity. Tests prove distinct source runs cannot collapse to one replay identity; malformed source-run identity, stale revision, unsupported identity and ambiguous-current-draft conditions fail closed.

### AC14 — existing Developer/Reviewer/QA compatibility: PASS

Existing production routing remains `codex -> copilot`, `claude -> copilot`, and `gemini -> copilot`. Shared Protocol regression, role registry checks, gate-worker security, feature-context, effective-model, command-boundary and runtime-preflight checks all pass.

### AC15 — Acceptance/release remains manual/trusted: PASS

No authoring translator can create or PASS `release-gate`, merge, or release state. Product/acceptance remains outside autonomous authoring routing. Current authoritative Manifest confirms `release-gate: PENDING` while verification is in progress.

### AC16 — final candidate CI: PASS for QA candidate

The verification-start user commit passed Protocol, Public Runtime Distribution, Required PR Gate and the complete 18-target strict compile matrix, as recorded above. This Verification Evidence-bearing commit must independently re-pass the required checks before QA authorizes `verification-gate: PASS`.

## Negative-path and integration coverage

The Protocol suite used for QA includes:

- Feature schema/event/persistence transitions and stale-revision handling;
- GitHub persistence and Feature Event push resolution;
- PR lifecycle Event validation;
- Commander and transport behavior;
- same-repository and cross-repository runtime/control-plane integration;
- Git write preconditions and action/workflow security;
- gh-aw adapter and Feature-context validation;
- autonomous authoring routing/result/provenance/path-negative cases;
- engine-profile and effective-model metadata checks;
- command boundary and runtime preflight;
- public distribution and release-readiness validation.

No required regression or integration check failed on the QA candidate.

## Evidence quality / known history

- Requirement and Design are approved.
- Code Review v1 is durably recorded as fail/REWORK, not erased.
- Developer remediation is durably recorded and passed required CI.
- Independent Code Review v2 is durably recorded as pass and `code-gate` is authoritative PASS.
- QA candidate is traceable to the remediated production commit and current lifecycle head with no intervening production-code delta.
- The bot-head `action_required` checks are explicitly not counted as passing evidence.

## Verification conclusion

All sixteen approved acceptance criteria have passing, traceable deterministic evidence on the reviewed production candidate. Required regression, security, negative-path, compile and integration checks are green. QA finds no blocker or major/minor defect and recommends `verification-gate: PASS` after this Evidence-bearing head itself completes the required PR checks successfully.