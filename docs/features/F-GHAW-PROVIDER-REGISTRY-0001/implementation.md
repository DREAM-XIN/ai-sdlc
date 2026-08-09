# Implementation Evidence — F-GHAW-PROVIDER-REGISTRY-0001

Feature: `F-GHAW-PROVIDER-REGISTRY-0001`

Issue: `#195`

Role: Implementation Developer

Status: **DONE — trusted Persist applied Implementation completion; ready for independent Code Review**

PR: `#196` — `[F-GHAW-PROVIDER-REGISTRY-0001] Generalize trusted gh-aw provider registry`

Validated implementation candidate SHA: `aaae61797c56047c37a455647bf2238218463bb3`

## Scope implemented

### WU-1 — Shared trusted Registry boundary

Added `scripts/gh_aw_provider_registry.py` as the authoritative full-Registry loader/validator.

The boundary validates the complete Registry before selection, returns frozen normalized profile objects, rejects malformed root/profile schema, duplicate YAML mapping keys, invalid profile/provider/model/credential/path/URL/host metadata, duplicate worker/source/credential identities, and unknown profile/worker lookups.

OpenAI-compatible validation is capability-driven (`protocol`, `engine`, `provider_type`, `wire_api`) rather than provider-name-driven.

Added `scripts/validate_gh_aw_provider_registry.py` with positive/current-profile coverage and fail-closed fixtures for malformed unrelated entries, duplicate worker/credential identities, path traversal, unknown identities, and a compatible extension fixture.

### WU-2 — Trusted Registry consumers

Migrated these consumers to the shared validated Registry:

- `scripts/render_gh_aw_workers.py`
- `scripts/resolve_gh_aw_engine.py`
- `scripts/gh_aw_runtime_preflight.py`
- `scripts/gh_aw_cross_repo_runtime.py`
- `scripts/validate_gh_aw_engine_profiles.py`
- `scripts/validate_gh_aw_runtime_preflight.py`
- `scripts/validate_gh_aw_effective_model_metadata.py`

Added `scripts/gh_aw_compiled_worker.py` to centralize static compiled-lock validation for strict mode, pinned compiler/schema metadata, Registry engine identity, explicit engine-version pin where present, and effective model metadata where present.

Effective-model audit now iterates every registered OpenAI-compatible profile rather than a DeepSeek-only path.

### WU-3 — Generated workflow surfaces

Added `scripts/render_gh_aw_profile_surfaces.py`.

Marker-owned generated blocks now derive from validated Registry metadata in:

- `.github/workflows/ai-sdlc-gh-aw-preflight.yml`
- `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml`

Generated surfaces cover workflow-dispatch profile options plus bounded credential-presence environment/case logic. Secret values never enter Python; YAML contains explicit secret-presence expressions only.

Added `credential_aliases` metadata for the existing Codex `CODEX_API_KEY` compatibility fallback while retaining `OPENAI_API_KEY` as its primary Registry credential.

The renderer and workflow-surface generator both support deterministic `--check` drift validation.

### WU-4 — Synthetic extension / anti-special-case proof

Added `scripts/validate_gh_aw_registry_extension.py`.

The test derives synthetic provider/profile/model/credential/worker identities from generic-module digests, injects them only into a temporary workspace, and exercises:

- Registry validation;
- deterministic worker rendering and drift check;
- trusted profile resolution;
- static preflight with absent/present credential booleans;
- exact worker-workflow allowlisting;
- generic effective-model audit;
- generated workflow surfaces and drift check.

It verifies generic-module hashes are unchanged and synthetic identity literals do not leak into generic production modules.

The AST guard is intentionally scoped to provider/profile identity comparisons and match cases. Positive fixtures prove capability constants and the explicit test-only compatibility baseline remain allowed; negative fixtures prove provider/profile literal branches are rejected.

### WU-5 — Backward compatibility / command-security regression

The explicit test-only compatibility baseline retains:

| Profile | Engine | Primary credential | Alias | Worker | Maturity |
| --- | --- | --- | --- | --- | --- |
| copilot | copilot | COPILOT_GITHUB_TOKEN | — | ai-sdlc-gh-aw-worker.lock.yml | reference |
| codex | codex | OPENAI_API_KEY | CODEX_API_KEY | ai-sdlc-gh-aw-worker-codex.lock.yml | reference |
| claude | claude | ANTHROPIC_API_KEY | — | ai-sdlc-gh-aw-worker-claude.lock.yml | reference |
| gemini | gemini | GEMINI_API_KEY | — | ai-sdlc-gh-aw-worker-gemini.lock.yml | reference |
| deepseek | copilot | DEEPSEEK_API_KEY | — | ai-sdlc-gh-aw-worker-deepseek.lock.yml | experimental |

