# Code Review v2 — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Review identity

Role: **independent Code Reviewer**  
Feature: `F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001`  
Issue: `#204`  
PR: `#207`  
Remediation implementation candidate reviewed: `7a9029ae8f48416c477f32df05ff530ed86891b5`  
Lifecycle head at review: `6c8b61689ac41ec9a1b0f46015c333185d6fac8c`  
Manifest revision at re-review: `17`

The re-review independently re-read the approved Requirement, Design, Design Review note, Code Review v1 findings, remediation diff, deterministic validation, and current lifecycle state. Commits after the remediation candidate were confirmed to contain only remediation/re-review Feature Events and trusted Manifest materialization; no additional production-code changes were introduced.

## Verdict

**PASS_WITH_NOTE**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0
- SUGGESTION: 1

The implementation is eligible for `code-gate: PASS` once this durable Review Evidence-bearing head itself passes the required PR checks.

## Re-review of v1 findings

### MAJOR-1 — durable exact source-run provenance: CLOSED

The trusted collector still validates the source Actions run/workflow/task/revision through `gh_aw_authoring_provenance.py`. The translator now consumes the collector's validated `source-run.json` identity and:

- validates `source_run_id` as a positive integer;
- validates control repository as canonical `owner/repo`;
- includes `source_run_id` in deterministic evidence/Event identity;
- persists the exact Actions run URI `https://github.com/<control-repository>/actions/runs/<source_run_id>` as Feature Evidence;
- therefore no longer treats the Bot Safe Output comment as the sole durable provenance identity.

Deterministic tests prove that two otherwise identical authoring results from different trusted source runs generate different evidence ids and Event ids, and malformed trusted source-run identities fail closed.

This closes approved Requirement AC6/AC13 and the Design replay/provenance requirement without giving the model or target repository any new authority.

### MINOR-1 / DR-MINOR-1 — explicit path-negative matrix: CLOSED

The deterministic authoring validator now explicitly rejects model-supplied path fields for:

- traversal (`../outside.md`);
- unrelated `docs/**`;
- `state/**`;
- `.github/**`.

It also rejects a traversal Feature id passed to canonical path construction. The only accepted authoritative destinations remain the three trusted table entries for requirement, design, and plan.

## Security and authority review

- Exact role+stage autonomous routing remains unchanged by remediation.
- Experimental providers remain excluded from production authoring routes.
- Authoring worker sources remain read-only and constrained to Safe Output comments.
- The model still cannot supply repository destination path, Event changes, Gate status, provider, profile, model, worker, or candidate order.
- Trusted writer persistence remains restricted to a non-default Feature branch.
- Authoring translation still emits no Gate changes.
- Product Acceptance, Requirement Review, Design Review, merge, release, and release-gate authority remain outside autonomous authoring.
- Existing Developer/Reviewer/QA shared security paths remain green.

## Deterministic checks reviewed

Remediation candidate `7a9029ae8f48416c477f32df05ff530ed86891b5`:

| Required check | Run | Result |
|---|---:|---|
| Validate AI-SDLC protocol | `31323327186` | SUCCESS |
| Validate AI-SDLC gh-aw Worker Compile | `31323327180` | SUCCESS — complete 18-target matrix |
| Required PR Gate | `31323327187` | SUCCESS |
| Validate Public Runtime Distribution | `31323327176` | SUCCESS |

The Protocol run includes the remediated durable source-run provenance tests and explicit forbidden-path negative matrix.

## Residual note

**SUGGESTION-1:** `scripts/gh_aw_adapter.py` still has an unused `GATE_ROLE_STAGES` import. It has no behavioral, security, data-integrity, or compatibility effect and does not justify another implementation churn cycle for this high-risk Feature after the required fixes have passed the full deterministic matrix.

## Gate recommendation

**PASS.** After the Review Evidence-bearing head itself is green, `implementation-v1` may be approved with `evidence-code-review-v2`, `code-gate` may PASS, `code-review` may become DONE, and `verification` may become READY. This Review does not approve `verification-gate` or `release-gate`.