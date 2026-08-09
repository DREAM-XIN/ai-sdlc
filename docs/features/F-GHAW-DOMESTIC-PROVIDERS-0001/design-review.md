# Design Review — F-GHAW-DOMESTIC-PROVIDERS-0001

## Verdict

**REWORK**

Severity summary:

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0
- SUGGESTION: 0

The Design is directionally sound and resolves both Requirement Review MINORs, but one implementation-order defect prevents the proposed materialization sequence from working against the current trusted Registry boundary.

## Review basis

Reviewed independently against:

- approved Requirement `requirement-v1`;
- `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/requirement-review.md`;
- `docs/features/F-GHAW-DOMESTIC-PROVIDERS-0001/design.md`;
- `gates/review-rubrics.yaml` design dimensions;
- merged `scripts/gh_aw_provider_registry.py`;
- merged `scripts/render_gh_aw_workers.py`;
- merged `scripts/render_gh_aw_profile_surfaces.py`;
- merged `scripts/validate_gh_aw_engine_profiles.py`;
- merged `scripts/validate_gh_aw_runtime_preflight.py`;
- merged `scripts/validate_gh_aw_effective_model_metadata.py`;
- `.github/workflows/compile-gh-aw-worker.yml`;
- `.github/workflows/materialize-gh-aw-worker-lock.yml`.

## Requirement coverage

PASS — The Design covers the three exact provider profiles, existing-profile compatibility, generated workers/surfaces, strict compilation, effective-model audit, static-preflight semantics, command boundary, fail-closed behavior, documentation provenance, maturity constraints, and lifecycle/security authority boundaries.

## Requirement Review note closure

### RQ-MINOR-1

RESOLVED — Section 9 defines explicit evidence states:

- static certification passed;
- live entitlement not established;
- bounded dogfood not established;
- maturity remains experimental.

It also keeps static preflight non-networked and `entitlement_verified: false`.

### RQ-MINOR-2

RESOLVED — Sections 3 and 15 record source URLs and observation date, Qwen Beijing-region/key coupling, shared-domain rationale, the ban on target/workspace host overrides, and explicit reviewed migration semantics for endpoint/model changes.

## Architecture/security review

PASS — The proposed production path remains Registry-driven and capability-based. No provider-name-specific branch is proposed for Registry validation, worker rendering, runtime preflight, effective-model audit, or exact worker allowlisting. Target commands remain unable to select execution identities, and Safe Output/Event/Gate/merge/release authority is unchanged.

The proposed conversion of compile/materialization orchestration from raw Registry YAML identity reads to `gh_aw_provider_registry.load_registry()` is consistent with the trusted boundary and removes a remaining duplicate identity parser.

## Finding

### DR-MAJOR-1 — New generated worker sources cannot be materialized with the current Registry load mode

**Severity:** MAJOR

The Design sequence assumes that the implementation can first add Qwen/GLM/MiniMax Registry entries and then run `scripts/render_gh_aw_workers.py` to create their new `.md` worker sources.

That sequence is not executable with the current implementation:

1. `render_gh_aw_workers.py` starts with `registry = load_registry()`.
2. `load_registry()` defaults `require_source_files=True`.
3. `_validate_worker_source(...)` rejects a registered `worker_source` when the file does not already exist.
4. The new Qwen/GLM/MiniMax worker source files are precisely what the renderer is supposed to create.

Therefore a candidate Registry containing the three new profiles fails before the renderer reaches `materialize()`. The materialization workflow has the same bootstrap dependency because it calls `render_gh_aw_workers.py --all` after checkout.

This is a real generation deadlock, not a test-only concern. Hand-authoring placeholder worker sources would violate the deterministic-generation intent and create an unnecessary trusted surface.

### Required Design remediation

Architect must update the Design to define an explicit two-mode source-existence contract:

- **write/materialization mode:** load and fully validate Registry structure/identity/security metadata with `require_source_files=False`, then deterministically create/update registered worker sources;
- **check/read/execution mode:** continue using `require_source_files=True`, so every trusted routing/preflight/audit/allowlist consumer and renderer `--check` fails closed on a missing source.

The implementation should make this distinction local to deterministic materialization. It must not weaken the default `load_registry()` behavior or allow production consumers to select the relaxed mode.

Deterministic tests must prove both directions:

1. a valid new compatible profile whose source does not yet exist can be materialized in write mode;
2. the same missing source is rejected by normal Registry load and renderer `--check`/trusted consumers.

The Design should also state how workflow discovery obtains profile identities before generated sources exist. If compile discovery runs only after materialization, it may require normal source existence; if materialization discovery occurs before generation, it must use the bounded materialization load mode deliberately.

## Other design dimensions

- Component boundaries: PASS apart from DR-MAJOR-1.
- Contracts/interfaces: PASS apart from source-materialization mode being undefined.
- Failure handling: PASS for Registry/preflight/compile failures; source bootstrap needs remediation.
- Compatibility: PASS — legacy five-profile snapshot is correctly reframed as a subset assertion.
- Observability/evidence: PASS — provider-fact provenance and static/live status are durable.
- Migration/rollback: PASS — reviewed Registry removal/regeneration path is defined.
- Testability: REWORK only for the missing-source bootstrap case above.
- Risks/alternatives: PASS after the missing-source path is explicitly designed.

## Conclusion

The Design must be revised before `design-gate` can PASS. No Requirement change is required. Remediation is confined to the Architect role and should preserve the existing default fail-closed Registry semantics while giving the deterministic generator a narrowly scoped bootstrap/materialization mode.
