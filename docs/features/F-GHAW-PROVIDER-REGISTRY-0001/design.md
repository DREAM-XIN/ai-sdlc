# Design — Trusted gh-aw Provider Registry and Certification Foundation

Feature: `F-GHAW-PROVIDER-REGISTRY-0001`

Issue: `#195`

Profile: `standard-feature`

Role: Design Architect

Requirement: `requirement-v1` (`approved`)

## 1. Context and design goal

The approved Requirement establishes one narrow goal: make the gh-aw trusted provider/profile extension path registry-driven, deterministic, and fail-closed so a future OpenAI-compatible provider can be added through trusted registry metadata plus generated/compiled worker artifacts without adding provider-name-specific Python validation or routing branches.

This Design does **not** add a production provider, autonomous lifecycle roles, or mixed-provider dogfood. It preserves the current control-plane authority model, Safe Outputs, Feature Event persistence, optimistic revisions, Gate authority, Runtime App trust, exact-target cross-repository routing, branch protection, merge authority, and release authority.

The current implementation already has a mostly generic renderer, but trusted consumers do not share one validated registry boundary:

- `render_gh_aw_workers.py` performs its own partial registry and OpenAI-compatible validation;
- `resolve_gh_aw_engine.py` directly loads raw YAML and indexes selected fields;
- `gh_aw_runtime_preflight.py` directly loads raw YAML;
- `gh_aw_cross_repo_runtime.py` builds an allowlist from partially checked raw YAML;
- `validate_gh_aw_engine_profiles.py` maintains a fixed `EXPECTED` profile map;
- `validate_gh_aw_runtime_preflight.py` maintains a fixed `PROFILES` tuple and special-cases DeepSeek assertions;
- `validate_gh_aw_effective_model_metadata.py` is DeepSeek-only;
- preflight/profile-dispatch workflows enumerate profile choices and credential-selection cases manually.

The architecture therefore centers on a single trusted validated registry contract and makes every downstream identity consumer depend on that contract.

## 2. Architecture overview

```text
                  trusted repository metadata
        runtimes/gh-aw/engine-profiles.yaml
                         |
                         v
        +------------------------------------+
        | gh_aw_provider_registry.py         |
        | load + schema + semantic +         |
        | uniqueness + security validation   |
        +------------------------------------+
                         |
              immutable validated objects
                         |
       +-----------------+-------------------+------------------+
       |                 |                   |                  |
       v                 v                   v                  v
   Renderer          Resolver           Preflight       Worker allowlist
       |                 |                   |                  |
       |                 |                   +----+-------------+
       |                 |                        |
       v                 v                        v
 rendered worker   trusted workflow        compiled-lock
 source             identity              validation helper
       |                                      |
       +-------------------+------------------+
                           |
                           v
                 Effective Model Audit
                           |
                           v
                 deterministic evidence

Registry + runtime config
          |
          v
profile-surface generator
          |
          +--> workflow_dispatch choice blocks
          +--> bounded credential-presence mapping
          |
          v
      --check drift validation
```

The Registry remains trusted metadata. The new loader/validator is the **authoritative runtime validation boundary**. Raw YAML is not a routing identity, credential identity, model identity, or worker identity until the whole Registry validates successfully.

A malformed entry invalidates the Registry for every trusted consumer, even if a different profile was requested. This intentionally prevents one path from ignoring an invalid entry while another path admits it into an allowlist or audit set.

## 3. Core design decisions

### D1 — Introduce one shared validated Registry module

Add a shared module, proposed as:

`scripts/gh_aw_provider_registry.py`

It owns all production parsing and validation of `runtimes/gh-aw/engine-profiles.yaml`.

Proposed public API:

```python
load_registry(path=DEFAULT_REGISTRY) -> ProviderRegistry
ProviderRegistry.require_profile(profile_id) -> EngineProfile
ProviderRegistry.profile_ids() -> tuple[str, ...]
ProviderRegistry.openai_compatible_profiles() -> tuple[EngineProfile, ...]
ProviderRegistry.trusted_worker_workflows() -> Mapping[str, EngineProfile]
```

The returned objects are immutable/typed normalized values rather than raw dictionaries. A minimal implementation may use frozen dataclasses.

No trusted consumer listed in this Feature should call `yaml.safe_load()` on `engine-profiles.yaml` directly after migration. Tests may load fixture YAML through the same shared loader.

Validation errors use one deterministic exception family, e.g. `RegistryValidationError`. CLI wrappers convert that to a concise error and non-zero exit without a Python traceback containing untrusted/raw object dumps.

### D2 — Validate the entire Registry before selection

`load_registry()` validates:

1. root/schema shape;
2. every profile's common fields;
3. protocol-specific fields;
4. URL/hostname/model/credential/path syntax;
5. global uniqueness constraints;
6. normalized worker and credential indexes.

