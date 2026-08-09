# Implementation Evidence — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Ownership and authority

Lifecycle owner for this evidence: **Implementation Developer**.

This document records implementation and deterministic verification only. It does **not** approve `code-gate`, `verification-gate`, or `release-gate`; those remain later independent lifecycle decisions.

Feature: `F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001`  
Issue: `#204`  
PR: `#207`

Initial implementation candidate: `2587bcd129e0c1f42cf58dd118dc70f7af441b2f`  
Initial Evidence-bearing candidate: `82f5c7d22f98cd3aae8eb3b2abf63e937b474e82`  
Code Review remediation candidate: `07717f412eb8eabd7b973e4e4c9fefead98ee707`

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
- Strict compile discovery validates trusted provider-profile workers plus trusted role workers: **18 targets**.
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

- `scripts/gh_aw_authoring_provenance.py` validates the exact trusted Actions run, control repository, default branch, registered workflow, role/stage, Feature, task, and revision before translation.
- The collector stores that validated run metadata as `source-run.json` in the trusted collector workspace.
- `scripts/gh_aw_authoring_result.py` consumes the already-validated source run identity (or explicit trusted equivalents), includes `source_run_id` in its deterministic evidence/Event digest, and persists an exact `https://github.com/<control-repository>/actions/runs/<source_run_id>` URI as durable Feature evidence.
- Distinct source run ids therefore produce distinct evidence/Event identities; invalid run ids and invalid control-repository identities fail closed.
- The trusted translator also rejects stale revisions, unregistered authoring identities, replayed evidence IDs, invalid remediation identity/status, and ambiguous multiple-current-draft state.
- Artifact version IDs are deterministic (`requirement-vN`, `design-vN`, `plan-vN`). A replacement supersedes the unique current draft before registering the next draft.
- Canonical artifact content is written by the trusted collector path, not by the read-only worker.

### WU-5 — Read-only worker and authority boundary

- Authoring workers are bounded to producing structured authoring output; lifecycle persistence remains owned by trusted Event/Persist machinery.
- Worker rules explicitly prohibit authoritative Feature Manifest edits and Gate self-approval.
- Requirement Review, Design Review, Acceptance/release, merge, and release authority remain independent/manual as required by the approved Requirement and Design.
- Existing Gate-role immutable-candidate and provenance boundaries remain covered by the shared protocol validation.

### WU-6 — Regression closure and compatibility

During final integration, Provider Registry evolution exposed stale deterministic fixtures. The fixes were deliberately limited to tests/compatibility and did not weaken runtime validation:

1. `validate_gh_aw_profile_routing.py` derives the complete credential-presence map from the trusted Registry, defaults identities to `False`, and explicitly enables only the credentials required by each scenario.
2. Codex fallback disables both `OPENAI_API_KEY` and trusted alias `CODEX_API_KEY`; credential identities remain forbidden from routing audit payloads.
3. The duplicate route failure assertion is synchronized to the current trusted validator error contract.
4. `validate_gh_aw_adapter.py` is synchronized with the approved Product/requirement authoring behavior: a generic Copilot request resolves to the registered Product authoring worker, uses the authoring-result contract, and carries the trusted numeric Feature Issue input. Legacy result/Event compatibility scenarios remain covered.
5. Design Review DR-MINOR-1 is now explicit in deterministic tests: model-supplied path fields targeting traversal, unrelated `docs/**`, `state/**`, and `.github/**` are all rejected; a traversal Feature path is also rejected by canonical path construction.

No production authority was relaxed to make CI pass.

## Code Review remediation

Independent Code Review v1 returned **REWORK** with one MAJOR and one MINOR.

### CR-MAJOR-1 — durable exact source-run provenance

Closed by the remediation candidate `07717f412eb8eabd7b973e4e4c9fefead98ee707`:

- online provenance validation remains in `gh_aw_authoring_provenance.py`;
- the translator now consumes the validated source run identity from the collector workspace;
- durable evidence points to the exact trusted Actions run rather than using the Bot comment as the sole durable provenance URI;
- `source_run_id` participates in deterministic evidence and Event identity;
- deterministic tests prove distinct source runs cannot collapse to the same durable evidence/Event identity and malformed trusted run identity fails closed.

