# Code Review Evidence — F-GHAW-DOMESTIC-PROVIDERS-0001

Feature: `F-GHAW-DOMESTIC-PROVIDERS-0001`

Issue: `#198`

PR: `#199`

Role: independent Code Reviewer

Verdict: **PASS_WITH_NOTES**

Findings: **0 BLOCKER / 0 MAJOR / 1 MINOR**

## Review basis

Reviewed independently against:

- approved Requirement and Requirement Review;
- approved Design, Design Review REWORK, Architect remediation, and Design Review v2 PASS;
- approved Plan;
- Implementation Evidence;
- PR #199 implementation diff and generated worker/lock artifacts;
- final implementation payload CI evidence;
- repository review rubric and lifecycle/security authority boundaries.

Implementation payload used for deterministic CI evidence: `e519035ccec5d6dda924faab4ee0b8a538f10147`.

All required payload workflows passed:

- Validate AI-SDLC protocol — `31311150684` — SUCCESS;
- Validate Public Runtime Distribution — `31311150688` — SUCCESS;
- Validate AI-SDLC gh-aw Worker Compile — `31311150690` — SUCCESS;
- Required PR Gate — `31311150764` — SUCCESS.

The Registry-derived strict compile matrix covers all eight registered profiles: Copilot, Codex, Claude, Gemini, DeepSeek, Qwen, GLM, and MiniMax.

## Requirement and Design compliance

### Provider cohort and scope

PASS.

The Registry adds exactly Qwen, GLM, and MiniMax with the approved endpoint/model/credential metadata. DeepSeek remains `experimental`; the three new providers are `experimental`; Copilot remains the default; Kimi is not added.

No target Issue Comment provider/model/profile/credential/worker selector was introduced and lifecycle/Gate/merge/release authority remains unchanged.

### Generic trusted control path

PASS.

Renderer, resolver/preflight/audit/allowlisting behavior remains capability/Registry-driven. Provider-specific names occur where expected in trusted Registry metadata, generated artifacts, documentation, and compatibility/certification fixtures; no Qwen/GLM/MiniMax production control branch was found.

### Design Review carry-forward: bounded missing-source materialization

PASS.

`render_gh_aw_workers.py` confines the source-existence relaxation to renderer write mode:

```python
def load_renderer_registry(*, check: bool):
    return load_registry(require_source_files=check)
```

Thus normal renderer `--check` uses strict source existence while write materialization may bootstrap newly registered generated sources.

`scripts/validate_gh_aw_worker_materialization.py` proves both directions:

- strict Registry load rejects an absent registered source;
- bounded materialization load may structurally resolve it;
- deterministic generation creates the expected source;
- strict Registry loading succeeds after generation;
- deleting the source makes strict loading fail again.

No target-facing resolver, static preflight, effective-model audit, cross-repository worker allowlist, or command surface exposes a source-existence relaxation selector.

### Registry-derived compile/materialization orchestration

PASS.

PR compile discovery loads the validated Registry and derives the matrix/profile identities from it. The materialization workflow uses the bounded pre-source mode only before generation, then immediately performs normal strict renderer/Registry checks before compiling and committing artifacts.

Generated locks were produced by pinned gh-aw `v0.83.4` strict compilation and are independently recompiled by PR CI.

### Compiler-generated lock security boundary

PASS_WITH_NOTES.

`validate_action_security.py` no longer trusts a provider-name filename list. A generated worker lock is eligible for the compiler-specific `persist-credentials: true` exception only when all of the following hold:

1. it is under the exact `.github/workflows` directory;
2. its filename is an exact `worker_workflow` identity from the fully validated Registry;
3. the first-line gh-aw metadata is valid JSON;
4. `schema_version == v4`;
5. `strict == true`;
6. `compiler_version == v0.83.4`.

A candidate-looking but unregistered gh-aw lock fails closed. A registered lock without the required pinned strict attestation also fails closed. Existing full-SHA action pin and forbidden-trigger/write checks remain active.

## Finding CR-MINOR-1 — deterministic negative fixtures for generated-lock exception

Severity: **MINOR**

The generated-lock security exception is narrow enough for this Gate and real repository validation passes, but the new boundary does not have a dedicated synthetic negative fixture that independently mutates the relevant inputs.

A follow-up hardening test should explicitly prove at least:

- an unregistered lookalike lock with otherwise plausible gh-aw metadata is rejected;
- a registered lock with `strict: false` is rejected;
- a registered lock with a non-pinned compiler version is rejected;
- a registered lock with the wrong schema version is rejected.

This is non-blocking because the implementation performs all four checks directly, the full Registry is validated before policy scanning, current generated artifacts carry the expected attestation, and required protocol/security CI is green. The note should be retained as future regression-hardening work rather than weakening the current boundary.

## Compatibility and failure handling

PASS.

- legacy five-profile mappings remain protected as test-only backward compatibility assertions rather than runtime authority;
- malformed unrelated Registry entries continue to invalidate trusted Registry loading;
- unknown profiles and unregistered workers fail closed;
- unsafe URL/path/duplicate identity validation remains active;
- secret values are not passed to Python preflight, only boolean presence;
- effective-model audit covers DeepSeek plus Qwen/GLM/MiniMax generically;
- static preflight never claims provider entitlement;
- provider certification documentation correctly records live entitlement/dogfood as not established.

## Gate recommendation

The implementation satisfies the approved Requirement and Design with no BLOCKER or MAJOR finding. `code-gate` may PASS with this review evidence, and the Feature may advance to independent Verification.

The Code Reviewer does not perform QA Verification, Acceptance, merge, or release.
