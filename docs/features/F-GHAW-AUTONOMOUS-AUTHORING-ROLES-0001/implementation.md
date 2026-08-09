# Implementation Evidence — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Ownership and authority

Lifecycle owner for this evidence: **Implementation Developer**.

This document records implementation and deterministic verification only. It does **not** approve `code-gate`, `verification-gate`, or `release-gate`; those remain later independent lifecycle decisions.

Feature: `F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001`  
Issue: `#204`  
PR: `#207`  
Verified implementation candidate: `2587bcd129e0c1f42cf58dd118dc70f7af441b2f`

## Implemented scope

The implementation extends the trusted gh-aw runtime to exactly three front-half authoring identities while preserving the existing autonomous implementation/review/verification identities and all independent Gate authorities:

| Role | Stage | Runtime | Preferred profile | Fallback | Authority boundary |
|---|---|---|---|---|---|
| Product | `requirement` | gh-aw autonomous | Claude | Copilot | author requirement draft only |
| Architect | `design` | gh-aw autonomous | Claude | Copilot | author design draft only |
| Orchestrator | `plan` | gh-aw autonomous | Codex | Copilot | author plan draft only |
| Developer | `implementation` | gh-aw autonomous | Codex | Copilot | existing behavior preserved |
| Reviewer | `code-review` | gh-aw autonomous | Claude | Copilot | existing independent review behavior preserved |
| QA | `verification` | gh-aw autonomous | Gemini | Copilot | existing independent verification behavior preserved |

`Product/acceptance`, `Reviewer/requirement-review`, `Reviewer/design-review`, merge, release, and all Gate PASS decisions remain outside autonomous authoring scope.

## Work-unit evidence

### WU-1 — Exact lifecycle routing

- `dispatch/gh-aw-developer.yaml` adds only the exact autonomous pairs `product+requirement`, `architect+design`, and `orchestrator+plan` before the role-level manual fallbacks.
- `runtimes/gh-aw/profile-routing.yaml` adds deterministic trusted profile order:
  - Product/requirement: `claude -> copilot`
  - Architect/design: `claude -> copilot`
  - Orchestrator/plan: `codex -> copilot`
- Existing Developer, Code Reviewer, and QA routing remains present with its previous profile order.
- Experimental provider profiles are excluded from these production routing rules (`allow_experimental: false`).

### WU-2 — Trusted role-worker registry and strict compile

- `runtimes/gh-aw/role-workers.yaml` registers two trusted workers for each authoring identity: preferred profile plus Copilot fallback.
- Product, Architect, and Orchestrator worker source/lock pairs were added under `.github/workflows/`.
- The strict compile discovery now validates the trusted provider-profile workers plus trusted role workers. The verified candidate compiled **18 targets** successfully.
- The adapter resolves a requested generic worker through the trusted provider registry and then through the exact role/stage/profile registry; target-controlled content does not choose a workflow, provider, profile, or model.

### WU-3 — Closed authoring result contract

- `runtimes/gh-aw/authoring-result.schema.json` defines a closed structured result envelope for autonomous authoring.
- `scripts/gh_aw_authoring_result.py` accepts only the exact authoring role/stage matrix and derives canonical repository paths from trusted code:
  - requirement -> `docs/features/<feature-id>/requirement.md`
  - design -> `docs/features/<feature-id>/design.md`
  - plan -> `docs/features/<feature-id>/plan.md`
- Model-supplied paths are not part of the accepted contract.
- A completed authoring result creates a `draft` artifact record and advances only its bounded authoring stage to `DONE` plus the immediately following stage to `READY`; it never emits a Gate change.
- `BLOCKED` authoring fails closed and creates no artifact.

### WU-4 — Provenance and deterministic draft replacement

- `scripts/gh_aw_authoring_provenance.py` binds accepted authoring output to the exact trusted Actions run, control repository, default branch, registered workflow, role/stage, Feature, task, and revision.
- The trusted translator rejects stale revisions, unregistered authoring identities, replayed evidence IDs, invalid remediation identity/status, and ambiguous multiple-current-draft state.
- Artifact version IDs are deterministic (`requirement-vN`, `design-vN`, `plan-vN`). A replacement supersedes the unique current draft before registering the next draft.
- Canonical artifact content is written by the trusted collector path, not by the read-only worker.

