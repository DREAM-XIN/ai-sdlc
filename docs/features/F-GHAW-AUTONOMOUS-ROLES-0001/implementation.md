# Implementation Evidence — F-GHAW-AUTONOMOUS-ROLES-0001

Feature: `F-GHAW-AUTONOMOUS-ROLES-0001`

Issue: `#202`

PR: `#203`

Lifecycle owner for this document: Implementation Developer. This evidence does not approve `code-gate`, `verification-gate`, or `release-gate`.

## Summary

This implementation adds bounded autonomous execution for independent Code Review and Verification QA while keeping lifecycle authority in trusted Event/Persist control code.

The production autonomous matrix is now:

```text
developer + implementation -> codex -> copilot
reviewer  + code-review    -> claude -> copilot
qa        + verification   -> gemini -> copilot
```

Requirement Review, Design Review, Product, Architect, Orchestrator and Product Acceptance remain manual. Experimental provider profiles remain excluded from these default production routes.

## WU-1 — Trusted implementation candidate records

Implemented in:

- `scripts/gh_aw_candidate.py`;
- `scripts/gh_aw_candidate_event.py`;
- `.github/workflows/ai-sdlc-gh-aw-result.yml`.

The Developer worker may report the Safe Output PR URL, but the trusted result collector re-resolves the canonical PR through GitHub and obtains the actual PR head SHA before adding candidate records to the Feature Event.

The resulting autonomous candidate tuple is:

- draft `implementation-candidate-<sha-prefix>` -> canonical PR URL;
- draft `implementation-head-<sha-prefix>` -> immutable commit URL.

Later draft candidates supersede earlier current draft candidate history rather than rebinding old records. Reviewer PASS also supersedes other old current draft/approved candidate-history records before making the newly reviewed tuple current, so later QA resolves one current approved candidate.

Manual compatibility is explicit rather than guessed: a manual `type: implementation` artifact can enter the same resolver when trusted state binds it to a canonical PR URL and exactly one same-repository `implementation-head`. A documentation-only implementation artifact is not guessed to be a PR candidate and remains eligible for the manual review path.

Deterministic coverage:

- `scripts/validate_gh_aw_autonomous_roles.py`;
- `scripts/validate_gh_aw_candidate_history.py`.

## WU-2 — Exact autonomous role dispatch policy

Modified `dispatch/gh-aw-developer.yaml` to authorize exactly:

- `developer + implementation`;
- `reviewer + code-review`;
- `qa + verification`.

Negative routing fixtures prove Requirement Review, Design Review and out-of-scope Product/Architect/Acceptance stages remain manual.

## WU-3 — Role-worker registry and resolver

Added:

- `runtimes/gh-aw/role-workers.yaml`;
- `scripts/gh_aw_role_workers.py`.

The registry contains exactly four Gate worker variants:

- Reviewer / Code Review / Claude;
- Reviewer / Code Review / Copilot;
- QA / Verification / Gemini;
- QA / Verification / Copilot.

Validation cross-checks the Provider Registry, trusted profile-routing policy, exact role/stage set and canonical unique source/lock identity. No provider-name-specific production branch is used to choose a Gate worker.

## WU-4 — Deterministic Gate workers and strict compile

Added `scripts/render_gh_aw_gate_workers.py` and generated:

- `.github/workflows/ai-sdlc-gh-aw-reviewer-claude.md`;
- `.github/workflows/ai-sdlc-gh-aw-reviewer-copilot.md`;
- `.github/workflows/ai-sdlc-gh-aw-qa-gemini.md`;
- `.github/workflows/ai-sdlc-gh-aw-qa-copilot.md`;
- corresponding four `.lock.yml` files.

Generated workers:

- use `permissions: read-all` for the agent;
- checkout the exact `candidate_head_sha`;
- expose only bounded `add-comment` Safe Output;
- do not expose create-PR/push Safe Outputs;
- dispatch the non-authoritative result comment to the trusted Gate-result collector.

