# Requirement — Bounded autonomous Reviewer and QA gh-aw roles

Feature: `F-GHAW-AUTONOMOUS-ROLES-0001`

Issue: `#202`

Profile: `standard-feature`

## Problem

AI-SDLC currently supports bounded autonomous Developer execution through gh-aw, and the trusted role-routing policy already defines `reviewer + code-review -> claude -> copilot` and `qa + verification -> gemini -> copilot`. However, Code Review and Verification are still manual lifecycle stages. The existing gh-aw worker/result adapter is Developer-oriented: a generic worker `COMPLETED` result is translated directly into `stage DONE`, which is not a safe contract for independent Gate roles.

Reviewer and QA automation must therefore add role-specific output semantics without moving Gate, Feature Manifest, merge, or release authority into the model worker.

## Goal

Add bounded autonomous gh-aw execution for:

- independent Code Reviewer at `code-review`;
- independent QA at `verification`.

The trusted control plane must dispatch those roles through existing role-aware profile routing, validate role-specific structured results, persist durable Evidence, and translate only valid verdicts into lifecycle Events through the existing trusted Event/Persist path.

## Initial autonomous role matrix

The v1 autonomous matrix is:

- `developer + implementation`: existing autonomous behavior, `codex -> copilot`;
- `reviewer + code-review`: new autonomous behavior, `claude -> copilot`;
- `qa + verification`: new autonomous behavior, `gemini -> copilot`.

The following remain manual in this Feature:

- Product / requirement author;
- Requirement Reviewer;
- Architect;
- Design Reviewer;
- Orchestrator / plan;
- Acceptance Product Owner.

This Feature must not implicitly make a broader role autonomous just because a routing rule exists.

## Independence requirements

Autonomous Reviewer and QA must remain logically independent from prior workers.

For Code Review:

- the reviewer worker must receive the current approved Requirement, Design, Plan, implementation evidence, actual PR/diff identity, and current CI context;
- it must not reuse Developer authority or edit implementation while issuing the same review verdict;
- if remediation is required, the trusted lifecycle must create/route a separate Developer remediation task before any subsequent independent re-review.

For QA:

- the QA worker must receive approved artifacts, implementation evidence, Code Review evidence, the actual current PR/head, and applicable deterministic commands/acceptance criteria;
- it must not edit implementation while declaring Verification PASS;
- it must not perform Product Acceptance or release decisions.

Worker/provider identity alone does not establish independence. Independence is a role/stage/output-contract boundary enforced by trusted dispatch and result validation.

## Role-specific result contracts

The existing generic Developer result contract must not be reused with `COMPLETED => stage DONE` semantics for Reviewer or QA.

The control plane must define strict structured result semantics for Gate roles.

### Reviewer result

A Reviewer result must contain at least:

- result contract/version;
- feature id;
- task/work-unit id;
- `stage: code-review`;
- `role: reviewer`;
- expected Manifest revision;
- reviewed PR/revision identity sufficient to bind the verdict to the reviewed candidate;
- verdict from a closed set such as `PASS`, `REWORK`, or `FAIL/BLOCKED` as approved by Design;
- severity counts/findings or a durable findings artifact reference;
- durable review Evidence records;
- occurred-at timestamp;
- no secret values.

A Reviewer PASS may be translated by trusted control logic into the normal Code Review lifecycle changes only when all required review Evidence and identity checks succeed. A REWORK verdict must not PASS `code-gate`; it must create or request bounded remediation through the existing remediation lifecycle contract.

### QA result

A QA result must contain at least:

- result contract/version;
- feature id;
- task/work-unit id;
- `stage: verification`;
- `role: qa`;
- expected Manifest revision;
- verified PR/head identity;
- verdict from a strict closed set;
- executed/check evidence or durable verification artifact references;
- acceptance-criterion coverage sufficient for the Feature risk/profile;
- occurred-at timestamp;
- no secret values.