Only after all checks pass can `require_profile()` resolve a selected id.

This means:

- unknown selected profile → fail closed;
- known selected profile + malformed unrelated registered profile → fail closed;
- duplicated worker identity anywhere → fail closed;
- malformed credential/URL/model/path anywhere → fail closed.

This directly resolves Requirement Review MINOR-1.

### D3 — Provider extension branches on capabilities, not provider names

Generic logic may branch on trusted capability values such as:

- `protocol == "openai-compatible"`;
- `engine == "copilot"` for the currently supported BYOK adapter;
- `provider_type == "openai"`;
- `wire_api in {"completions", "responses"}`;
- maturity semantics.

Generic logic must not branch on `provider == "deepseek"`, a future provider name, or a profile id to decide compatible-provider validation, preflight, audit, or allowlisting behavior.

Native engine support is not being generalized into an arbitrary engine-plugin system by this Feature. A bounded native-engine capability set may remain because adding a new native gh-aw engine is a different extension problem. The prohibition is specifically against provider/profile-name-specific compatible-provider control logic.

### D4 — Choose generated bounded credential mapping, not runtime dynamic secret lookup

This Design selects the Requirement's **Option B**.

The trusted Registry continues to store only credential names/references. A deterministic generator derives the preflight workflow's bounded credential-presence mapping from validated Registry metadata. The workflow checks only booleans such as `${{ secrets.NAME != '' }}`; secret values are never passed to Python, shell arguments, artifacts, summaries, Task Packages, or registry files.

This choice favors an explicit, reviewable and drift-checkable secret boundary over relying on dynamic secret-name indirection at runtime.

### D5 — Workflow profile choices are generated presentation metadata, not authority

GitHub Actions `workflow_dispatch` choice lists are a presentation layer. They may contain the current profile names, but they do not define trust.

The authoritative path remains:

`input profile id -> shared validated Registry -> exact registered profile -> exact registered worker workflow`

Choice blocks in:

- `.github/workflows/ai-sdlc-gh-aw-preflight.yml`
- `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml`

will be generated or checked from the Registry so they cannot silently drift.

### D6 — Synthetic extension evidence must be resistant to provider-specific test cheating

The fixture-provider proof will not use a fixed provider name that can be pre-special-cased in generic control logic. The test derives one or more synthetic profile/provider ids from a digest of the generic control modules under test, injects those ids only into temporary fixture registry/workspace data, and verifies the generic modules are byte-identical before and after the fixture pipeline.

In addition, an AST/static guard rejects provider/profile-name comparisons or dispatch cases in the generic modules under test. Capability comparisons remain allowed.

This resolves Requirement Review MINOR-2.

## 4. Validated Registry contract

### 4.1 Root contract

```yaml
version: 0.1.0
profiles:
  <profile-id>:
    ...
```

Rules:

- root must be a mapping;
- `version` is required and must equal the supported Registry contract version;
- `profiles` is required, must be a non-empty mapping;
- unknown root fields are rejected unless intentionally introduced by a future Registry-version change;
- profile order is preserved for deterministic generated UI presentation but must not affect trust semantics.

### 4.2 Profile id

Profile ids are trusted identifiers and must follow a narrow slug form, proposed:

`^[a-z][a-z0-9-]*$`

They are unique by YAML mapping definition and are retained exactly as registered. No case folding is used for identity resolution.

### 4.3 Common required fields

Every profile requires:

| Field | Semantics |
| --- | --- |
| `engine` | gh-aw engine/adapter identity |
| `worker_source` | trusted canonical or rendered `.md` source under `.github/workflows/` |
| `worker_workflow` | trusted compiled lock workflow filename |
| `credential` | canonical repository secret reference name |
| `maturity` | trusted evidence/maturity classification |

Optional common fields:

- `engine_version` — pinned engine version where the profile currently requires it;
- `model` — native pinned model where applicable, required for OpenAI-compatible profiles;
- `credential_aliases` — optional compatibility aliases for an existing credential contract.

`credential_aliases` is introduced only if required to preserve an existing accepted credential fallback, notably the current Codex preflight behavior. It is generic metadata and is not provider-specific logic.

### 4.4 Worker identity rules

`worker_source`:

- repository-relative only;
- must be under `.github/workflows/`;
- must end in `.md`;
- no absolute path, `..`, empty segments, or backslash traversal;
- must exist for the production Registry during production validation;
- must be globally unique.

`worker_workflow`:

- filename only, no directory separator;
- must match a bounded workflow filename syntax such as `^[A-Za-z0-9._-]+\.lock\.ya?ml$`;
- must be globally unique;
- production validation requires the lock to exist where the consumer requires an installed compiled worker;
- the source/lock basename relationship is checked when it is deterministic for gh-aw compilation.

