# Plan — F-GHAW-DOMESTIC-PROVIDERS-0001

## Objective

Implement the approved Design for Qwen, GLM, and MiniMax as trusted experimental OpenAI-compatible gh-aw profiles without provider-name-specific production control branches and with deterministic static certification evidence.

## Work units

### WU-1 — Registry entries and bounded materialization mode

Owner: Developer

Changes:

- add Qwen, GLM, and MiniMax Registry entries exactly as approved;
- keep default `load_registry()` source-existence validation strict;
- change deterministic renderer write mode only to load with `require_source_files=False` before generating new registered sources;
- keep renderer `--check` on normal strict source-existence validation;
- add deterministic positive/negative missing-source materialization tests.

Acceptance:

- no generic runtime/read/preflight/audit/allowlist caller can select relaxed source validation;
- malformed Registry metadata still fails in materialization mode;
- newly registered absent source can be generated, then normal validation passes;
- deleting a source makes normal consumers fail closed.

### WU-2 — Generated worker/profile surfaces

Owner: Developer

Changes:

- deterministically generate Qwen/GLM/MiniMax worker sources;
- regenerate preflight/dispatch profile options and boolean credential-presence blocks;
- do not hand-author generated source deltas.

Acceptance:

- renderer `--all --check` passes;
- profile-surface `--check` passes;
- secret values are never serialized or echoed.

### WU-3 — Legacy compatibility assertions become open-ended

Owner: Developer

Changes:

- convert five-profile exact-set assertions in `validate_gh_aw_engine_profiles.py` and `validate_gh_aw_runtime_preflight.py` into legacy subset assertions;
- keep exact old mappings/maturity assertions for existing five profiles;
- derive generic preflight expected identity from validated `EngineProfile` for every Registry profile.

Acceptance:

- legacy five profiles cannot silently drift;
- new compatible profiles need no provider-specific generic validator branches.

### WU-4 — Registry-derived compile/materialization orchestration

Owner: Developer

Changes:

- make `compile-gh-aw-worker.yml` discover matrix profiles from normal validated Registry;
- resolve selected worker source/lock through shared Registry helper rather than raw YAML;
- broaden provider-worker path filters to generic worker source pattern;
- align `materialize-gh-aw-worker-lock.yml` identity enumeration with shared Registry helper;
- use bounded materialization Registry load only before generated source creation, then normal validation afterward.

Acceptance:

- final PR compile matrix is derived from Registry and strict-compiles every profile;
- no provider-name list controls compile eligibility;
- pinned compiler remains `v0.83.4`.

### WU-5 — Compiler-generated lock artifacts

Owner: Developer / trusted materialization workflow

Changes:

- materialize all workers on a bounded `gh-aw/compile-*` branch;
- obtain Qwen/GLM/MiniMax `.lock.yml` strictly from gh-aw compiler output;
- transfer exact compiler-generated artifacts to the Feature branch;
- never hand-edit lock contents.

Acceptance:

- new lock files exist and match Registry engine/model identities;
- existing lock compatibility remains green.

### WU-6 — Provider certification documentation

Owner: Developer

Changes:

- update OpenAI-compatible provider documentation with the three direct profiles;
- record official source URLs and observation date 2026-08-09;
- record Qwen Beijing key/region coupling and shared-domain decision;
- state per provider: static certification status, live entitlement status, bounded dogfood status, maturity.

Acceptance:

- no static evidence is described as live entitlement;
- all three remain experimental;
- Kimi and maturity promotion remain out of scope.

### WU-7 — Integrated implementation verification

Owner: Developer

Run/collect:

- Registry validation and synthetic extension;
- missing-source materialization positive/negative tests;
- renderer and generated-surface drift checks;
- effective-model audit;
- runtime-preflight validation;
- command-boundary/security validation;
- cross-repository/runtime security regressions;
- public runtime distribution;
- Required PR Gate;
- Registry-derived strict compile matrix.

Record durable Implementation Evidence with exact candidate SHA and CI run ids.

## Implementation order

1. WU-1 first, because new Registry entries cannot be safely materialized until source bootstrapping is implemented.
2. WU-3 and WU-4 next, removing closed-set test/CI assumptions before adding final provider artifacts.
3. Add the three Registry entries.
4. WU-2 generate source/workflow surfaces.
5. WU-5 materialize compiler locks on bounded compile branch.
6. WU-6 documentation.
7. WU-7 final candidate verification.

## Review focus carried forward

Code Reviewer must explicitly verify `DR2-MINOR-1`: relaxed `require_source_files=False` is structurally limited to deterministic materialization/write bootstrap paths and cannot be selected by normal resolver, preflight, effective-model audit, cross-repository allowlisting, runtime routing, or target inputs.

Reviewer must also verify:

- no new provider-name-specific production Python branches;
- no direct provider HTTP calls in static/control paths;
- Qwen/GLM/MiniMax facts match approved Requirement/Design;
- legacy profile mappings/default remain stable;
- generated locks are compiler output;
- static/live/dogfood terminology remains truthful.

## Lifecycle boundary

Orchestrator produces this Plan only. Implementation Developer may implement WU-1 through WU-7, but may not self-PASS `code-gate`, perform independent Verification, PASS `release-gate`, or merge/release before the corresponding independent stages complete.