The runtime default remains `copilot`.

`validate_gh_aw_command_boundary.py` explicitly rejects provider/model/profile/credential/worker selectors and provider credential names on the target Issue Comment command surface.

Cross-repository worker admission now uses the exact worker index from the fully validated Registry.

### WU-6 — Certification documentation

Updated `docs/integrations/openai-compatible-providers.md` with the canonical certification sequence:

`Registry entry -> shared validation -> deterministic render -> strict compile -> compiled-lock validation -> static preflight -> live entitlement probe -> bounded dogfood -> durable evidence -> maturity promotion`.

The document explicitly states that static preflight does not prove subscription, entitlement, quota, billing, model availability, endpoint health, rate-limit capacity, inference success, or autonomous task success.

## Security/authority boundaries preserved

This implementation does not:

- add a new production provider;
- promote DeepSeek maturity;
- change the default `copilot` profile;
- expose provider/model/profile/credential/worker selectors to target Issue Comments;
- change Runtime App exact-target trust;
- change Safe Output write authority;
- change Feature Event / optimistic revision / Gate authority;
- add autonomous Product/Architect/Reviewer/QA roles;
- change merge or release authority;
- replace the pinned gh-aw compiler/runtime dependency.

## WU-7 — Integrated validation evidence

### Candidate and PR identity

- PR: `#196`
- Validated implementation candidate: `aaae61797c56047c37a455647bf2238218463bb3`
- Base at validation: `main@8ba1515f5d9d455683a8eaacbd7443f1e415e0a0`

### Repository validation workflow

`Validate AI-SDLC protocol` run `31307009684`: **SUCCESS**.

The `validate` job `93228862331` completed every repository validator successfully, including:

- `python scripts/validate_github_workflow_security.py`
- `python scripts/validate_cross_repo_transport.py`
- `python scripts/validate_cross_repo_gh_aw_dispatch.py`
- `python scripts/validate_action_security.py`
- `python scripts/validate_gh_aw_adapter.py`
- `python scripts/validate_gh_aw_feature_context.py`
- `python scripts/validate_gh_aw_workflow_security.py`
- `python scripts/validate_gh_aw_engine_profiles.py`
- `python scripts/validate_gh_aw_effective_model_metadata.py`
- `python scripts/validate_gh_aw_command_boundary.py`
- `python scripts/validate_gh_aw_runtime_preflight.py`
- `python scripts/validate_release_readiness.py`

`validate_gh_aw_engine_profiles.py` chains the new targeted checks, so the successful CI result also proves:

- `python scripts/validate_gh_aw_provider_registry.py` — success;
- `python scripts/validate_gh_aw_registry_extension.py` — success through the Registry validator;
- `python scripts/render_gh_aw_workers.py --all --check` — success;
- `python scripts/render_gh_aw_profile_surfaces.py --check` — success.

The same workflow's `cross-repo-control` job `93228862296` also completed successfully, preserving installation/bootstrap/plan/persist/re-plan behavior across the shared control action.

### Additional required PR-head workflows

On the validated implementation candidate:

- `Required PR Gate` run `31307009693`: **SUCCESS**.
- `Validate Public Runtime Distribution` run `31307009683`: **SUCCESS**.
- `Validate AI-SDLC gh-aw Worker Compile` run `31307009677`: **SUCCESS**.

The evidence-only head `2aa51a5440af38e76a5f713d0bb7a7ebde72c579` was also fully green:

- `Validate AI-SDLC protocol` run `31307059313`: **SUCCESS**.
- `Required PR Gate` run `31307059322`: **SUCCESS**.
- `Validate Public Runtime Distribution` run `31307059319`: **SUCCESS**.
- `Validate AI-SDLC gh-aw Worker Compile` run `31307059349`: **SUCCESS**.

The legal `IMPL-DONE` Event commit `526b99336d562da9863c2e398f005d72f973cb99` was likewise fully green before trusted persistence:

