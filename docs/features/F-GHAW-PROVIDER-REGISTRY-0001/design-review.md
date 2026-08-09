# Design Review — F-GHAW-PROVIDER-REGISTRY-0001

## Verdict

PASS_WITH_NOTES

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1

The Design is feasible, satisfies the approved Requirement, resolves both Requirement Review MINOR findings, and is sufficiently bounded and testable for `design-gate` to PASS.

## Authoritative baseline reviewed

- Repository: `DREAM-XIN/ai-sdlc`
- Feature branch: `feature/F-GHAW-PROVIDER-REGISTRY-0001`
- Feature Issue: `#195`
- Manifest revision observed before review: `6`
- workflow.status: `ACTIVE`
- current_stage: `design-review`
- design: `DONE`
- design-review: `READY`
- design-gate: `PENDING`
- requirement-v1: `approved`
- design-v1: `draft`

`AGENTS.md` and `.ai-sdlc/project.yaml` are not present on the Feature branch, consistent with the prior Requirement Review. No project-specific constraints were inferred from absent files.

## Material reviewed

- Feature Manifest and bootstrap record
- Issue #195 and lifecycle handoff comments
- approved Requirement and Requirement Review Evidence
- current Design
- `docs/role-guide.md`
- `gates/review-rubrics.yaml`
- `profiles/standard-feature.yaml`
- `runtimes/gh-aw/engine-profiles.yaml`
- current renderer/resolver/preflight/effective-model/cross-repository allowlist validators
- current preflight and profile-dispatch workflows
- current trusted Feature Event / push-triggered Persist contract

## Design rubric assessment

### Requirement coverage — PASS

The Design traces every approved acceptance criterion to a concrete component and evidence strategy. It retains the explicit non-goals: no new production provider, no autonomous lifecycle roles, no target-controlled runtime identity, no default-profile or DeepSeek-maturity change, and no compiler/runtime replacement.

### Requirement Review MINOR-1 — RESOLVED

The Design makes `gh_aw_provider_registry.py` the sole authoritative full-Registry validation boundary. Renderer, Resolver, Preflight, cross-repository worker allowlisting, and Effective Model Audit consume immutable validated objects. The whole Registry validates before selection, so a malformed unrelated entry cannot become a routing, credential, model, or worker identity.

Consumer-consistency negative tests explicitly require representative consumers to reject the same malformed Registry before identity use. This is materially stronger than independent partial YAML parsing.

### Requirement Review MINOR-2 — RESOLVED

The synthetic extension proof uses a temporary workspace, digest-derived provider/profile identities, unchanged generic-module byte hashes, literal-absence checks, and an AST/static anti-special-case invariant. It exercises validation, rendering, resolution, preflight, effective-model audit, and exact worker allowlisting without modifying generic control modules or the production Registry.

This prevents a fixed fixture-provider branch from masquerading as registry-driven extensibility.

### Component boundaries and contracts — PASS

Registry validation, rendering, trusted resolution, compiled-lock inspection, static preflight, effective-model audit, workflow surface generation, and cross-repository allowlisting have separate responsibilities. No component adds provider HTTP, secret-value access, lifecycle persistence, Gate, merge, or release authority.

The full-Registry atomic validity decision is explicit and intentional. Runtime compatibility baselines are test-only and are not imported as routing authority.

### Credential strategy and security — PASS

The selected generated bounded mapping is appropriate for GitHub Actions. Registry metadata contains secret names only; generated workflow expressions reduce secret access to booleans; Python, command lines, outputs, artifacts, summaries, and durable evidence never receive secret values.

Credential aliases are generic trusted metadata used only to preserve existing behavior. HTTPS, no-userinfo, no query/fragment, exact hostname match, narrow identifiers, exact worker filenames, read-only workers, Safe Outputs, and target-command selector rejection remain explicit invariants.

### Fail-closed and authority boundaries — PASS

The failure matrix has no fallback to another profile, credential, model, or worker. Unknown profiles/workers, malformed Registry fields, source drift, missing/invalid locks, and model-metadata drift all fail closed.

Runtime App, Safe Output, Feature Event, optimistic revision, Gate, target isolation, branch/write, merge, and release authority are unchanged.

### Backward compatibility and migration — PASS

The five current profiles, worker mappings, model/version pins, credential behavior, `copilot` default, and DeepSeek `experimental` maturity are retained. Migration is phased: establish the shared boundary, migrate consumers, factor lock validation, generate workflow surfaces, add extension/negative proof, then document/evidence. Direct parsing outside the shared module becomes a deterministic invariant violation only after consumer migration.

### Deterministic test strategy — PASS_WITH_NOTE

Coverage includes Registry positive/negative cases, cross-consumer malformed-Registry rejection, generic preflight iteration, all-compatible-profile effective-model audit, workflow drift, command/trust boundaries, current-profile compatibility, and repository security workflows.

MINOR-1: The AST/static anti-special-case guard is intentionally ambitious. Implementation should scope it to semantically relevant provider/profile identity flows and publish positive and negative guard fixtures so capability constants and the explicit test-only compatibility baseline do not produce false positives. This is an implementation-quality note, not an unresolved architecture/security decision.

### Observability, risks, and alternatives — PASS

Static preflight JSON is non-secret, machine-readable Evidence and explicitly separates readiness from live entitlement/quota/billing/model/rate-limit claims. Risks of atomic Registry rejection, a high-value shared loader, generated YAML ownership, and duplicate test baselines have mitigations. Rejected alternatives are consistent with the Requirement.

## Gate recommendation

`design-gate`: PASS.

Approve `design-v1`, complete `design-review`, and make `plan` READY through trusted Feature Event/Persist. The next role is Orchestrator; implementation must not begin in this review context.