A QA PASS may be translated by trusted control logic into `verification-gate PASS`, `verification DONE`, and `acceptance READY` only when required Evidence and candidate identity checks succeed. QA cannot PASS `release-gate`.

## Trusted verdict translation

Gate-role workers produce **recommendations plus Evidence**, not authoritative Feature Events.

The trusted collector/adapter must:

1. validate the result JSON against the exact role-specific schema;
2. verify feature id, task id, role, stage, expected revision, target repository/ref and candidate PR/head identity against trusted dispatch context/current state;
3. reject unknown fields or unsupported verdicts where schema strictness applies;
4. reject role/stage mismatches;
5. reject stale expected revisions;
6. reject missing/invalid Evidence;
7. translate a valid verdict into the existing Feature Event vocabulary;
8. pass that Event through the existing Inbox/transition/optimistic-concurrency/Persist validators;
9. never let raw worker JSON directly rewrite `state/features/**`.

A malformed, stale, ambiguous or identity-mismatched result fails closed.

## Dispatch requirements

The Runtime Router/dispatch policy must explicitly authorize autonomous gh-aw execution for exactly the new role/stage pairs in scope.

Normal autonomous dispatch must:

- derive role/stage from trusted Commander/lifecycle context;
- resolve the provider profile through `profile-routing.yaml`;
- preserve static-readiness-only fallback semantics;
- preserve exact registered worker allowlisting and strict compiled worker validation;
- preserve cross-repository target/ref/revision identity checks;
- preserve one Feature revision / one autonomous dispatch serialization semantics;
- produce durable non-secret routing audit data.

Target repositories and Issue Comments must not choose role, provider, model, profile, credential, worker workflow, candidate order, verdict, or `allow_experimental`.

## Worker capability boundaries

Reviewer and QA workers may read the target repository, Feature artifacts, Issue/PR context and deterministic test output required for their stage.

They must not:

- directly modify `state/features/**`;
- directly modify `state/events/**`;
- merge or release;
- change Gate policy or runtime trust configuration in the target repository;
- silently modify implementation and then approve/verify that same modification;
- broaden role ownership;
- expose provider secrets or runtime app credentials;
- claim live provider entitlement solely from credential presence.

If durable review/verification Evidence must be stored in the target repository, the output path and write mechanism must be bounded and collector/Safe-Output controlled as defined by the approved Design. Worker lifecycle authority must remain zero.

## Reviewer remediation semantics

Reviewer automation must preserve the existing durable remediation model.

For a Reviewer REWORK result:

- review Evidence is persisted as failure/rework Evidence;
- `code-gate` remains PENDING;
- a bounded remediation task is created with `role: developer`, `source_stage: code-review`, actionable feedback, and trusted identity;
- the reviewer worker must not fix the issue itself;
- after remediation DONE, a new independent Code Review dispatch/revision is required before PASS.

No historical failed review Evidence may be overwritten or erased.

## QA failure semantics

For a QA non-PASS result:

- verification Evidence is persisted truthfully;
- `verification-gate` remains PENDING;
- the Feature does not advance to Acceptance;
- any required implementation remediation must use an explicit lifecycle task/event path approved by the Design rather than allowing QA to modify code and self-reverify.

## Provider/routing policy

Default production routing remains:

- Developer: `codex -> copilot`;
- Reviewer: `claude -> copilot`;
- QA: `gemini -> copilot`.

`deepseek`, `qwen`, `glm`, and `minimax` remain experimental and are not added to default production routes by this Feature.

Provider readiness remains static/presence-only. Runtime inference failure does not automatically trigger another provider.

## Authority boundary

This Feature must preserve unchanged:

- authoritative Feature Manifest ownership;
- Feature Event event sourcing;
- optimistic `expected_revision` semantics;
- Requirement/Design/Code/Verification/Release Gate definitions;
- trusted Persist validation;
- Safe Output semantics;
- Runtime GitHub App trust and least privilege;
- merge authority;
- release authority;
- Product Acceptance as a separate manual role.

Automation changes who executes bounded review/verification work, not who owns authoritative lifecycle persistence.