The canonical worker source may be referenced by exactly one profile because `worker_source` is unique. Renderer behavior identifies the canonical source by path/contract, not by a provider name.

### 4.5 Credential rules

Canonical `credential` and any `credential_aliases`:

- must be strings;
- use the repository convention `^[A-Z][A-Z0-9_]*$`;
- must not use the reserved `GITHUB_` prefix;
- contain a secret **name only**, never a value;
- must be globally unambiguous across profiles/aliases unless a future Registry version deliberately defines credential sharing semantics.

The current profiles remain mapped to their existing credential contract. If `CODEX_API_KEY` compatibility is preserved as an alias to the current Codex primary reference, that alias becomes explicit trusted metadata rather than hidden workflow-only behavior.

### 4.6 Maturity rules

For Registry version `0.1.0`, supported maturity values remain the currently used bounded classifications:

- `reference` — established reference profile in the current project baseline;
- `experimental` — profile is registered/compiled and may have bounded evidence, but static compatibility is not maturity-promotion evidence.

This Feature does not introduce a new maturity state and does not change any existing value. In particular, DeepSeek remains `experimental`.

Maturity is metadata used for reporting/admission policy; it never grants lifecycle authority.

### 4.7 Native profile contract

A native profile has no `protocol: openai-compatible` capability.

Rules:

- `engine` must be a supported native gh-aw engine for this Registry version;
- `engine_version`, when present, uses the existing pinned semver syntax;
- `model`, when present, uses the common model-id syntax;
- OpenAI-compatible-only fields (`base_url`, `network_host`, `provider_type`, `wire_api`) must not be interpreted for a native profile;
- native profile onboarding outside the existing supported engines is out of scope for this Feature.

The existing `copilot`, `codex`, `claude`, and `gemini` behaviors remain compatible.

### 4.8 OpenAI-compatible profile contract

An applicable compatible profile declares:

```yaml
engine: copilot
protocol: openai-compatible
provider: <trusted-provider-id>
provider_type: openai
wire_api: completions | responses
base_url: https://...
network_host: <exact-hostname>
model: <model-id>
credential: <SECRET_NAME>
worker_source: .github/workflows/<worker>.md
worker_workflow: <worker>.lock.yml
maturity: experimental | reference
```

Rules:

- `provider` is required, non-empty, and uses a narrow provider-id syntax;
- current gh-aw OpenAI-compatible BYOK profiles require `engine: copilot`;
- `provider_type` is required and must be `openai` for this protocol version;
- `wire_api` is required and must be `completions` or `responses`;
- `model` is required and must match the shared model-id syntax;
- `base_url` is required and must be absolute HTTPS;
- URL userinfo/embedded credentials are forbidden;
- URL query and fragment are forbidden; a path is allowed so compatible providers that require a versioned API base remain representable;
- `network_host` is required, must be a syntactically valid host value, and must exactly equal the parsed base URL hostname after the loader's documented normalization;
- credentials follow the common secret-name contract.

No rule refers to `deepseek` or any future provider name.

## 5. Component responsibilities and interfaces

### 5.1 `gh_aw_provider_registry.py` — authoritative Registry boundary

Owns:

- YAML load;
- root/schema validation;
- profile normalization;
- common validation;
- protocol/capability validation;
- URL/host/credential/model/path validation;
- uniqueness checks;
- profile lookup;
- compatible-profile iteration;
- exact worker-workflow index.

Does not own:

- worker source rendering;
- compilation;
- secret value access;
- provider HTTP calls;
- lifecycle state;
- Gate decisions.

All Registry consumers use this module.

### 5.2 `render_gh_aw_workers.py` — deterministic source materialization

Renderer responsibilities remain narrow:

1. load a validated profile through the shared Registry module;
2. read the canonical gh-aw worker source;
3. preserve the canonical lifecycle/security content exactly;
4. replace only the engine/provider frontmatter material owned by the profile contract;
5. write the registered `worker_source` for derived profiles;
6. support a deterministic check mode that compares committed generated source to expected output without rewriting it.

For `protocol: openai-compatible`, frontmatter derives from validated metadata:

- `engine.id = copilot`;
- `engine.model = registry.model`;
- `COPILOT_PROVIDER_BASE_URL = registry.base_url`;
- `COPILOT_MODEL = registry.model`;
- `COPILOT_PROVIDER_API_KEY = secrets.<registry.credential>`;
- `COPILOT_PROVIDER_TYPE = registry.provider_type`;
- `COPILOT_PROVIDER_WIRE_API = registry.wire_api`;
- network allowlist includes only the validated `network_host` in addition to existing defaults.

Renderer must not modify lifecycle instructions, target identity, Safe Output configuration, protected files, result dispatch, Gate restrictions, merge/release restrictions, or Feature state authority.

