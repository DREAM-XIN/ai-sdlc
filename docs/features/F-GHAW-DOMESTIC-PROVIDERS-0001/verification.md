# Verification Evidence — F-GHAW-DOMESTIC-PROVIDERS-0001

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

PR: `#199`

Role: independent Verification QA

Verdict: **PASS**

Findings: **0 BLOCKER / 0 MAJOR**

## Candidate and environment validity

Verification is based on the repository-hosted PR candidate and GitHub Actions execution using the reviewed pinned dependencies.

Final lifecycle candidate checked by QA: `f0de1a100531513ab155f1ecba28df1efdf12b93`.

All required PR workflows passed on that candidate:

- Validate AI-SDLC protocol — run `31311501170` — **SUCCESS**;
- Validate Public Runtime Distribution — run `31311501167` — **SUCCESS**;
- Validate AI-SDLC gh-aw Worker Compile — run `31311501168` — **SUCCESS**;
- Required PR Gate — run `31311501172` — **SUCCESS**.

The implementation payload `e519035ccec5d6dda924faab4ee0b8a538f10147` independently passed the same four workflow classes before review evidence/lifecycle-only commits were added.

## Acceptance-criteria coverage

### AC1 — Registry contains Qwen, GLM, MiniMax with approved metadata

**PASS.**

The trusted Registry contains exactly the required new profiles:

- `qwen` → Beijing DashScope OpenAI-compatible endpoint, `qwen3.7-plus`, `DASHSCOPE_API_KEY`, `experimental`;
- `glm` → BigModel general endpoint, `glm-5.2`, `ZHIPUAI_API_KEY`, `experimental`;
- `minimax` → MiniMax OpenAI-compatible endpoint, `MiniMax-M2.7`, `MINIMAX_API_KEY`, `experimental`.

HTTPS/host/model/path/credential constraints are validated by the shared Registry boundary.

### AC2 — Generic trusted control path, no new provider-name production branches

**PASS.**

Registry, renderer, resolution, static preflight, effective-model audit, compiled-lock validation, cross-repository worker allowlisting, workflow-surface generation, and action-security lock identity resolution use validated metadata/capability branches. Qwen/GLM/MiniMax identities appear where expected in Registry metadata, generated artifacts, provider documentation, and test/certification fixtures rather than production provider-specific control branches.

### AC3 — Deterministic worker source generation and drift check

**PASS.**

The three committed worker sources were generated from the canonical worker contract. Protocol validation includes renderer `--all --check` and the dedicated bounded materialization regression.

The missing-source Design Review remediation is verified in both directions: a normal Registry read rejects a missing registered source, bounded write materialization can create it, normal strict loading succeeds after generation, and fails again if the source is removed.

### AC4 — Generated workflow choices/credential-presence plumbing

**PASS.**

Preflight/dispatch surfaces are Registry-generated and include `qwen`, `glm`, `minimax`. Credential handling uses boolean checks for `DASHSCOPE_API_KEY`, `ZHIPUAI_API_KEY`, and `MINIMAX_API_KEY`; secret values are not serialized into Python input or evidence. Generated-surface `--check` is part of protocol validation.

### AC5 — Strict compile and compiled engine/model identity

**PASS.**

GitHub Actions run `31311150690` and final-candidate run `31311501168` derive the compile matrix from the validated Registry and strict-compile every registered profile using pinned `github/gh-aw v0.83.4`.

Run `31311150690` contains successful jobs for all eight profiles, including successful `qwen`, `glm`, and `minimax` jobs.

Compiler-generated lock metadata for the three new profiles records schema `v4`, `strict: true`, `agent_id: copilot`, and the Registry model:

- Qwen: `qwen3.7-plus`;
- GLM: `glm-5.2`;
- MiniMax: `MiniMax-M2.7`.

### AC6 — Generic effective-model audit

**PASS.**

The effective-model validator iterates all OpenAI-compatible Registry profiles, so the same invariant covers DeepSeek, Qwen, GLM, and MiniMax across Registry model, rendered engine model, provider-routing model, compiled run metadata, compiled telemetry, and compiled `agent_model`.

### AC7 — Static preflight semantics

**PASS.**

Static preflight keeps these semantics for every registered profile:

- missing credential → `MISSING_CREDENTIAL` / non-ready;
- valid lock + credential-present boolean → `READY_FOR_ENTITLEMENT_PROBE`;
- `entitlement_verified` remains false.

No static result is treated as subscription, quota, billing, endpoint health, rate-limit capacity, inference, or dogfood evidence.

### AC8 — Fail-closed negative paths

**PASS.**

Deterministic validators cover unknown profiles, unregistered worker workflow identities, malformed unrelated Registry entries, duplicate worker/credential identities, path traversal/non-canonical paths, unsafe URLs, invalid compatible credential aliases, and source-existence failure.

Atomic Registry validation remains in force before trusted identity selection.

### AC9 — Existing profile compatibility and default profile

**PASS.**

Copilot, Codex, Claude, Gemini, and DeepSeek remain present and are covered by the same successful compile/security/protocol run. Legacy profile mappings remain explicit backward-compatibility assertions rather than runtime authority. `copilot` remains the default profile and DeepSeek remains `experimental`.

### AC10 — Command boundary

**PASS.**

The existing Issue Comment command grammar remains bounded and validation continues rejecting target-controlled provider/model/profile/credential/worker selectors. No new target execution-identity input was introduced.

### AC11 — Durable provider documentation and evidence classification

**PASS.**

`docs/integrations/openai-compatible-providers.md` and `provider-certification.md` record the selected provider facts, observation date, Qwen region/key coupling, GLM/MiniMax endpoint choices, and the distinction among static certification, live entitlement, bounded dogfood, and maturity.

### AC12 — Final required PR checks

**PASS.**

Final QA lifecycle candidate `f0de1a100531513ab155f1ecba28df1efdf12b93` passed all four required workflow classes listed above, including Registry-derived strict worker compile.

### AC13 — Conservative maturity and unchanged authority

**PASS.**

DeepSeek, Qwen, GLM, and MiniMax all remain `experimental`. No Feature Manifest, Feature Event, Gate, Safe Output, Runtime App, merge, or release authority was granted to provider workers or target repositories.

Kimi was not added.

## Live-entitlement and dogfood status

QA did **not** infer live provider access from compilation, Registry validity, or credential-presence plumbing.

For Qwen, GLM, and MiniMax:

- static certification: **PASS**;
- live entitlement: **NOT ESTABLISHED**;
- bounded provider dogfood: **NOT ESTABLISHED**;
- maturity: **experimental**.

This is compliant with the approved Requirement, which explicitly permits static certification when repository credentials or a trusted bounded live-probe mechanism are unavailable, provided Acceptance/Documentation state the limitation.

## Code Review note follow-up

Code Review `CR-MINOR-1` requested future deterministic negative fixtures specifically around the generalized compiler-generated lock exception (`unregistered lookalike`, `strict:false`, wrong compiler, wrong schema). QA confirms this is a non-blocking test-hardening note rather than an uncovered current acceptance criterion: the current implementation directly enforces all four conditions, exact Registry identity is required, current generated artifacts carry the expected attestation, and final protocol/security CI is green.

## Verification Gate recommendation

All 13 approved Acceptance Criteria have passing, traceable evidence on the candidate. No required regression or integration check failed. `verification-gate` may PASS and the Feature may advance to independent Product Acceptance.

QA does not perform Product Acceptance, merge, or release.
