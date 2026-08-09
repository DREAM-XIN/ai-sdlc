# Verification — F-GHAW-ROLE-ROUTING-0001

## Verdict

PASS

## Verification basis

Independent QA verification was performed after Code Gate PASS against the approved Requirement, approved Design, Plan, current PR #201 implementation, Code Review v2, and completed Code Remediation evidence.

Verified current QA candidate head: `98af6f42a2809cd675a278364f1dbf39c4988659`.

## Acceptance-oriented checks

1. **Trusted role/stage policy is deterministic and fail-closed.** Unknown role/stage, malformed policy, unknown candidates, duplicate candidates, incomplete readiness, and no-ready candidate cases fail closed.
2. **Developer route is active on the normal autonomous entrypoint.** The default Developer/implementation policy orders `codex` before `copilot`; Issue Comment dispatch enters the core role-aware gateway instead of the manual profile gateway.
3. **Static fallback is deterministic.** Copilot is selected only when the preferred Codex candidate is statically not ready; no live inference/runtime retry or circuit breaker is introduced.
4. **Routing evidence is complete.** Audit output records policy version, rule id, role, stage, full `candidate_order`, evaluated decisions, selected profile/engine/provider/model/worker, fallback boolean/reason, and `entitlement_verified: false`.
5. **Credential readiness is metadata-driven.** Registry `credential_source` supports only validated `secret` and `github-token`; Copilot uses trusted GitHub token presence and system-token profiles cannot use aliases.
6. **No secret serialization.** Routing/readiness payloads contain booleans and profile metadata only; secret values are not passed to Python or written to routing evidence.
7. **Experimental profiles remain excluded by default.** Default routing rules do not allow experimental profiles; trusted `allow_experimental` is required for an experimental candidate.
8. **Target command boundary remains closed.** Target Issue Comment syntax cannot select provider, model, engine profile, credential, worker, candidate order, routing policy, or experimental opt-in.
9. **Manual operator path remains distinct.** Explicit trusted worker override is audited as `manual-trusted-profile`; it is not exposed through the target command grammar.
10. **Compatibility remains intact.** Copilot remains the global compatibility default and all eight registered profiles continue to pass strict worker compile validation.
11. **Cross-repository security boundary remains intact.** Exact Registry worker identity, target repository binding, Runtime App permissions, Safe Output restrictions, and Feature/Gate authority are unchanged.
12. **Reviewer/QA autonomy is not expanded by this Feature.** Routing metadata for those roles does not grant new autonomous lifecycle execution or Gate authority.

## GitHub Actions evidence

All required workflows succeeded on the QA candidate head:

- Validate AI-SDLC protocol — SUCCESS — run `31314278557`
- Validate Public Runtime Distribution — SUCCESS — run `31314278589`
- Validate AI-SDLC gh-aw Worker Compile — SUCCESS — run `31314278564`
- Required PR Gate — SUCCESS — run `31314278561`

The protocol run covers lifecycle validation, routing regression, Registry validation, synthetic extension/anti-special-case guards, worker materialization, effective-model metadata, command boundary, source-aware runtime preflight, release readiness, and cross-repository control scenarios.

## Result

No verification blocker or regression was found. The implementation satisfies the approved deterministic role-aware routing and static-readiness fallback contract while preserving existing authority/security boundaries.

Verification therefore PASSes and supports `verification-gate: PASS`, subject to trusted Feature Event persistence.
