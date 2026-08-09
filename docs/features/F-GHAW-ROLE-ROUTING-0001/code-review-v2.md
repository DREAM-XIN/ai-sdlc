# Code Review v2 — F-GHAW-ROLE-ROUTING-0001

## Verdict

PASS

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0

## Scope reviewed

Independent review of PR #201 against the approved Requirement, approved Design, Plan, implementation evidence, prior Code Review REWORK, and completed Code Remediation evidence.

Reviewed the current routing implementation, Registry credential-source contract, generated readiness surfaces, same-repository and cross-repository dispatch integration, command boundary tests, synthetic Registry-extension guard, runtime preflight compatibility, and the final remediation for ordered-candidate audit evidence.

## Prior MAJOR closure

The v1 review found that successful preferred-profile selection stopped `decisions` after the selected profile and therefore did not durably record the full trusted candidate order required by the approved routing audit contract.

Remediation closes this without changing selection semantics:

- `RoutingResolution` now carries `candidate_order` directly from the validated routing rule;
- `resolution_payload()` emits `candidate_order` independently from evaluated `candidates` decisions;
- preferred Developer routing therefore records `candidate_order: [codex, copilot]` even when Codex is immediately selected;
- `decisions` continues to contain only candidates actually evaluated before selection, avoiding fabricated readiness states for later candidates;
- fallback and no-ready behavior remains unchanged and deterministic.

The regression suite explicitly checks preferred, fallback, no-ready, complete readiness, ordered-candidate evidence, and non-secret audit output.

## Security and lifecycle findings

1. **No provider/profile identity branching introduced.** Credential-source behavior is metadata-driven (`secret` / `github-token`) and routing selection is driven by validated policy/Registry metadata.
2. **Credential readiness remains presence-only.** Secret values are not serialized to Python or routing evidence; Copilot trusted system-token readiness is represented by `github.token != ''` rather than a repository secret.
3. **Experimental profiles remain excluded from default routing.** A routing rule containing an experimental profile requires trusted `allow_experimental: true`; current default rules do not opt in.
4. **Target command boundary remains closed.** Issue Comment command syntax cannot choose provider, model, engine profile, credential, worker, candidate order, routing policy, or experimental opt-in.
5. **Normal autonomous Developer entrypoint is actually role-routed.** The Issue Command dispatches the core gh-aw gateway with an empty trusted worker override; the gateway resolves role/stage policy before the existing exact-worker boundary.
6. **Manual profile selection remains a trusted operator path.** Manual override is explicitly audited as `manual-trusted-profile` and is not exposed through the target command grammar.
7. **Fallback is static-readiness-only.** No live inference/runtime retry or circuit breaker was introduced.
8. **Reviewer/QA are not newly made autonomous by this Feature.** Routing rules exist for audit/policy definition, but the approved non-goal remains intact.
9. **Existing lifecycle authority is unchanged.** Workers do not gain Feature Manifest, Gate, merge, or release authority.

## Verification evidence reviewed

Latest reviewed PR head before this evidence commit: `1cda66189c115051970d3a2cec1d4af9d17d08b1`.

GitHub Actions on that head:

- Validate AI-SDLC protocol — SUCCESS — run `31314124854`
- Validate Public Runtime Distribution — SUCCESS — run `31314124858`
- Validate AI-SDLC gh-aw Worker Compile — SUCCESS — run `31314124852`
- Required PR Gate — SUCCESS — run `31314124862`

Protocol validation includes the routing validator, Registry/worker materialization, effective-model validation, command boundary, credential-source-aware runtime preflight, release readiness, and cross-repository control scenarios. The worker compile workflow covers all eight registered profiles.

## Conclusion

The prior MAJOR is fully remediated. The implementation now satisfies the approved role-aware routing and audit contract without weakening fail-closed provider/worker boundaries, exposing target-controlled selectors, introducing provider-name-specific routing branches, or expanding into runtime retry/autonomous review roles.

Code Review v2 therefore PASSes and supports `code-gate: PASS`, subject to trusted Feature Event persistence.