The materialization path uses pinned `github/gh-aw v0.83.4` with `--strict`. The PR compile workflow now validates 8 existing engine/profile workers plus 4 Gate-role workers, for 12 strict compile targets.

Static source and compiled-lock write-capability guards are in `scripts/validate_gh_aw_gate_worker_security.py`.

## WU-5 — Reviewer and QA result schemas

Added:

- `runtimes/gh-aw/reviewer-result.schema.json`;
- `runtimes/gh-aw/qa-result.schema.json`.

Reviewer verdicts are closed to `PASS`, `REWORK`, `BLOCKED`. QA verdicts are closed to `PASS`, `FAIL`, `BLOCKED`. The schemas bind feature/task/stage/role/revision/repository/ref/candidate PR/head identity and reject unsupported fields.

## WU-6 — Trusted Gate-result translator

Added `scripts/gh_aw_gate_result.py`.

The translator is separate from the generic Developer `result_to_event()` path. Gate workers cannot send arbitrary Feature Event `changes`.

Reviewer PASS can produce only the approved bounded changes: review Evidence, exact candidate/head approval, reviewed-head record, `code-gate PASS`, `code-review DONE`, and `verification READY`.

Reviewer REWORK persists review Evidence and creates a bounded Developer remediation task while leaving Gate PASS absent. The Reviewer does not implement remediation.

QA PASS can produce Verification Evidence, verified-head record, `verification-gate PASS`, `verification DONE`, and `acceptance READY`. QA has no `release-gate` authority. FAIL/BLOCKED never advances Acceptance.

## WU-7 — Trusted Gate-result collector

Added `.github/workflows/ai-sdlc-gh-aw-gate-result.yml`.

The collector:

1. resolves the exact target repository and bounded Runtime App token when cross-repository;
2. re-reads the target Manifest and expected revision;
3. fetches the exact candidate PR and verifies current head SHA;
4. fetches the exact Safe Output comment and validates PR/comment identity and Bot author type;
5. accepts exactly one `AI-SDLC-GATE-RESULT` machine envelope;
6. validates trusted identity fields and closed role-specific schema;
7. normalizes Evidence URI to the durable trusted comment URL;
8. translates the recommendation through trusted code;
9. runs normal Event ingestion and Manifest validation;
10. re-checks candidate PR head again immediately before persistence;
11. persists only through the normal validated Feature Event path.

The Safe Output comment itself has zero direct lifecycle authority.

## WU-8 — Same-repository and cross-repository gateway integration

Modified:

- `scripts/gh_aw_adapter.py`;
- `runtimes/gh-aw/dispatch-plan.schema.json`;
- `.github/workflows/ai-sdlc-gh-aw-dispatch.yml`;
- `.github/workflows/ai-sdlc-gh-aw-cross-repo-dispatch.yml`;
- `scripts/gh_aw_candidate_dispatch_guard.py`.

The adapter maps the selected engine profile to `(role, stage, profile)` Gate-worker identity and injects the trusted candidate PR/head into Gate dispatch plans. Developer plans remain backward compatible.

Both gateways re-fetch the current candidate PR head immediately before the actual worker dispatch. A moved head fails closed. Cross-repository PR checks use the Runtime App read token scoped to the exact target repository.

Candidate identity is therefore checked at three meaningful boundaries:

- immediately before Gate worker dispatch;
- when the Gate result collector begins;
- immediately before lifecycle persistence.

## WU-9 — Security and lifecycle regression suite

Added/extended deterministic checks for:

- exact autonomous role set and manual negative roles;
- role-worker Registry constraints;
- candidate creation/resolution and ambiguity;
- manual PR-bound implementation candidate compatibility;
- multi-round candidate supersession;
- Reviewer PASS and REWORK;
- Reviewer stale candidate rejection;
- QA PASS and FAIL/no Acceptance advance;
- QA unsupported authority-field rejection;
- candidate dispatch head movement fail closed;
- Gate source/compiled-lock no-write capability;
- existing profile routing and provider compatibility;
- existing Developer path through the unchanged generic worker result contract;
- public runtime/workflow security.