### 5.3 `resolve_gh_aw_engine.py` — trusted profile resolution only

Resolver becomes a thin CLI over the shared Registry:

```text
trusted profile id
  -> load and validate entire Registry
  -> require exact profile
  -> return registered engine/provider/protocol/model/worker/credential/maturity
```

Unknown profile returns non-zero. Malformed Registry returns non-zero. It never accepts provider, model, credential, or worker override flags.

The existing default-profile choice remains in `runtimes/gh-aw/runtime.yaml`; this Feature keeps it as `copilot`.

### 5.4 Compiled lock validation helper

Preflight and Effective Model Audit currently parse/check compiled metadata separately. To avoid a second divergence point, introduce or factor a small shared helper, proposed as:

`scripts/gh_aw_compiled_worker.py`

Responsibilities:

- locate the registered lock file under the trusted workflow directory;
- parse the `# gh-aw-metadata:` line deterministically;
- verify `strict: true`;
- verify the pinned compiler version remains the reviewed project version;
- verify the expected metadata schema version;
- return normalized compiled metadata;
- never execute the lock or call a provider.

This helper is not a Registry authority; it validates the artifact produced from an already validated profile.

### 5.5 `gh_aw_runtime_preflight.py` — static readiness only

Preflight flow:

1. load the whole validated Registry;
2. resolve exact selected profile;
3. locate/validate its exact registered compiled lock through the lock helper;
4. receive a boolean `credential_present` from the trusted workflow layer;
5. emit normalized static status.

Output retains at least:

- `profile`;
- `engine`;
- `provider`;
- `protocol`;
- `model`;
- `maturity`;
- `credential` name/reference;
- `worker_workflow`;
- compiler/strict-lock metadata;
- `credential_present`;
- `entitlement_verified: false`.

Statuses remain fail-closed and non-invasive:

- `UNKNOWN_PROFILE` / registry failure — not ready, non-zero;
- `MISSING_LOCK` — not ready, non-zero;
- `INVALID_LOCK` — not ready, non-zero;
- `MISSING_CREDENTIAL` — statically not ready, but may remain a reportable non-error process result if existing workflow behavior depends on it;
- `READY_FOR_ENTITLEMENT_PROBE` — static prerequisites are present only.

`READY_FOR_ENTITLEMENT_PROBE` never means subscription, entitlement, quota, billing, model availability, or rate-limit capacity has been verified.

No provider HTTP call is added to preflight.

### 5.6 `validate_gh_aw_runtime_preflight.py` — generic regression/security validator

The validator removes the fixed production `PROFILES` tuple as an execution authority. It iterates validated Registry profiles for generic preflight assertions.

Provider/profile-specific assertions are moved into explicit backward-compatibility fixture/snapshot tests rather than generic control logic.

It continues to validate:

- missing credential → non-ready;
- present credential + valid lock → `READY_FOR_ENTITLEMENT_PROBE`;
- entitlement always false in static preflight;
- read-only preflight workflow permissions;
- no mutation/dispatch/state-write behavior;
- exact cross-repo repository identity parsing;
- exact registered worker allowlisting;
- target command surface rejects runtime identity selectors;
- Runtime App configuration fails before token mint where applicable.

### 5.7 `validate_gh_aw_effective_model_metadata.py` — all compatible profiles

The Effective Model Audit loads the validated Registry and iterates every profile with `protocol: openai-compatible`.

For each applicable profile, it verifies one authoritative model pin across all required surfaces:

1. Registry `model`;
2. rendered worker `engine.model`;
3. rendered provider routing `COPILOT_MODEL`;
4. compiled lock run/audit metadata model, currently `GH_AW_INFO_MODEL`;
5. compiled lock engine/telemetry model, currently `GH_AW_ENGINE_MODEL`.

The audit compares every surface directly to the Registry model. It must not depend on a hard-coded Copilot fallback model string and must not contain a DeepSeek-only source/lock path.

Any missing or mismatched surface fails the audit for that profile.

### 5.8 `gh_aw_cross_repo_runtime.py` — exact worker allowlisting

`trusted_worker_workflows()` is replaced by/shared with the Registry's validated worker index.

The flow becomes:

```text
worker_workflow input from trusted internal gateway
  -> syntax check
  -> load/validate entire Registry
  -> exact worker_workflow lookup
  -> accept only exact registered identity
```

An invalid Registry, malformed worker path, duplicate registered worker, or unknown workflow fails closed.

The target repository still never selects this workflow through Issue Comment syntax. It reaches the cross-repository gateway only after trusted profile resolution in the control plane.

### 5.9 `validate_gh_aw_engine_profiles.py` — contract/invariant validator, not inventory authority

