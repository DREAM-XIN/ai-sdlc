# Code Review v1 — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Review identity

Role: **independent Code Reviewer**  
Feature: `F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001`  
Issue: `#204`  
PR: `#207`  
Implementation candidate reviewed: `82f5c7d22f98cd3aae8eb3b2abf63e937b474e82`  
Lifecycle head at review: `62c74507bd3d3a08b0c09d07653fcd94ae1644a2`  
Manifest revision at review: `13`

The lifecycle-only commits after the implementation candidate were inspected only to confirm legal `IMPL-DONE` and `CODE-REVIEW-START` persistence. The implementation judgment is against the implementation candidate and approved Requirement/Design/Plan.

## Verdict

**REWORK**

- BLOCKER: 0
- MAJOR: 1
- MINOR: 1
- SUGGESTION: 1

`code-gate` must remain `PENDING`.

## What passed review

- Exact autonomous scope is correctly limited to Product/`requirement`, Architect/`design`, and Orchestrator/`plan` while Product/`acceptance`, Requirement Review, and Design Review remain manual.
- Trusted profile routing is deterministic, uses the approved preferred profiles with Copilot fallback, and keeps experimental profiles out of production authoring routes.
- The specialized role-worker registry is closed and fail-closed for unexpected identities.
- Authoring results use a closed schema and cannot provide an authoritative path, Event payload, Gate mutation, provider selector, or workflow selector.
- Canonical artifact mapping is a trusted closed table; normal completion can only create the expected draft and bounded next-stage transition.
- `BLOCKED`, stale revision, ambiguous-current-draft, unsupported role/stage, and model-supplied path-field cases fail closed.
- The trusted collector executes on the control-plane default branch, validates current target revision/Issue identity, re-fetches Safe Output, and persists only to a non-default Feature branch.
- Existing Developer, Code Reviewer, and QA routing/security paths remain covered by the green shared Protocol regression suite.
- The implementation candidate and the Evidence-bearing head passed Protocol, complete 18-target strict compile, Required PR Gate, and Public Runtime Distribution.

## Findings

### MAJOR-1 — Durable authoring provenance does not retain the exact trusted source run

**Requirement/Design impact:** approved AC6 and AC13, the Trusted Artifact Contract, the Design provenance boundary, and the Design replay/idempotency rule require the authored artifact/evidence to remain attributable to the exact trusted worker run/task/revision. The Design specifically defines replay identity using `(task_id, expected_revision, source_run_id)` and says a generic Bot comment or unbound payload is insufficient.

**Observed implementation:** `.github/workflows/ai-sdlc-gh-aw-authoring-result.yml` correctly validates `source_run_id` and `source_workflow_ref` before translation. However, `scripts/gh_aw_authoring_result.py` receives only `comment_url` and `occurred_at` as trusted provenance inputs. Its durable evidence record stores `uri: comment_url`, and its deterministic evidence/replay digest is derived only from `task_id:expected_revision:stage`.

**Risk:** after persistence, the Feature Manifest/evidence history does not retain the exact source Actions run that was trusted at collection time. A later auditor cannot derive the authoritative run identity from the durable Feature evidence alone, and the persisted replay identity does not implement the approved source-run-bound key. The online collector check is good but does not satisfy the durable attribution contract.

**Required remediation:** bind the trusted `source_run_id` into translation from the collector, include it in the deterministic authoring evidence/replay identity, and persist a durable exact Actions-run URI (or equivalent schema-valid durable reference). Keep the Safe Output comment as transport evidence if useful, but do not make the comment the sole durable provenance identity. Add deterministic tests proving distinct/wrong source run identity cannot silently collapse into the same durable authoring evidence identity.

### MINOR-1 — DR-MINOR-1 path-negative matrix is not explicit enough

The approved Design Review required a Code Review checkpoint with negative fixtures covering traversal, arbitrary `docs/**`, `state/**`, `.github/**`, and model-supplied path fields. Current tests prove the closed schema rejects one model-supplied `path` targeting `state/features/**`, and the canonical mapping itself is closed, so the underlying implementation boundary is sound. However, the requested explicit negative regression matrix is incomplete.

**Required remediation:** parameterize negative path-field fixtures for at least traversal, unrelated `docs/**`, `state/**`, and `.github/**`, while keeping exact canonical-map assertions for the three supported role/stage pairs.

### SUGGESTION-1 — Remove unused `GATE_ROLE_STAGES` import

`scripts/gh_aw_adapter.py` imports `GATE_ROLE_STAGES` but does not use it. This has no correctness or security impact; clean it while touching the file if convenient.

## Rework boundary

Developer remediation must be limited to:

1. durable source-run provenance binding in the trusted authoring collector/translator and deterministic tests;
2. the explicit DR-MINOR-1 negative path regression matrix;
3. optional unused-import cleanup.

No routing, authority, provider maturity, Gate behavior, merge/release behavior, or broader product scope may change.

## Gate decision

**REWORK.** No Code Gate PASS is authorized by this review. After remediation, the implementation candidate must pass the required deterministic checks and receive a new independent Code Review before `code-gate` may PASS.