### CR-MINOR-1 — explicit path-negative matrix

Closed by parameterized negative fixtures for:

- `../outside.md` traversal;
- unrelated `docs/**`;
- `state/**`;
- `.github/**`;
- malicious traversal passed into canonical Feature path construction.

The worker result schema remains closed and contains no authoritative path field.

## Acceptance-criteria mapping

- **AC1:** exact autonomous Product/requirement, Architect/design, and Orchestrator/plan dispatch exists while Product/acceptance and independent Requirement/Design Review remain manual.
- **AC2:** trusted routing defines deterministic non-experimental preferred/fallback candidates for all three authoring roles.
- **AC3–AC4:** the closed role-worker registry covers every approved authoring role/profile identity and the full trusted worker set strict-compiles deterministically.
- **AC5:** dedicated authoring-result semantics are separate from generic Developer completion semantics.
- **AC6:** collector provenance validates exact Actions run/workflow/task/revision and the remediated durable evidence retains the exact source run identity.
- **AC7–AC10:** Product, Architect, Orchestrator, and design-remediation translations are bounded to approved draft/stage/task transitions and cannot PASS Gates.
- **AC11:** canonical writer paths are closed and explicit traversal/docs/state/.github negative fixtures fail closed.
- **AC12:** target-controlled syntax cannot select provider/model/profile/worker/candidate order/experimental opt-in or expand artifact paths.
- **AC13:** Bot comment transport alone is not provenance; exact source run is validated and durably retained, with run-bound replay identity.
- **AC14:** existing Developer/Reviewer/QA autonomous routes and shared security contracts remain green.
- **AC15:** Acceptance/release authority remains manual/trusted and no authoring translator can create release, merge, or release-gate authority.
- **AC16:** required candidate CI is green as recorded below.

## Deterministic verification

### Initial implementation candidate

Candidate: `2587bcd129e0c1f42cf58dd118dc70f7af441b2f`

| Required check | Run | Result |
|---|---:|---|
| Validate AI-SDLC protocol | `31322678206` | SUCCESS |
| Validate AI-SDLC gh-aw Worker Compile | `31322678205` | SUCCESS — 18 trusted compile targets |
| Required PR Gate | `31322678203` | SUCCESS |
| Validate Public Runtime Distribution | `31322678244` | SUCCESS |

The initial Evidence-bearing head `82f5c7d22f98cd3aae8eb3b2abf63e937b474e82` independently re-passed all four required checks (`31322776610`, `31322776624`, `31322776609`, `31322776607`).

### Code Review remediation candidate

Candidate: `07717f412eb8eabd7b973e4e4c9fefead98ee707`

| Required check | Run | Result |
|---|---:|---|
| Validate AI-SDLC protocol | `31323260812` | SUCCESS |
| Validate AI-SDLC gh-aw Worker Compile | `31323260797` | SUCCESS — complete 18-target matrix |
| Required PR Gate | `31323260806` | SUCCESS |
| Validate Public Runtime Distribution | `31323260799` | SUCCESS |

The Protocol run includes successful execution of the core validator, Feature Event/persistence and cross-repository scenarios, autonomous-authoring source-run/path regressions, gh-aw adapter, feature-context, workflow-security, engine-profile/effective-model, command-boundary, runtime-preflight, and release-readiness checks.

## Deviations / limitations

- No new provider, provider maturity promotion, adaptive quality/cost routing, retry/circuit-breaker behavior, autonomous Requirement Review, autonomous Design Review, autonomous Acceptance, merge, or release was added.
- Code Review v1 REWORK is preserved as durable history; this evidence does not convert it into PASS. A new independent Code Review must evaluate the remediation candidate.
- QA and Acceptance remain future independent lifecycle stages.

## Implementation conclusion

The bounded Code Review remediation is implemented and its remediation candidate passes all four required checks. The remediation task may be completed after this Evidence-bearing head itself re-passes the required checks; `code-gate` remains outside Developer authority.