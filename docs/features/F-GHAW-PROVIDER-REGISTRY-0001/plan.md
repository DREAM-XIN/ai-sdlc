# Implementation Plan — F-GHAW-PROVIDER-REGISTRY-0001

Feature: `F-GHAW-PROVIDER-REGISTRY-0001`

Issue: `#195`

Approved inputs: `requirement-v1`, `design-v1`, `evidence-design-review-v1`

## Scope boundary

Implement the approved registry/certification foundation only. Do not register a new production provider, promote DeepSeek, change the default `copilot` profile, expose runtime identity selectors to target repositories, add autonomous lifecycle roles, replace the pinned gh-aw compiler/runtime, or alter Feature/Gate/Safe Output/merge/release authority.

## Work units and dependencies

### WU-1 — Shared trusted Registry boundary

Add `scripts/gh_aw_provider_registry.py` with immutable normalized profile objects, deterministic errors, full-Registry validation before selection, protocol/capability-based validation, and exact indexes for profile and worker workflow identity.

Required invariants include root/schema validation, common and protocol-specific fields, HTTPS endpoint restrictions, no URL credentials/query/fragment, exact `network_host` hostname match, narrow model/credential/path syntax, and global uniqueness of trusted worker/credential identities.

Dependencies: approved Design only.

Completion evidence:

- positive tests for every current registered profile;
- negative tests for malformed unrelated entries blocking all selection;
- unknown profile and duplicate/unregistered worker identities fail closed;
- errors identify profile/field without dumping raw records or secrets.

### WU-2 — Migrate all trusted Registry consumers

Migrate renderer, resolver, static preflight, cross-repository worker allowlisting, profile validator, runtime-preflight validator, and effective-model audit to consume the shared validated Registry rather than independently parsing `engine-profiles.yaml`.

Factor a shared compiled-lock inspection helper if needed so preflight and effective-model audit enforce the same source digest, compiled identity, provider, base URL, wire API, model, and audit metadata invariants.

Dependencies: WU-1.

Completion evidence:

- no production consumer in Feature scope directly parses the Registry;
- every applicable OpenAI-compatible profile is audited through one generic path;
- unknown profile/worker, malformed Registry, missing/invalid lock, and metadata drift fail closed;
- no fallback to another profile, model, credential, or worker.

### WU-3 — Deterministic generated workflow surfaces

Add a bounded generator for marker-owned profile-choice and credential-presence blocks in:

- `.github/workflows/ai-sdlc-gh-aw-preflight.yml`;
- `.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml`.

Keep secret references explicit in trusted workflow YAML. Pass only credential-presence booleans to Python; never pass, print, serialize, or persist secret values. Provide a `--check` drift mode and integrate it into deterministic validation.

Dependencies: WU-1; coordinate with WU-2 resolver/preflight contracts.

Completion evidence:

- generated choices exactly match the validated Registry;
- generated credential mapping is bounded and fail closed;
- missing credentials report non-ready state;
- credential presence never claims entitlement, quota, billing, model availability, or rate-limit capacity;
- workflow security validators remain green.

### WU-4 — Synthetic extension and anti-special-case proof

Create a temporary-workspace test that derives randomized/digest-based provider, profile, credential, worker, endpoint, and model identities, adds only fixture Registry/generated artifacts, and exercises registry validation, rendering, resolution, preflight, effective-model audit, and exact worker allowlisting without modifying generic control modules or the production Registry.

Implement the Design Review note by scoping the AST/static guard to semantically relevant provider/profile identity flows. Include explicit positive and negative guard fixtures so provider-name branches are rejected while capability constants and the test-only five-profile compatibility baseline are accepted.

Dependencies: WU-1 through WU-3.

Completion evidence:

- generic-module hashes remain unchanged during the fixture proof;
- synthetic literals are absent from generic production modules;
- positive and negative AST guard fixtures pass deterministically;
- malformed fixture and unregistered worker cases fail deterministically.

### WU-5 — Backward compatibility and command/security regression

