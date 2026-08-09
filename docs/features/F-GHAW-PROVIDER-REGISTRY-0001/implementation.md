# Implementation Evidence — F-GHAW-PROVIDER-REGISTRY-0001

Feature: `F-GHAW-PROVIDER-REGISTRY-0001`

Issue: `#195`

Role: Implementation Developer

Status: **WORKING — candidate implementation assembled; CI evidence pending**

## Scope implemented

### WU-1 — Shared trusted Registry boundary

Added `scripts/gh_aw_provider_registry.py` as the authoritative full-Registry loader/validator.

The boundary now validates the complete Registry before selection, returns frozen normalized profile objects, rejects malformed root/profile schema, duplicate YAML mapping keys, invalid profile/provider/model/credential/path/URL/host metadata, duplicate worker/source/credential identities, and unknown profile/worker lookups.

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

## Validation planned / pending durable results

The candidate is wired into existing repository validators. Required evidence still to be recorded here after the Draft PR head is exercised:

- `python scripts/validate_gh_aw_provider_registry.py`
- `python scripts/render_gh_aw_workers.py --all --check`
- `python scripts/render_gh_aw_profile_surfaces.py --check`
- `python scripts/validate_gh_aw_registry_extension.py`
- `python scripts/validate_gh_aw_engine_profiles.py`
- `python scripts/validate_gh_aw_effective_model_metadata.py`
- `python scripts/validate_gh_aw_runtime_preflight.py`
- `python scripts/validate_gh_aw_command_boundary.py`
- gh-aw workflow/security validators;
- complete repository `Validate AI-SDLC protocol` CI;
- `Required PR Gate`.

Any failed required check blocks Implementation completion. This evidence must be updated with the exact candidate commit, PR, CI run links/results, and any remediation before proposing the Implementation DONE transition.

## Developer authority boundary

The Developer does not approve this implementation, PASS `code-gate`, perform independent Verification, merge, release, or directly edit the authoritative Feature Manifest.