The existing fixed `EXPECTED` runtime inventory is split into two concerns:

1. **generic production contract checks** — driven by the shared Registry;
2. **explicit backward-compatibility baseline checks** — test-only fixture/snapshot data for the five existing profiles.

This allows runtime identity to remain Registry-driven while still proving that the current mappings did not drift accidentally.

The validator continues to verify canonical worker security markers and deterministic rendered-source drift, but it consumes validated profiles rather than raw dictionaries.

## 6. Credential strategy

### 6.1 Selected strategy: generated bounded mapping

Add a deterministic generator/checker, proposed as:

`scripts/render_gh_aw_profile_surfaces.py`

It consumes only:

- the validated Registry;
- `runtimes/gh-aw/runtime.yaml` for the trusted default profile;
- workflow templates/marker blocks owned by this generator.

It deterministically produces/checks:

1. profile `choice` options for gh-aw preflight;
2. profile `choice` options for gh-aw profile dispatch;
3. the bounded credential-presence expressions for every registered profile;
4. the profile-to-credential-presence selection block.

The generated block may visibly enumerate profile ids and secret names because GitHub Actions YAML must ultimately reference bounded trusted secrets. That enumeration is **derived presentation/secret plumbing**, not independent identity logic.

### 6.2 Secret-value boundary

The workflow must evaluate only credential presence and pass a boolean to `gh_aw_runtime_preflight.py`.

Forbidden:

- printing a secret value;
- serializing a value into preflight JSON;
- writing a value to `$GITHUB_OUTPUT`;
- passing a value on a command line;
- storing a value in an uploaded artifact;
- putting a value in Registry or generated source committed to git.

Allowed durable data:

- credential reference/name;
- `credential_present: true|false`;
- the static readiness status.

### 6.3 Credential alias compatibility

If an existing profile currently accepts multiple repository secret names for presence detection, preserve that behavior through generic Registry metadata such as `credential_aliases` rather than a manual profile-specific shell branch.

The generated workflow may produce a boolean OR over the primary and aliases. The Python Registry/Preflight logic continues to see one profile and a boolean; it does not learn secret values.

### 6.4 Drift validation

`render_gh_aw_profile_surfaces.py --check` must fail when:

- Registry profile order/set differs from workflow choices;
- a registered credential is missing from the generated presence block;
- a stale removed profile remains in generated blocks;
- workflow default differs from `runtime.yaml` default;
- generated profile/credential block content differs byte-for-byte from expected deterministic output.

This is required CI evidence for any future provider registration.

## 7. Workflow selection and authority boundary

There are three distinct identity surfaces:

### 7.1 Trusted manual/control-plane profile input

`.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` may expose `engine_profile` to a trusted control-plane operator. Its choice list is generated from the Registry.

This input is not trusted merely because it appears in a choice list. The resolver still validates it against the Registry.

### 7.2 Target Issue Comment command

The target command remains exactly bounded to Feature location, e.g. target ref and manifest path. It must not add:

- `provider`;
- `model`;
- `engine_profile`;
- `credential`;
- `worker_workflow`;
- equivalent aliases that let the target choose runtime identity.

A deterministic security validator continues to inspect the installed command template and fail if these selectors are introduced.

### 7.3 Internal worker handoff

The resolved `worker_workflow` may be handed from the trusted profile gateway to same-repo/cross-repo runtime workflows as an internal field. The receiving gateway must validate that exact workflow against the validated Registry before dispatch.

Thus even an internal malformed or stale handoff fails closed.

## 8. Fail-closed behavior matrix

| Condition | Result |
| --- | --- |
| Registry root/version invalid | all Registry consumers fail |
| any registered profile malformed | whole Registry rejected before selection |
| unknown profile id | resolver/preflight fail |
| duplicate worker source/workflow | whole Registry rejected |
| invalid worker filename/path | whole Registry rejected |
| unknown worker workflow | cross-repo worker validation fails |
| invalid credential reference syntax | whole Registry rejected |
| invalid/HTTP/credential-bearing compatible URL | whole Registry rejected |
| `network_host` mismatch | whole Registry rejected |
| unsupported compatible `wire_api` | whole Registry rejected |
| rendered worker drift | deterministic validation fails |
| lock missing | preflight not ready |
| lock metadata invalid/unpinned/non-strict | preflight not ready |
| credential missing | `MISSING_CREDENTIAL`, not ready |
| credential present | only `READY_FOR_ENTITLEMENT_PROBE` |
| model differs on source/route/lock/audit surface | Effective Model Audit fails |
| target attempts runtime identity selector | command parser/security validator rejects |

No failure path falls back to another profile, another credential, another model, or an unregistered worker.

## 9. Backward compatibility

The implementation must retain the following production profile ids and their current behavior:

- `copilot`;
- `codex`;
- `claude`;
- `gemini`;
- `deepseek`.