### WU-5 — Read-only worker and authority boundary

- Authoring workers are bounded to producing structured authoring output; lifecycle persistence remains owned by trusted Event/Persist machinery.
- Worker rules explicitly prohibit authoritative Feature Manifest edits and Gate self-approval.
- Requirement Review, Design Review, Acceptance/release, merge, and release authority remain independent/manual as required by the approved Requirement and Design.
- Existing Gate-role immutable-candidate and provenance boundaries remain covered by the shared protocol validation.

### WU-6 — Regression closure and compatibility

During final integration, Provider Registry evolution exposed stale deterministic fixtures. The fixes were deliberately limited to tests/compatibility and did not weaken runtime validation:

1. `validate_gh_aw_profile_routing.py` was changed from a handwritten credential-presence map to a Registry-derived complete map, defaulting every trusted credential identity to `False` and explicitly enabling only the credentials required by the test scenario. This preserves fail-closed completeness when trusted aliases/providers are added.
2. Codex fallback disables both `OPENAI_API_KEY` and its trusted alias `CODEX_API_KEY`; credential identities remain forbidden from routing audit payloads.
3. The duplicate route failure assertion was synchronized to the current trusted validator error contract.
4. `validate_gh_aw_adapter.py` was synchronized with the approved Product/requirement authoring behavior: a generic Copilot request resolves to the registered `ai-sdlc-gh-aw-product-copilot.lock.yml`, uses the authoring-result contract, and carries the trusted numeric Feature Issue input. Legacy result/event compatibility scenarios remain covered.

No production authority was relaxed to make CI pass.

## Acceptance-criteria mapping

- **AC1–AC3:** exact autonomous Product/requirement, Architect/design, and Orchestrator/plan routes are registered and deterministically tested.
- **AC4:** Product Acceptance and independent Requirement/Design Review remain on manual fallback paths.
- **AC5–AC7:** authoring dispatch uses specialized role workers and the closed authoring-result path; canonical artifacts are draft-only and no authoring result can emit Gate PASS.
- **AC8–AC10:** trusted canonical path mapping, exact run/task/revision provenance, replay/stale/ambiguous-draft rejection, and deterministic supersession are implemented and tested.
- **AC11–AC13:** preferred/fallback routing is deterministic, experimental profiles are excluded by production policy, and audit payloads do not expose credential identities.
- **AC14:** existing Developer/Reviewer/QA autonomous routes remain registered and all shared protocol/security checks pass.
- **AC15:** Acceptance/release authority remains manual/trusted and is not translated by the authoring result collector.
- **AC16:** required candidate CI is green as recorded below.

## Deterministic verification

Verified candidate: `2587bcd129e0c1f42cf58dd118dc70f7af441b2f`

| Required check | Run | Result |
|---|---:|---|
| Validate AI-SDLC protocol | `31322678206` | SUCCESS |
| Validate AI-SDLC gh-aw Worker Compile | `31322678205` | SUCCESS — 18 trusted compile targets |
| Required PR Gate | `31322678203` | SUCCESS |
| Validate Public Runtime Distribution | `31322678244` | SUCCESS |

The Protocol run includes successful execution of the core validator, Feature Event/persistence and cross-repository scenarios, gh-aw adapter, feature-context, workflow-security, engine-profile/effective-model, command-boundary, runtime-preflight, and release-readiness checks.

## Deviations / limitations

- No new provider, provider maturity promotion, adaptive quality/cost routing, retry/circuit-breaker behavior, autonomous Requirement Review, autonomous Design Review, autonomous Acceptance, merge, or release was added.
- This evidence does not claim independent Code Review or QA. Those lifecycle stages must evaluate the frozen implementation candidate and create their own durable evidence before their respective Gates may PASS.

## Implementation conclusion

The approved implementation scope is complete and the verified implementation candidate satisfies the deterministic implementation checks. The Feature is ready for the legal `IMPL-DONE` transition **after the Evidence-bearing head itself also passes the required PR checks**.