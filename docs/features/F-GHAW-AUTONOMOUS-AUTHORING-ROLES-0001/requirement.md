# Requirement — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Goal

Extend the trusted gh-aw execution path to the artifact-producing front half of the standard Feature lifecycle so that Product/`requirement`, Architect/`design`, and Orchestrator/`plan` can execute autonomously under deterministic trusted routing, while independent review and release authority remain separate and fail closed.

## Product intent

The system should be able to progress from a bootstrapped Feature through requirement authoring, independent requirement review, design authoring/remediation, independent design review, planning, implementation, independent code review, and QA without requiring a human to manually operate the Product/Architect/Orchestrator authoring steps. This Feature does **not** automate Requirement Review, Design Review, or Acceptance.

Autonomous authoring workers are artifact producers, not lifecycle authorities. They may propose durable artifacts and bounded completion recommendations. Trusted collectors and Feature Event/Persist remain the only path that registers artifacts and changes stage state. Review/release Gates remain owned by independent later roles.

## Scope

### In scope

- Product / `requirement` autonomous gh-aw worker path.
- Architect / `design` autonomous gh-aw worker path, including bounded design-remediation tasks sourced from design-review.
- Orchestrator / `plan` autonomous gh-aw worker path.
- Trusted role-aware profile routing for the three authoring roles with deterministic fallback to Copilot.
- Role-worker registry, generated sources, pinned strict compiled locks, result schemas, trusted collectors, provenance validation, same-repo/cross-repo dispatch integration, regression tests, operator/user docs, and durable Evidence.
- Compatibility with existing autonomous Developer, Code Reviewer, and QA execution.

### Out of scope

- Autonomous Requirement Reviewer.
- Autonomous Design Reviewer.
- Autonomous Acceptance / release-gate Product role.
- Direct Gate PASS/FAIL/REWORK writes by authoring workers.
- Merge/release authority.
- Adaptive inference retry, circuit breaking, dynamic cost/quality routing, provider promotion, or new providers.

## Required role boundaries

1. Product may be autonomous **only** for stage `requirement`. `product + acceptance` remains manual.
2. Architect may be autonomous **only** for stage `design`, including an explicitly assigned design-remediation task. It has no review Gate authority.
3. Orchestrator may be autonomous **only** for stage `plan` and may not modify Requirement/Design semantics.
4. Requirement Review and Design Review remain independent manual Gate roles in this Feature.
5. Existing Developer/code-review/QA autonomous routes remain semantically unchanged.

## Trusted artifact contract

Authoring workers must not directly write authoritative Feature Manifest/Event state. Each authoring worker must return a closed role-specific result that identifies exact Feature/task/stage/role/revision/repository/ref and durable artifact output. The trusted collector must:

- verify exact trusted worker run/workflow/task provenance;
- verify current authoritative revision and expected role/stage/task;
- resolve the durable artifact from trusted repository state rather than accepting an arbitrary model-selected authoritative path;
- validate the result against a closed schema;
- register the authored artifact as `draft` through a Feature Event;
- mark only the authoring stage/task completion permitted by the role;
- never PASS a review/release Gate as a consequence of an authoring result.

For normal authoring completion:

- Product requirement result may register a draft requirement artifact, mark `requirement: DONE`, and ready `requirement-review`.
- Architect design result may register a draft design artifact, mark `design: DONE`, and ready `design-review`.
- Orchestrator plan result may register a draft plan artifact and mark `plan: DONE`, then ready `implementation`.

For design remediation, the Architect result may register a new draft/superseding design candidate and complete only the assigned remediation task; the source `design-review` remains responsible for the subsequent review verdict and design-gate.

## Safe Output / repository write boundary

Autonomous authoring requires write-capable artifact production, but source writes must remain bounded to the assigned artifact surface. Workers must not receive generic unrestricted repository write authority. The implementation must use an auditable Safe Output or equivalent trusted write mechanism that constrains allowed artifact paths for the assigned Feature and role. The worker must not edit the authoritative Manifest/Event tree directly.

## Routing policy

Routing must be exact `role + stage`; broad role-only rules are forbidden for the new autonomous paths. The trusted routing layer must define deterministic non-experimental candidates for:

- Product / `requirement`
- Architect / `design`
- Orchestrator / `plan`

with Copilot as fallback. The exact preferred reference profiles are a Design decision, but experimental providers must remain excluded from default routing.

Target repositories, Issue Comments, worker prompts, and untrusted task fields must not select or override provider/model/profile/worker/candidate order/experimental opt-in/routing policy.

## Acceptance criteria

1. Runtime dispatch contains exact autonomous rules for `product+requirement`, `architect+design`, and `orchestrator+plan`; `product+acceptance`, Requirement Review, and Design Review remain manual.
2. Trusted profile routing defines deterministic non-experimental preferred/fallback candidates for all three authoring roles, and all selected profiles are valid Registry profiles.
3. A validated role-worker registry identifies every trusted authoring worker by exact role/stage/profile/source/compiled-lock identity; unknown identities fail closed.
4. Authoring worker sources are generated deterministically from trusted metadata and strict-compiled with the repository-pinned gh-aw compiler; drift checks cover both source and lock artifacts.
5. Product, Architect, and Orchestrator use closed role-specific result contracts. Generic Developer `COMPLETED => stage DONE` semantics cannot complete these stages.
6. Trusted collectors verify source Actions run id, workflow identity/ref, trusted task id, role/stage, Feature/revision, repository/ref, and result contract before translating an authoring result.
7. Normal Product completion may only register a draft requirement artifact, complete `requirement`, and ready `requirement-review`; it cannot PASS `requirement-gate`.
8. Normal Architect completion may only register a draft design artifact, complete `design`, and ready `design-review`; it cannot PASS `design-gate`.
9. Normal Orchestrator completion may only register a draft plan artifact, complete `plan`, and ready `implementation`; it cannot PASS any Gate.
10. Design-remediation Architect execution is task-bound: it may complete only the assigned remediation task and produce the next draft/superseding design candidate; independent Design Review remains responsible for the subsequent Gate verdict.
11. Repository write capability for authoring workers is bounded to approved Feature artifact paths and does not permit direct writes to authoritative `state/features/**` or `state/events/**`; negative tests prove forbidden-path attempts are rejected/fail closed.
12. Target-controlled syntax cannot select provider/model/profile/worker/candidate order/experimental opt-in/routing policy or expand authoring artifact paths.
13. Worker result provenance is not established by a Bot comment/file alone; wrong task/run/workflow/revision/role/stage/repository/ref/artifact-path provenance is rejected deterministically.
14. Existing autonomous Developer (`codex -> copilot`), Code Reviewer (`claude -> copilot`), and QA (`gemini -> copilot`) routing/result/security contracts remain green and semantically unchanged.
15. Product Acceptance remains manual and no new authoring worker/result translator can create or PASS `release-gate`, merge, or release state.
16. Final candidate CI must pass Protocol, Public Runtime Distribution, Required PR Gate, and the complete strict gh-aw compile matrix including all new authoring role workers.

## Evidence expectations

Durable implementation/review/verification evidence must record selected role-worker identities, profile/model metadata, result/provenance contract validation, artifact write-boundary tests, fail-closed negative tests, strict compiler identity, final CI run ids, and any deviations/remediation.