Compatibility requirements:

- `runtimes/gh-aw/runtime.yaml` default remains `copilot`;
- existing worker source and compiled workflow identities remain unchanged unless a deterministic renderer normalization produces byte-equivalent behavior and is explicitly reviewed;
- existing engine/model pins remain unchanged;
- existing credential behavior is preserved, including any accepted compatibility alias modeled explicitly in Registry metadata;
- DeepSeek remains `experimental`;
- current canonical worker security markers remain unchanged;
- cross-repository Runtime App and exact-target behavior remain unchanged;
- no target command gains runtime identity selectors.

To prove compatibility without turning tests into runtime authority, add a test-only baseline fixture containing the expected current mappings. Runtime code never imports this fixture.

## 10. Migration strategy

Migration is intentionally incremental so a failed refactor cannot silently widen trust.

### Phase 1 — Shared Registry contract

- add `gh_aw_provider_registry.py`;
- add positive/negative Registry contract tests;
- keep production Registry values unchanged except an explicit credential alias field if required to preserve current behavior.

### Phase 2 — Migrate consumers

Move, in bounded commits where practical:

- renderer;
- resolver;
- engine profile validator;
- runtime preflight;
- cross-repo worker allowlist;
- Effective Model Audit;

to consume validated objects.

After this phase, direct production parsing of `engine-profiles.yaml` outside the shared Registry module is rejected by a deterministic source/invariant test.

### Phase 3 — Shared lock validation

Factor strict compiled-lock metadata validation used by preflight/audit into the lock helper without changing the pinned compiler/runtime dependency.

### Phase 4 — Generated workflow surfaces

- add profile-surface generator/check mode;
- regenerate the two profile choice blocks;
- regenerate bounded credential-presence mapping;
- preserve `copilot` default;
- add drift validation.

### Phase 5 — Synthetic extension and malformed-profile proof

Add temp-workspace fixture pipeline and anti-special-case guard described below.

### Phase 6 — Documentation/evidence

Update provider integration docs with the certification sequence and security/authority boundary. Do not register a new production provider.

## 11. Deterministic test and evidence strategy

### 11.1 Registry contract tests

Positive tests:

- all current production profiles validate;
- native model/version forms validate;
- OpenAI-compatible `completions` and `responses` capability forms validate;
- compatible base URL path is allowed with exact host match;
- optional credential alias syntax validates when used.

Negative tests include at least:

- wrong Registry version/root shape;
- empty/non-mapping profiles;
- invalid profile id;
- missing common field;
- invalid maturity;
- path traversal/absolute worker source;
- invalid or duplicate worker workflow;
- duplicate worker source;
- invalid/ambiguous credential or alias;
- malformed model;
- unknown protocol;
- compatible profile with wrong engine;
- missing provider/provider_type/wire_api;
- unsupported wire API;
- HTTP URL;
- URL with embedded username/password;
- URL with query/fragment;
- invalid hostname;
- `network_host` mismatch.

For every malformed case, all representative consumers must fail rather than only the Registry unit test.

### 11.2 Consumer-consistency tests

A malformed Registry fixture is passed to shared APIs used by:

- resolver;
- renderer/check mode;
- preflight;
- worker allowlist;
- Effective Model Audit.

The expected result is consistent fail-closed rejection before any selected identity is used.

This is the concrete evidence for MINOR-1.

### 11.3 Synthetic OpenAI-compatible provider proof

The fixture proof runs entirely in a temporary workspace and does not edit the production Registry.

Proposed algorithm:

1. define the generic control-module set under test, including Registry loader, renderer, resolver, preflight, Effective Model Audit, and cross-repo worker allowlisting;
2. hash the exact module bytes to `control_digest_before`;
3. derive synthetic profile/provider ids from that digest, for example `fixture-<digest-prefix>`; optionally derive two ids to exercise iteration rather than a single special case;
4. assert those synthetic ids do not occur as literals in the generic control modules;
5. create a temporary Registry by extending a valid baseline with the synthetic compatible profile metadata;
6. use only public/shared generic functions to validate and resolve it;
7. render its worker source in the temporary workspace;
8. create/compile the corresponding deterministic test lock artifact through the same lock-metadata contract used by preflight/audit; production strict-compile tests remain separate evidence for real registered workers;
9. run generic preflight with credential absent and present;
10. run Effective Model Audit and verify Registry model == rendered engine model == provider routing model == lock/audit models;
11. run exact worker allowlisting for the synthetic registered worker and reject an unregistered worker;
12. recompute the generic control-module digest and require `control_digest_after == control_digest_before`;
13. ensure no generic module was written by the test.

To prevent a contributor from adding a provider-name branch before the test starts, add an AST/static invariant over the generic control modules:

