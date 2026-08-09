# Requirement Review — F-GHAW-PROVIDER-REGISTRY-0001

## Verdict

PASS_WITH_NOTES

- BLOCKER: 0
- MAJOR: 0
- MINOR: 2

The Requirement is sufficiently clear, scoped, security-bounded, and testable for `requirement-gate` to PASS and for Design to begin.

## Authoritative baseline reviewed

- Feature: `F-GHAW-PROVIDER-REGISTRY-0001`
- Feature Issue: `#195`
- Feature branch: `feature/F-GHAW-PROVIDER-REGISTRY-0001`
- Manifest revision observed before review: `2`
- workflow.status: `ACTIVE`
- current_stage: `requirement-review`
- requirement: `DONE`
- requirement-review: `READY`
- requirement-gate: `PENDING`
- requirement-v1: `draft`

`AGENTS.md` and `.ai-sdlc/project.yaml` were requested by the role guide but are not present in this control-plane repository on either the Feature branch or `main`; no project-specific constraints were inferred from missing files.

## Material reviewed

- `state/features/F-GHAW-PROVIDER-REGISTRY-0001.yaml`
- `state/bootstrap/F-GHAW-PROVIDER-REGISTRY-0001.yaml`
- `docs/features/F-GHAW-PROVIDER-REGISTRY-0001/requirement.md`
- Feature Issue `#195` and lifecycle handoff comment
- `profiles/standard-feature.yaml`
- `gates/review-rubrics.yaml`
- `docs/role-guide.md`
- `runtimes/gh-aw/engine-profiles.yaml`
- `scripts/render_gh_aw_workers.py`
- `scripts/resolve_gh_aw_engine.py`
- `scripts/validate_gh_aw_engine_profiles.py`
- `scripts/gh_aw_runtime_preflight.py`
- `scripts/validate_gh_aw_runtime_preflight.py`
- `scripts/validate_gh_aw_effective_model_metadata.py`
- `scripts/gh_aw_cross_repo_runtime.py`
- `.github/workflows/ai-sdlc-gh-aw-preflight.yml`
- `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml`

## Requirement rubric assessment

### Problem and goal — PASS

The Requirement identifies a concrete control-plane defect: renderer/registry behavior is already substantially generic, while validator, preflight, effective-model audit, and workflow selection surfaces still maintain provider/profile-specific enumerations. The desired outcome is explicitly registry-driven trusted extension, not merely code cleanup.

### Scope and non-goals — PASS

The scope cleanly separates the provider-registry/certification foundation from production onboarding of Qwen/GLM/MiniMax/Kimi and from autonomous Product/Architect/Reviewer/QA/Acceptance workers. The follow-up boundary is explicit and prevents this Feature from absorbing multi-provider dogfood or multi-role autonomy.

### Testability and acceptance criteria — PASS

The Acceptance Criteria are sufficient to prove the Feature behavior rather than only a refactor:

- registry-driven validation of applicable compatible profiles;
- no required provider-name-specific Python control branches;
- generic effective-model validation across all applicable registered compatible providers;
- non-invasive preflight with explicit entitlement limits;
- fail-closed unknown profile/unregistered worker behavior;
- target command injection protection;
- deterministic fixture-provider extension evidence;
- malformed profile rejection;
- backward-compatibility regression coverage;
- repository validation/security CI;
- documentation of the full certification path;
- unchanged lifecycle/Gate/merge/release authority.

The fixture requirement is especially important because it gives a falsifiable proof that a future compatible provider can be introduced through registry metadata and generated worker artifacts without requiring provider-name-specific Python routing/validation changes.

### Provider Registry / Renderer / Resolver / Preflight / Audit boundaries — PASS_WITH_NOTE

The Requirement establishes the correct high-level ownership:

- Registry: trusted profile/provider inventory and metadata authority.
- Renderer: deterministic materialization of provider-specific worker sources from the canonical worker/security contract.
- Resolver/routing: trusted profile identity resolves to a registered compiled worker; arbitrary target-selected identities remain forbidden.
- Preflight: static, non-invasive readiness only; credential presence never becomes entitlement/quota/model-availability evidence.
- Effective Model Audit: verifies provider-routing model pin and compiled runtime/audit metadata invariants for every applicable compatible registered profile.

The Design should make one boundary more explicit: runtime consumers such as `resolve_gh_aw_engine.py`, `gh_aw_runtime_preflight.py`, worker allowlisting, and effective-model audit should consume a common validated registry contract (or an equivalently deterministic validation boundary) rather than each independently loading partially validated YAML and indexing fields. A malformed registered entry must fail closed before it can become a routing/credential/worker identity.

Severity: MINOR. This is an implementable Design responsibility, not an unresolved Requirement decision.

### Credential handling and security — PASS

The Requirement does not leave a security decision open-ended. It requires:

- credentials to remain repository-secret references;
- secret values never to be serialized to Task Packages, logs, artifacts, or registry files;
- syntactic validation of credential identifiers;
- HTTPS provider endpoints without embedded credentials;
- exact `network_host` / `base_url` hostname matching;
- target Issue Comment commands to reject provider/model/profile/credential/worker selectors;
- trusted worker workflows to remain registry allowlisted;
- no direct provider HTTP calls in Commander/lifecycle/Gate/persistence paths.

It deliberately permits either a safely enforceable dynamic secret-resolution mechanism or a bounded trusted mapping generated from registry metadata. That is an acceptable Design choice because the required safety properties and deterministic validation obligations are already fixed by the Requirement.

### Fail-closed and authority preservation — PASS

The Requirement explicitly preserves unknown-profile and unregistered-worker rejection, canonical worker read-only behavior, Safe Outputs for GitHub writes, Feature Event persistence, optimistic revision semantics, Gate authority, Runtime App trust, and the prohibition on workers editing authoritative Feature state, self-approving Gates, merging, or releasing.

### Backward compatibility — PASS

The Requirement explicitly preserves `copilot`, `codex`, `claude`, `gemini`, and `deepseek`, keeps the default profile at `copilot`, and forbids DeepSeek maturity promotion from being inferred from this refactor.

## Review notes for Design / Verification

### MINOR-1 — Make registry validation a single trusted contract

Design should define where registry schema/capability validation is authoritative and how Resolver, Renderer, Preflight, worker allowlisting, and Effective Model Audit consume it. A malformed registered entry must fail closed consistently across every trusted runtime path.

### MINOR-2 — Make the extension proof resistant to false positives

The deterministic fixture test should demonstrate that a synthetic OpenAI-compatible provider can be introduced by changing fixture registry metadata/generated worker inputs only (or an equivalent controlled setup) while the generic Python control logic remains unchanged. This prevents a test from passing merely because provider-specific branches were added before the fixture was executed.

## Gate recommendation

`requirement-gate`: PASS.

The Requirement may be approved. `requirement-review` may complete and `design` may become READY through the trusted Feature Event/Persist path.