Primary files:

- `scripts/validate_gh_aw_autonomous_roles.py`;
- `scripts/validate_gh_aw_candidate_history.py`;
- `scripts/validate_gh_aw_gate_worker_security.py`;
- standard `scripts/validate.py` integration;
- updated 12-target `.github/workflows/compile-gh-aw-worker.yml`.

## WU-10 — Operator documentation

Added `docs/autonomous-gate-roles.md` documenting:

- role/provider routing;
- immutable candidate binding;
- read-only Gate workers;
- non-authoritative Safe Output results;
- trusted collector/Event/Persist authority;
- Reviewer remediation/re-review;
- QA failure semantics;
- static readiness fallback versus live runtime failure;
- same/cross-repository candidate checks;
- manual fallback;
- Product Acceptance remaining manual.

Existing `docs/autonomous-development.md` and `docs/role-guide.md` remain valid for the manual/Developer paths; the new Gate-role guide is the current extension for autonomous Code Review/Verification.

## Requirement acceptance-criteria mapping

- AC1: exact runtime route set -> `dispatch/gh-aw-developer.yaml`, routing regressions.
- AC2: Reviewer Claude->Copilot and candidate binding -> profile routing + role-worker Registry + adapter/guard.
- AC3: QA Gemini->Copilot and candidate binding -> same trusted stack.
- AC4: role-specific schemas/translator separate from Developer generic completion.
- AC5: Reviewer PASS closed translation and Event validation.
- AC6: Reviewer REWORK -> bounded Developer remediation task, no Gate PASS.
- AC7: QA PASS closed translation; no release authority.
- AC8: stale revision/role/stage/repo/ref/PR/head/schema failures fail closed.
- AC9: source and strict-lock no-write security validation.
- AC10: target command/provider/profile/worker/verdict authority remains closed by existing command boundary plus trusted routing.
- AC11: Manifest/Event/Gate/Persist/merge/release authority unchanged.
- AC12: generic Developer worker/result path preserved.
- AC13: 8 existing profile workers remain in strict compile; 4 Gate variants are additive.
- AC14: deterministic Reviewer/QA/identity/isolation/failure regressions integrated into standard validation.
- AC15: final required CI must be green on the final implementation candidate before IMPL-DONE.
- AC16: `docs/autonomous-gate-roles.md` provides the operator boundary and failure/remediation guide.

## CI evidence

A pre-documentation implementation candidate `420903c215f5aef39a5b2b541e08d141f0a7e308` passed all required PR workflows:

- Validate AI-SDLC protocol — run `31317172581` — SUCCESS;
- Validate Public Runtime Distribution — run `31317172598` — SUCCESS;
- Validate AI-SDLC gh-aw Worker Compile — run `31317172603` — SUCCESS;
- Required PR Gate — run `31317172575` — SUCCESS.

A later candidate `2e6e287f1f1a31d02e46a2a4fe12ff7f6f5ebe19`, including multi-round candidate supersession, also passed all four required workflows:

- Validate Public Runtime Distribution — run `31317495139` — SUCCESS;
- Validate AI-SDLC protocol — run `31317495137` — SUCCESS;
- Validate AI-SDLC gh-aw Worker Compile — run `31317495181` — SUCCESS;
- Required PR Gate — run `31317495177` — SUCCESS.

The final documentation/evidence head must independently pass the same four workflows before Implementation is declared DONE.

## Known bounded limitations / non-goals

- Product/Requirement Review/Architect/Design Review/Orchestrator/Acceptance are not made autonomous by this Feature.
- Provider readiness remains static/presence based; a live model failure does not automatically retry another provider.
- DeepSeek/Qwen/GLM/MiniMax remain experimental and outside default Gate routing.
- Gate-role worker comments are transport, not lifecycle authority.
- Documentation-only manual implementation artifacts are not guessed into autonomous PR candidates; manual review remains the fallback unless trusted PR/head binding exists.
- This implementation does not grant merge or release authority to any worker.