- conditions/match cases must not compare provider/profile identity to registered provider/profile string literals;
- provider/profile identity must not be dispatched through `startswith`, membership tables, or equivalent name-specific cases in the generic paths;
- capability fields such as protocol/engine/provider_type/wire_api may be compared to their supported capability values;
- explicit backward-compatibility tests may name current profiles, but runtime/generic modules may not rely on those test constants.

Because the synthetic identity is derived from the generic code digest, inserting a new exact fixture-name branch changes the digest and therefore changes the fixture identity. The AST rule covers broader name-pattern special casing.

This is the concrete evidence for MINOR-2.

### 11.4 Effective Model Audit tests

For every production compatible profile from the Registry:

- valid source/lock passes;
- source `engine.model` mismatch fails;
- `COPILOT_MODEL` mismatch fails;
- compiled info-model mismatch fails;
- compiled engine-model mismatch fails;
- missing model metadata fails.

The test set contains no `deepseek` branch in the generic audit implementation.

### 11.5 Preflight tests

Iterate the validated production Registry rather than a fixed execution tuple:

- credential false → `MISSING_CREDENTIAL`;
- credential true + strict valid lock → `READY_FOR_ENTITLEMENT_PROBE`;
- `entitlement_verified` remains false in both paths;
- missing/invalid lock fails;
- unknown profile fails;
- malformed Registry fails before identity use;
- profile/provider/protocol/model/maturity/credential/worker fields are sourced from the validated Registry result.

### 11.6 Workflow drift tests

`render_gh_aw_profile_surfaces.py --check` proves:

- both workflow choice lists equal Registry profile ids in deterministic order;
- workflow default equals `runtime.yaml` default;
- credential-presence block covers every registered profile and its aliases;
- no stale profile/credential mapping remains.

### 11.7 Command/trust boundary tests

Continue deterministic checks that the target Issue Comment syntax does not accept runtime identity selectors.

Also assert:

- cross-repo gateway validates worker identity through the shared Registry;
- provider/profile code is not imported into Commander, transition engine, Gate evaluation, or persistence paths;
- canonical worker remains read-only by default and preserves Safe Output markers;
- worker cannot directly edit Feature Manifest/Event state, pass Gates, merge, or release.

### 11.8 Backward-compatibility regression tests

A test-only compatibility baseline records the five existing profile mappings and validates:

- profile id;
- engine;
- credential contract;
- worker source;
- worker workflow;
- model/version where pinned;
- maturity;
- default profile.

This explicit enumeration is acceptable in regression evidence because it is not consumed for runtime routing or validation.

### 11.9 Repository validation/security workflows

Implementation evidence must include the existing repository validation/security checks relevant to:

- `runtimes/**`;
- `scripts/**`;
- `.github/workflows/**`;
- deterministic worker render/compile checks;
- command/cross-repo security validators.

All required checks must be green before Code Review/Verification can rely on the implementation.

## 12. Certification pipeline documentation model

`docs/integrations/openai-compatible-providers.md` is updated to make onboarding a staged certification process:

```text
1. registry entry
   |
   v
2. validated Registry contract
   |
   v
3. deterministic worker render
   |
   v
4. strict compile + compiled metadata validation
   |
   v
5. static preflight
   |   proves: registered identity, strict lock, credential presence
   |   does NOT prove: entitlement/quota/billing/model availability/rate limit
   v
6. live entitlement probe
   |   provider-specific live evidence, outside static control path
   v
7. bounded dogfood
   |   Draft PR + Worker Result + persistence/handoff evidence
   v
8. maturity promotion decision
       evidence-backed; never implied by protocol compatibility
```

The documentation must state that a Registry entry is necessary but not sufficient for production/reference maturity.

## 13. Security considerations

### Registry trust

The Registry is code-reviewed trusted control-plane metadata, not target/user input. Nonetheless, every field that becomes a URL, host, model, secret name, source path, or workflow identity is validated before use.

### Secret safety

Only credential names are committed. The preflight workflow produces booleans from secret-presence expressions. Secret values never enter Python control-plane data structures or durable evidence.

### Network safety

Compatible endpoints must be HTTPS, carry no embedded credentials, and use an exact registered host that is also the worker network allowlist host. Provider registration therefore changes network reach only through an explicit trusted Registry diff and regenerated worker artifact.

### Worker identity

Only exact registered compiled workflow filenames are accepted. No fallback/glob/path search is used.

### Target isolation

Target repositories cannot choose provider, profile, engine, model, credential, or worker. Exact target repository/ref/manifest validation remains separate and unchanged.

### Lifecycle authority

Provider workers remain execution backends only. They do not gain direct Feature state write, Feature Event authority, Gate authority, merge authority, or release authority.

### Provider HTTP dependency boundary