Retain the existing `copilot`, `codex`, `claude`, `gemini`, and `deepseek` mappings, models, credentials, compiled worker identities, default `copilot`, and DeepSeek `experimental` maturity.

Verify Issue Comment parsing still rejects `provider`, `model`, `engine_profile`, `credential`, and `worker_workflow` selectors. Verify provider workers remain read-only by default, use Safe Outputs for GitHub writes, and have no lifecycle/Gate/merge/release authority.

Dependencies: WU-2 and WU-3.

Completion evidence:

- deterministic compatibility baseline passes;
- rendered and compiled workers show no unexpected drift;
- command-boundary, cross-repository allowlist, Safe Output, and workflow security tests pass.

### WU-6 — Provider certification documentation

Update the canonical gh-aw/OpenAI-compatible provider integration documentation to define:

`registry entry → shared validation → worker render → strict compile → static preflight → live entitlement probe → bounded dogfood → maturity promotion`.

Clearly separate static compatibility/readiness from live entitlement and operational evidence, and state that maturity promotion requires separate durable evidence.

Dependencies: finalized contracts from WU-1 through WU-5.

Completion evidence:

- documentation uses current file/workflow names and secret-boundary language;
- no documentation implies static preflight proves live access;
- non-goals and unchanged authority boundaries remain explicit.

### WU-7 — Integrated verification package

Run the targeted validators/tests introduced or changed above, then all repository validation/security workflows relevant to `runtimes/**`, `scripts/**`, and `.github/workflows/**`. Record exact commands, commit SHA, outputs/results, generated-artifact drift status, and CI run links in durable implementation evidence.

Dependencies: WU-1 through WU-6.

Completion evidence:

- all required deterministic checks pass on the candidate commit;
- repository CI required checks are green;
- evidence is traceable and contains no secrets;
- remaining live entitlement/dogfood work is explicitly outside this Feature.

## Suggested implementation order

1. Establish WU-1 and its negative tests.
2. Migrate consumers in WU-2.
3. Generate bounded workflow surfaces in WU-3.
4. Add WU-4 synthetic and AST guard proofs.
5. Lock backward compatibility/security in WU-5.
6. Update documentation in WU-6.
7. Produce integrated evidence in WU-7.

WU-2 consumer migrations may be developed in parallel after WU-1 stabilizes, but they must converge on the same shared Registry and compiled-lock contracts before WU-3/WU-4 integration.

## Required command categories

The Developer must resolve exact repository-supported commands from the current branch and record them rather than inventing substitutes. At minimum run:

- shared Registry unit/fixture tests;
- renderer drift check;
- generated workflow-surface drift check;
- runtime preflight regression for every registered profile;
- effective-model audit for every applicable OpenAI-compatible profile;
- synthetic extension and malformed/unregistered rejection tests;
- command-boundary and forbidden-selector validation;
- gh-aw and GitHub workflow security validation;
- cross-repository trust/allowlist validation;
- the repository's complete relevant CI/validation suite.

Any failed required check blocks implementation completion; do not mark it as a note.

## Implementation evidence expectations

Durable implementation evidence must include:

- candidate commit/PR identity;
- files changed grouped by work unit;
- exact commands and exit results;
- current-profile compatibility matrix;
- effective-model audit matrix;
- static-preflight result matrix with credential values redacted/not present;
- synthetic extension and AST guard positive/negative results;
- malformed Registry/unknown profile/unregistered worker failure results;
- generated-artifact drift results;
- security/command-boundary results;
- CI run/check links;
- known limitations and follow-up boundaries.

## Definition of Done

Plan completion means only that implementation is ready to start. Feature implementation is complete only when all WU-1 through WU-7 outputs exist, all approved acceptance criteria are traceably covered, deterministic and security checks pass, the implementation evidence is durable, and the lifecycle is advanced to independent Code Review through trusted Persist.

The Developer must not approve its own implementation, PASS `code-gate`, perform independent Verification, merge, release, or directly edit the authoritative Feature Manifest.