## Compatibility

Existing autonomous Developer execution must remain backward compatible.

All eight registered gh-aw profiles must remain valid/strictly compilable. `copilot` remains the global compatibility default/fallback. Existing manual trusted profile diagnostics remain available without becoming a target-controlled bypass.

Manual Code Review and QA execution must remain possible when autonomous routing is unavailable or deliberately not configured, according to the existing lifecycle protocol.

## Acceptance criteria

1. Runtime routing/dispatch supports exactly `developer+implementation`, `reviewer+code-review`, and `qa+verification` as the default autonomous gh-aw stage/role set; other lifecycle roles remain manual.
2. Autonomous Code Review uses trusted role routing (`claude -> copilot`) and binds the review result to the trusted Feature/revision/PR candidate.
3. Autonomous QA uses trusted role routing (`gemini -> copilot`) and binds Verification to the trusted Feature/revision/PR head.
4. Reviewer and QA use strict role-specific result contracts; generic Developer `COMPLETED => stage DONE` semantics cannot complete either Gate stage.
5. A valid Reviewer PASS can be translated only by trusted collector logic into durable review Evidence, implementation approval, `code-gate PASS`, `code-review DONE`, and `verification READY` through the existing Feature Event/Persist validators.
6. A valid Reviewer REWORK result records durable failed/rework Evidence and creates a bounded Developer remediation task while leaving `code-gate` PENDING; a later independent review is required.
7. A valid QA PASS can be translated only by trusted collector logic into durable Verification Evidence, `verification-gate PASS`, `verification DONE`, and `acceptance READY`; QA cannot PASS `release-gate`.
8. Reviewer/QA stale revision, role/stage mismatch, wrong target repo/ref, wrong PR/head identity, malformed verdict, missing Evidence, unknown result contract, or unsupported fields fail closed.
9. Autonomous Reviewer cannot edit implementation while issuing its verdict; autonomous QA cannot edit implementation while issuing Verification; write boundaries are deterministic and tested.
10. Target Issue Comments/Project Adapter/worker payloads cannot choose provider/model/profile/credential/worker/candidate order/experimental opt-in or inject a Gate verdict.
11. Existing Feature Manifest/Event/Gate authority, Safe Output, Runtime App, merge/release authority and manual Acceptance remain unchanged.
12. Existing autonomous Developer flow remains green and backward compatible.
13. Existing 8-profile Registry/render/preflight/effective-model/security/strict-compile regressions remain green.
14. Deterministic tests cover Reviewer PASS, Reviewer REWORK/remediation, stale/mismatched Reviewer result, QA PASS, QA failure, stale/mismatched QA result, role isolation, target selector rejection, and non-secret result/audit output.
15. Final protocol/security/public-runtime/strict-worker-compile required CI is green on the final lifecycle candidate.
16. User/operator documentation explains autonomous role boundaries, routing, fallback semantics, manual fallback, remediation/re-review and why workers still cannot self-approve lifecycle state directly.

## Evidence expected

- approved Requirement and Design;
- role-specific schemas and positive/negative fixtures;
- Runtime Router/dispatch policy tests for exact autonomous role set;
- trusted result translation tests for Reviewer and QA verdicts;
- reviewer remediation closed-loop tests;
- QA failure/no-advance tests;
- cross-repository identity/revision/PR-head binding tests;
- role write-boundary/security tests;
- routing audit evidence showing Claude/Copilot and Gemini/Copilot selection without secrets;
- existing Developer and 8-profile compatibility regressions;
- final required CI results;
- updated operator/role/autonomous execution documentation.

## Non-goals

- No autonomous Product, Requirement Reviewer, Architect, Design Reviewer, Orchestrator, or Acceptance.
- No new provider.
- No provider maturity promotion.
- No live-runtime provider retry/circuit breaker.
- No cost/latency/quality/adaptive routing.
- No worker direct Gate/Manifest/Event authority.
- No target-controlled provider/model/profile/worker/verdict selection.
- No merge/release authority change.
