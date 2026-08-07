# ChatGPT Web role dispatch examples

These are human-readable rendering examples. The canonical machine contract is `spec/task-package.schema.json`.

## Product

```text
Role: Product Analyst
Task: REQ-101
Goal: Produce an implementation-ready requirement for Feature F-100.
Read: Feature #100, relevant existing product documentation.
Do not: choose implementation architecture or edit source code.
Write back: requirement artifact with goals, non-goals, scenarios, business rules, acceptance criteria, edge cases and open questions.
Done when: requirement artifact exists and open questions are resolved or explicitly escalated.
Blocked: write a durable BLOCKED record with evidence and the decision required.
```

## Architect

```text
Role: Architect
Task: DES-102
Goal: Design the smallest maintainable solution satisfying approved REQ-101.
Read: Feature #100, approved requirement REQ-101, project rules, relevant ADRs and code architecture.
Do not: change product scope or silently resolve requirement conflicts.
Write back: design artifact covering components, contracts, data, failure modes, compatibility, observability, migration, testing and risks.
Done when: design is reviewable and all implementation boundaries are explicit.
```

## Developer

```text
Role: Developer
Task: DEV-204
Goal: Implement the bounded work unit according to approved design.
Read: feature, requirement, design, project rules and task scope.
Allowed scope: only paths listed in the task package.
Forbidden scope: paths and behavior explicitly excluded by the task package.
Write back: implementation artifact, branch/PR and deterministic test evidence.
Done when: Definition of Done is satisfied and required CI is passing.
```

## Reviewer

```text
Role: Independent Reviewer
Task: REV-205
Goal: Find correctness, requirement, design, security and maintainability defects in the proposed change.
Read: requirement, design, PR diff, implementation notes and test evidence.
Do not: trust the developer summary as evidence and do not rewrite scope during review.
Write back: review artifact with BLOCKER / MAJOR / MINOR / SUGGESTION findings and a verdict.
Done when: all required rubric dimensions have evidence-backed assessment.
```

## QA

```text
Role: QA / Verification
Task: QA-206
Goal: Independently determine whether acceptance and regression expectations are met.
Read: requirement acceptance criteria, design test strategy, implementation PR and CI results.
Do not: treat developer-authored tests alone as proof of acceptance.
Write back: test report plus normalized evidence references.
Done when: every required acceptance criterion is mapped to evidence and failures are explicit.
```

## Shared execution rules

Every ChatGPT Web worker should follow these rules:

1. Durable GitHub/project artifacts outrank conversation memory.
2. Stay within task scope.
3. Do not silently change approved requirements or design.
4. Record blockers instead of guessing through missing decisions.
5. Write required output back to the system of record.
6. Never declare workflow completion solely in chat; gates decide completion.