- `Validate AI-SDLC protocol` run `31307110256`: **SUCCESS**.
- `Required PR Gate` run `31307110257`: **SUCCESS**.
- `Validate Public Runtime Distribution` run `31307110232`: **SUCCESS**.
- `Validate AI-SDLC gh-aw Worker Compile` run `31307110237`: **SUCCESS**.

The worker compile results confirm the pinned strict gh-aw generated worker set remains compilable after the Registry refactor; no generated worker source/lock drift was introduced by this Feature.

### Static-preflight matrix

All registered profiles were exercised by `validate_gh_aw_runtime_preflight.py` through the successful Protocol run:

| Profile | Credential absent | Credential present | Entitlement claimed | Maturity compatibility |
| --- | --- | --- | --- | --- |
| copilot | MISSING_CREDENTIAL | READY_FOR_ENTITLEMENT_PROBE | false | reference |
| codex | MISSING_CREDENTIAL | READY_FOR_ENTITLEMENT_PROBE | false | reference |
| claude | MISSING_CREDENTIAL | READY_FOR_ENTITLEMENT_PROBE | false | reference |
| gemini | MISSING_CREDENTIAL | READY_FOR_ENTITLEMENT_PROBE | false | reference |
| deepseek | MISSING_CREDENTIAL | READY_FOR_ENTITLEMENT_PROBE | false | experimental |

Credential values were not passed to the Python preflight or recorded in artifacts/evidence; only boolean presence is used.

### Effective-model audit matrix

The current Registry contains one `openai-compatible` profile, `deepseek`. Generic audit verified the Registry model against all required surfaces:

| Profile | Registry model | rendered engine.model | COPILOT_MODEL | compiled agent_model | GH_AW_INFO_MODEL | GH_AW_ENGINE_MODEL |
| --- | --- | --- | --- | --- | --- | --- |
| deepseek | deepseek-chat | match | match | match | match | match |

The validator iterates all applicable compatible profiles, so a future compatible Registry entry enters the same audit path without a provider-name-specific Python branch.

### Synthetic extension / fail-closed evidence

The successful targeted validator chain proves:

- a digest-derived compatible provider/profile fixture is admitted through Registry metadata and generated artifacts without modifying generic production modules;
- generic module hashes remain unchanged during the fixture proof;
- provider/profile literal branches are rejected by the scoped AST guard;
- capability comparisons and the explicit test-only five-profile baseline remain accepted;
- malformed unrelated Registry entries fail the whole Registry before selecting an otherwise valid profile;
- duplicate worker/credential identities fail closed;
- path traversal and invalid provider URL forms fail closed;
- unknown profiles and unregistered worker workflows fail closed.

### Generated-artifact drift

Both deterministic drift checks passed in CI:

- worker source drift: clean;
- workflow profile/credential surface drift: clean.

## Lifecycle persistence result

The Developer proposed `EVT-F-GHAW-PROVIDER-REGISTRY-0001-IMPL-DONE` with `expected_revision: 11` after the required implementation validation was green.

Trusted `.github/workflows/ai-sdlc-persist.yml` consumed the Event and produced authoritative Feature revision `12` with:

- `implementation: DONE`;
- `implementation-v1` registered as a draft implementation artifact;
- `code-review: READY`;
- `code-gate: PENDING`;
- `workflow.current_stage: code-review`.

The Developer did not directly modify the Feature Manifest. The immediate bot-origin Persist commit produced GitHub `action_required` PR workflow entries with no jobs, rather than test failures; the preceding Event commit was fully green. This final evidence update creates a normal user-origin head so the PR can receive a normal final CI run before independent review.

## Known limitations / follow-up boundaries

This Feature deliberately leaves live provider entitlement, quota/billing, current endpoint/model availability, rate-limit headroom, and multi-provider dogfood outside static validation. Future provider onboarding must follow the documented live entitlement probe and bounded dogfood sequence before maturity promotion.

Separate follow-up Features remain responsible for adding Qwen/GLM/MiniMax production profiles, autonomous Product/Architect/Reviewer/QA roles, and mixed-provider multi-role dogfood.

## Developer authority boundary

The Developer does not approve this implementation, PASS `code-gate`, perform independent Verification, merge, release, or directly edit the authoritative Feature Manifest.

Implementation is complete through trusted Persist. The next lifecycle owner is an independent Code Reviewer who must review PR `#196` and the approved Requirement/Design/Plan plus this Evidence before deciding `code-gate`.
