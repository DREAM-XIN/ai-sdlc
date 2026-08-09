# Plan — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## WU-1 — Exact runtime and profile routing

Add exact autonomous routes for Product/requirement, Architect/design, and Orchestrator/plan. Add deterministic non-experimental profile rules with Copilot fallback. Prove Product/acceptance and review stages remain manual.

## WU-2 — Authoring role-worker registry and strict generation

Extend the trusted role-worker registry with Product/Architect/Orchestrator authoring identities and generated gh-aw worker variants. Authoring workers remain read-only and expose only bounded Safe Output; compile all generated locks with the pinned compiler.

## WU-3 — Closed authoring result schema

Add a role-specific authoring result schema/validator for `COMPLETED | BLOCKED`, Markdown body, task/revision/role/stage identity, and bounded summary. Reject arbitrary path, Event, Gate, provider, workflow, and lifecycle changes.

## WU-4 — Trusted canonical writer and artifact translator

Implement closed role+stage → artifact type/path mapping. Verify trusted run/workflow/task/revision provenance, write only canonical Feature doc paths, register draft artifacts, supersede prior current draft deterministically, and emit bounded stage-completion Events. Add replay/idempotency protection.

## WU-5 — Same-repo / cross-repo dispatch integration

Route eligible authoring stages through the same trusted gateway/Runtime App boundary. Preserve manual fallback and all existing Developer/Reviewer/QA behavior. Target-controlled commands cannot select profile/worker/path/candidate order/experimental mode.

## WU-6 — Security/regression/docs/evidence

Add negative fixtures for traversal/arbitrary `docs/**`/`state/**`/`.github/**`, wrong task/run/workflow/revision/role/stage, duplicate replay, and multi-draft ambiguity. Prove compiled authoring workers have no repository-write/PR/merge/release capability. Update operator docs and implementation evidence. Run Protocol, Public Runtime, Required PR Gate, and expanded strict worker compile matrix.

## Code Review checkpoints

- DR-MINOR-1 closed-table path mapping and negative path fixtures.
- Exact authoring scope only; Acceptance and review Gates manual.
- Authoring workers cannot directly write repository contents or lifecycle state.
- Trusted writer provenance is at least as strong as Gate-result collector provenance.
- Retry/remediation leaves exactly one current draft per artifact type.
- Existing Developer/Reviewer/QA behavior and target-control boundary remain green.
