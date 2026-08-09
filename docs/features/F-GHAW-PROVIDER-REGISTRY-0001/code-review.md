# Code Review — F-GHAW-PROVIDER-REGISTRY-0001

## Verdict

REWORK

- BLOCKER: 0
- MAJOR: 1
- MINOR: 1

The implementation substantially satisfies the approved Requirement and Design and all required CI is green, but one generic credential-readiness defect can make static preflight report readiness for a credential that the generated OpenAI-compatible worker will not actually consume. This must be fixed before `code-gate` can PASS.

## Authoritative baseline reviewed

- Repository: `DREAM-XIN/ai-sdlc`
- Feature: `F-GHAW-PROVIDER-REGISTRY-0001`
- PR: `#196`
- Feature branch: `feature/F-GHAW-PROVIDER-REGISTRY-0001`
- Manifest revision at review start: `13`
- `current_stage: code-review`
- `implementation: DONE`
- `code-review: WORKING`
- `code-gate: PENDING`
- reviewed implementation head before review START persistence: `8b3f9097caba9971e1d8a4adb69cf15bc10894c7`

`AGENTS.md` and `.ai-sdlc/project.yaml` are absent on this repository/Feature branch, so no additional project-specific reviewer constraints were inferred.

## Material reviewed

- approved `requirement-v1`
- approved `design-v1`
- Design Review Evidence and its AST-guard implementation note
- implementation Plan and WU-1 through WU-7 Definition of Done
- durable `implementation-v1` Evidence
- PR #196 actual changed-file set and implementation patches
- `gates/review-rubrics.yaml`
- Registry, renderer, profile-surface generator, compiled-lock helper, resolver, runtime preflight, cross-repository allowlist, effective-model audit, synthetic extension validator and compatibility/security validators
- final pre-review CI runs recorded in implementation Evidence

## Rubric assessment

### Requirement and design compliance — PASS except finding CR-MAJOR-1

The implementation establishes one full-Registry validation boundary, migrates the trusted consumers in scope, generalizes compatible-provider rendering/audit/preflight/worker admission, keeps target Issue Comment runtime selectors closed, preserves the five current profiles/default/maturity, and documents the certification sequence.

### Correctness / error handling — REWORK

#### CR-MAJOR-1 — OpenAI-compatible credential aliases can produce a false-ready static preflight

`credential_aliases` is accepted as common Registry metadata for any profile. `render_gh_aw_profile_surfaces.py` treats the primary credential plus every alias as interchangeable credential-presence signals, so an alias alone can set `present=true`.

For an OpenAI-compatible profile, however, `render_gh_aw_workers.py` injects only the primary Registry credential into:

`COPILOT_PROVIDER_API_KEY: ${{ secrets.<primary credential> }}`

No alias fallback is rendered into the worker. Therefore a future compatible Registry entry with `credential_aliases` can reach `READY_FOR_ENTITLEMENT_PROBE` when only an alias secret exists, while the actual worker still receives an empty primary provider API key.

This is a correctness defect in the generic future-provider extension path. Static preflight is explicitly intended to prove credential presence for the exact registered runtime path; it must not claim readiness based on a secret that the worker does not consume.

Required remediation: make alias semantics match actual runtime consumption. The minimal safe fix is to reject non-empty `credential_aliases` for `protocol: openai-compatible` until the renderer/runtime explicitly supports alias fallback. Preserve the existing Codex native alias compatibility. Add a negative Registry fixture proving a compatible profile with aliases fails closed.

Severity: **MAJOR** because the Feature's core goal is a safe generic future-provider onboarding path and this defect creates a false readiness signal at that boundary.

#### CR-MINOR-1 — Worker source normalization check does not strictly reject all non-canonical path spellings

`_validate_worker_source()` constructs `PurePosixPath(value)` before testing for `.`/empty segments. `PurePosixPath` normalizes some spellings such as repeated separators and dot segments, so the subsequent `parts` checks cannot observe every raw non-canonical spelling that the Design says should be rejected.

This is not currently exploitable as traversal because `..`, absolute paths and backslashes remain rejected, but it weakens the stated deterministic canonical-path contract.

Required remediation: reject raw empty/dot path segments before normalization (while handling the intentional leading `.github` segment correctly), and add negative fixtures for duplicate separators / embedded `./` forms.

Severity: **MINOR**.

### Security / authority boundaries — PASS

No target-controlled provider/model/profile/credential/worker selector was introduced. Registry endpoints remain HTTPS/no-userinfo/no-query/no-fragment with exact host matching. Cross-repository worker admission is derived from the validated Registry. Worker lifecycle/Gate/merge/release authority remains unchanged.

### Compatibility — PASS

The explicit test-only baseline preserves Copilot, Codex, Claude, Gemini and DeepSeek mappings. `copilot` remains the default. DeepSeek remains `experimental`. Codex retains its existing `OPENAI_API_KEY` / `CODEX_API_KEY` accepted secret behavior.

### Maintainability / scope discipline — PASS

The shared Registry boundary and generated marker-owned workflow surfaces reduce duplicated trust logic without adding a new provider or autonomous lifecycle roles.

### Test adequacy — PASS_WITH_REWORK

The synthetic extension and AST guard are appropriately scoped and have positive/negative fixtures. Repository validation and worker compile suites were green on the reviewed candidate. The two findings above require deterministic negative tests as part of remediation.

## Gate decision

`code-gate` remains `PENDING`.

Do not set `code-review: DONE` and do not PASS the Gate. Create a Developer remediation task while keeping the independent Code Review stage current. After remediation and green CI, re-run independent Code Review against the new PR head.
