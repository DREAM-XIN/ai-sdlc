# Plan — F-GHAW-ROLE-ROUTING-0001

## WU-1 — Provider Registry credential-source contract

- Extend validated `EngineProfile` metadata with required `credential_source`.
- Allow exactly `secret` and `github-token`.
- Migrate all eight profiles explicitly.
- Constrain aliases by source type.
- Add positive/negative Registry fixtures and synthetic extension coverage.

DoD: Registry remains atomically fail-closed; no profile-name branch is needed to determine credential source.

## WU-2 — Trusted routing policy and loader

- Add `runtimes/gh-aw/profile-routing.yaml`.
- Add strict policy parser/validator.
- Validate rule ids, role/stage uniqueness, candidate order/uniqueness, Registry membership, maturity permission, and default profile.

DoD: malformed/unknown/duplicate/disallowed policy fails closed deterministically.

## WU-3 — Readiness surface generation

- Extend Registry-derived workflow surface generation for source-aware boolean readiness.
- `secret` uses primary/alias secret-presence expressions.
- `github-token` uses trusted GitHub token presence.
- Never serialize secret values to Python.
- Add drift and source-expression tests.

DoD: readiness generation branches only on validated source capability, not profile identity.

## WU-4 — Role-aware resolver and audit

- Add `scripts/gh_aw_profile_routing.py`.
- Resolve exact rule by role/stage.
- Iterate candidates deterministically.
- Skip statically non-ready candidates, enforce maturity, validate compiled worker, fail closed on no-ready/invalid context.
- Emit deterministic non-secret audit JSON.

DoD: Developer preferred/fallback/no-ready scenarios and Reviewer/QA audit routes are fully tested.

## WU-5 — Developer dispatch integration

- Integrate policy resolution into normal autonomous Developer dispatch before the existing gateway receives `worker_workflow`.
- Keep Reviewer/QA runtime manual.
- Preserve global Copilot compatibility default.
- Keep manual trusted profile workflow separate and label selection mode.

DoD: normal autonomous Developer path uses policy selection; target inputs cannot override route/profile/worker.

## WU-6 — Boundary/security regression

- Extend command/project/dispatch boundary tests for routing policy path, candidate order and `allow_experimental` selectors.
- Prove cross-repo worker allowlist remains exact Registry identity.
- Prove no secret fixture value appears in routing output/log-oriented fixtures.

DoD: forbidden target selectors fail closed; authority boundaries unchanged.

## WU-7 — Compatibility and verification package

Run/record:

- Provider Registry validation and synthetic extension tests;
- generated worker/profile/readiness drift checks;
- runtime preflight/effective-model tests;
- routing validators/resolver tests;
- action/workflow/security validation;
- public-runtime validation;
- 8-profile strict compile CI;
- Required PR Gate.

Document static-readiness fallback vs runtime/inference retry non-goal and produce implementation evidence.

## Review checkpoints

Code Review must explicitly inspect:

1. no profile/provider-name branch for credential source or routing selection;
2. `github-token` source cannot accept aliases;
3. experimental profiles cannot enter default policy without trusted opt-in;
4. target repositories cannot override policy/profile/candidates/experimental flag;
5. no runtime-failure retry was smuggled into v1;
6. routing audit contains no secret values;
7. Reviewer/QA remain manual runtime roles.