No direct provider HTTP dependency is added to Commander, transition engine, Gate evaluation, Runtime App persistence, or Feature persistence. Static preflight is deliberately offline with respect to provider APIs.

## 14. Observability and durable evidence

Static preflight JSON is the primary machine-readable readiness evidence. It contains only non-secret identity and boolean readiness data.

Deterministic validation output should identify the failing profile/field without dumping secret values or entire raw records.

Implementation/Verification evidence should be traceable to:

- Registry validation results;
- generated workflow-surface drift check;
- rendered worker drift check;
- strict compiled-lock checks;
- Effective Model Audit across all compatible profiles;
- synthetic extension proof;
- malformed-profile rejection proof;
- command/cross-repo security validation;
- repository CI status.

Live entitlement and dogfood evidence remain distinct artifacts from static preflight.

## 15. Risks and alternatives

### Risk: one malformed unused profile blocks all routing

This is intentional. Partial acceptance would recreate inconsistent trust surfaces. Because the Registry is trusted control-plane configuration, it should be atomically valid.

### Risk: shared loader becomes a high-value boundary

Mitigation: small API, immutable values, extensive negative tests, no secret values, no provider HTTP, and all consumers use it rather than duplicating parsing.

### Risk: generated workflow YAML is harder to hand-edit

Mitigation: keep generation limited to marker-owned profile/credential blocks and expose `--check`; surrounding workflow logic remains human-authored. CI reports drift rather than silently rewriting.

### Alternative rejected: fully dynamic runtime secret lookup

Not required to satisfy the Feature. It would make the secret-resolution boundary depend more heavily on expression indirection and make review/debugging less explicit. The generated bounded mapping preserves the same provider-neutral Python architecture while keeping each permitted secret reference visible in trusted workflow YAML.

### Alternative rejected: remove workflow choices and accept free-form strings

The resolver would still fail closed, but this degrades the trusted operator UI without improving authority. Generated choices keep usability while eliminating manual drift.

### Alternative rejected: keep independent YAML parsing in each consumer

This fails MINOR-1 because malformed entries can be treated differently by routing, preflight, allowlisting, and audit.

### Alternative rejected: fixed `fixture-provider` test only

This fails MINOR-2 because implementation could special-case that exact name. Digest-derived synthetic identities plus AST/static branch invariants make the proof materially stronger.

### Risk: test-only compatibility baseline duplicates profile data

Mitigation: it is explicitly non-runtime and exists only to detect unintended backward-compatibility drift. Registry remains the sole runtime authority.

## 16. Acceptance criteria traceability

| Approved AC | Design/evidence |
| --- | --- |
| 1. Registry-driven compatible validation | shared full-Registry loader + protocol/capability contract |
| 2. no provider-name-specific Python branches required | capability-based logic + AST/static invariant + digest-derived fixture ids |
| 3. Effective Model Audit covers all applicable compatible profiles | Registry iteration + five model identity surfaces |
| 4. generic non-invasive preflight | shared Registry + shared lock validation + no provider HTTP |
| 5. missing credential non-ready; presence != entitlement | generated boolean presence + `MISSING_CREDENTIAL` / `READY_FOR_ENTITLEMENT_PROBE` + entitlement false |
| 6. unknown profile/worker fail closed | exact profile lookup + exact validated worker index |
| 7. existing providers regression green | test-only five-profile compatibility baseline + rendered/compiled checks |
| 8. target commands cannot inject runtime identity | unchanged Issue Comment syntax + deterministic forbidden-selector validator |
| 9. synthetic registry-only extension | temp Registry/workspace + digest-derived ids + unchanged generic modules + AST guard |
| 10. repository validation/security workflows green | required implementation/verification evidence set |
| 11. certification documentation | explicit 8-step registration-to-promotion pipeline |
| 12. no lifecycle/Gate/merge/release authority change | canonical worker/security and control-plane boundary checks |

## 17. Explicit non-goals retained

This Design does not authorize implementation of:

- Qwen;
- GLM;
- MiniMax;
- Kimi;
- any other new production provider;
- Product autonomous worker;
- Architect autonomous worker;
- Requirement/Design/Code Reviewer autonomous workers;
- QA autonomous worker;
- Acceptance autonomous worker;
- full mixed-provider/multi-role dogfood;
- intelligent provider scoring/routing;
- target-controlled provider/model selection;
- DeepSeek maturity promotion;
- default-profile change;
- compiler/runtime dependency replacement.

Those remain separate follow-up Features.

## 18. Design completion boundary

The Architect's responsibility ends with this durable Design artifact and a proposed Design completion Feature Event.

An independent Design Reviewer must evaluate this Design against the approved Requirement and `design` rubric. This Architect does not approve `design-v1`, does not PASS `design-gate`, and does not implement the code described above.
