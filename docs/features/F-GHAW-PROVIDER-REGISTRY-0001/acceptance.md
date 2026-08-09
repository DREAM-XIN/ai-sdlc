# Acceptance — F-GHAW-PROVIDER-REGISTRY-0001

## Verdict

PASS

## Acceptance scope

Evaluated the delivered provider-registry generalization as the Acceptance Product Owner after independent Code Review and Verification passed.

The Product acceptance question is whether the Feature delivers the approved outcome: future trusted OpenAI-compatible providers can be admitted through one deterministic Registry/certification path without provider-name-specific trusted control branches, while current compatibility and lifecycle/security authority remain intact.

## Product outcome acceptance

### 1. One trusted provider inventory

PASS — `runtimes/gh-aw/engine-profiles.yaml` remains the trusted profile inventory, while shared Registry validation now establishes the fail-closed boundary used by trusted consumers instead of each consumer maintaining an independent provider enumeration.

### 2. Future compatible-provider extensibility

PASS — A future OpenAI-compatible provider can be represented through trusted Registry metadata and generated worker artifacts without requiring a new provider-name conditional in generic validation, preflight, effective-model auditing, or worker allowlisting logic. The deterministic synthetic-provider proof validates this product outcome.

### 3. Certification rather than accidental enablement

PASS — The delivered documentation defines a deliberate admission sequence:

```text
registry entry → shared validation/rendering → strict compile → static preflight → live entitlement probe → bounded dogfood → maturity promotion
```

Static API compatibility is not treated as production readiness, entitlement, or maturity evidence.

### 4. Preflight semantics remain honest

PASS — Static preflight reports only configuration/compiled-lock/credential-presence readiness. It does not claim live quota, billing, entitlement, model availability, or rate-limit capacity. The Code Review remediation also closed the credential-alias false-readiness case so a positive static credential result maps to the credential actually used by the OpenAI-compatible worker.

### 5. Existing provider compatibility

PASS — Copilot, Codex, Claude, Gemini, and DeepSeek remain supported through their trusted profile/worker mappings. The default remains Copilot. DeepSeek remains `experimental`; this refactor is not used as evidence to promote it.

### 6. Scope control

PASS — This Feature did not absorb the planned follow-up work. It does not onboard Qwen, GLM, MiniMax, Kimi, or another new production provider; does not add autonomous Product/Architect/Reviewer/QA roles; and does not introduce provider scoring or target-controlled provider/model routing.

### 7. Target-repository trust boundary

PASS — Target Issue Comments still cannot inject arbitrary provider, model, profile, credential, or worker workflow identities. Unknown or unregistered identities fail closed and trusted routing remains a control-plane concern.

### 8. Lifecycle and authority boundary

PASS — Provider workers remain read-only by default and use Safe Outputs for writes. They do not gain direct Feature Manifest mutation, Gate self-approval, merge, or release authority. Feature Events, trusted Persist, optimistic revisions, independent review, Verification, and Acceptance remain authoritative.

### 9. Real dogfood remediation quality

PASS — The lifecycle was not artificially forced through green Gates. Independent Code Review found a MAJOR and MINOR, both were remediated and re-reviewed. The same cycle exposed a separate trusted-control remediation-closure bug; that defect was isolated into hotfix PR #197, independently reviewed, fully validated, and merged to trusted `main` before the Feature was allowed to pass Code Review. This demonstrates the intended fail-closed governance behavior rather than bypassing it.

### 10. Final integration readiness

PASS — QA verified the final mergeable integration form of PR #196 after the trusted-control hotfix landed. The final tested implementation head had successful protocol, Required PR Gate, public runtime distribution, and gh-aw worker compile workflows, with deterministic/security coverage for Registry extension, effective model metadata, runtime preflight, command boundaries, cross-repository control, and remediation closure.

## Verification dependency

Acceptance relies on `evidence-verification-v1`, which independently verified all twelve approved acceptance criteria against the final mergeable implementation state. Acceptance does not replace or waive Verification.

## Follow-up boundary

With the Registry/certification foundation accepted, separate Features may now safely address:

- registering and certifying additional domestic providers such as Qwen, GLM, and MiniMax as experimental profiles;
- autonomous non-Developer roles with independent role/output contracts;
- mixed-provider full-lifecycle dogfood and evidence-backed maturity promotion.

Those outcomes are intentionally not claimed by this Feature.

## Release Gate conclusion

The Feature delivers the approved provider-registry generalization and certification foundation with existing compatibility preserved, fail-closed trusted identity handling, honest static-preflight semantics, deterministic extension proof, and unchanged lifecycle/security authority.

`release-gate` may PASS.
