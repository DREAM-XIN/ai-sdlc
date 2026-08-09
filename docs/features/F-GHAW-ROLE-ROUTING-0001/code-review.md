# Code Review — F-GHAW-ROLE-ROUTING-0001

Feature: `F-GHAW-ROLE-ROUTING-0001`

Issue: `#200`

PR: `#201`

Verdict: **REWORK**

Severity summary:

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

## Scope reviewed

Independent review covered the approved Requirement, Requirement Review notes, approved/remediated Design, Plan checkpoints, Implementation Evidence, PR #201 changed files, trusted Registry credential-source migration, routing/readiness libraries, same-repository and cross-repository workflow integration, Issue Comment command path, generated readiness surfaces, manual diagnostic profile path, and required CI evidence.

The implementation candidate had green protocol/public-runtime/8-profile strict compile/Required PR Gate CI before lifecycle completion. Green CI is treated as supporting evidence, not as a substitute for independent review.

## Positive findings

- Credential source semantics are metadata-driven (`secret` / `github-token`) rather than profile-name-specific.
- `github-token` profiles fail closed on credential aliases.
- Default routing excludes experimental profiles; trusted experimental opt-in is explicit.
- Developer normal routing is Codex → Copilot static-readiness fallback.
- Reviewer/QA routes remain policy/audit-only; their runtime roles remain manual.
- Normal Issue Comment dispatch was correctly moved away from the manual profile gateway so target commands cannot silently force the Copilot default.
- Target command parsing does not expose profile/provider/model/credential/worker/candidate-order/experimental-routing selectors.
- Manual trusted profile selection is audit-distinct as `manual-trusted-profile`.
- Routing validates the complete Boolean readiness map before selection.
- No live-provider retry/circuit-breaker semantics were introduced.
- Routing/preflight output keeps `entitlement_verified: false` and does not serialize secret values.

## MAJOR — CR-MAJOR-1 — Preferred-selection audit omits the full ordered candidate list

Approved Requirement AC8 requires routing evidence to record the routing rule, lifecycle context, **ordered candidates**, selected profile/engine/provider/model/worker, and deterministic skip/fallback reasons.

Current `scripts/gh_aw_profile_routing.py` uses one `decisions` list for both candidate-order evidence and evaluated decisions. Resolution stops immediately after the first ready candidate is selected. Therefore, when the preferred Developer candidate `codex` is ready, the emitted audit JSON contains only:

```json
"candidates": [
  {"profile":"codex","ready":true,"reason":"SELECTED"}
]
```

and omits the policy fallback candidate `copilot`. The full policy order `[codex, copilot]` is not recorded in the routing evidence itself.

A consumer could reconstruct it later by loading the matching policy version/rule id, but that is not equivalent to the approved requirement that the routing evidence record the ordered candidates. It also weakens standalone auditability of retained `gh-aw-routing.json` artifacts.

### Required remediation

Preserve deterministic selection behavior, but make the audit contract explicitly carry the complete ordered candidate list independently from the evaluated/decision list. Acceptable shapes include, for example:

```json
"candidate_order": ["codex", "copilot"],
"candidates": [
  {"profile":"codex","ready":true,"reason":"SELECTED"}
]
```

or an equivalent representation where every policy candidate is present and non-evaluated tail candidates have an unambiguous deterministic state that does not claim readiness evaluation after selection.

Regression coverage must prove at least:

1. preferred selection still records the complete `[codex, copilot]` order;
2. fallback selection records the same complete order plus deterministic skip/selection decisions;
3. no-ready failure remains fail closed;
4. audit contains no secret values and does not overclaim entitlement;
5. no provider/profile-name-specific branch is introduced.

No other implementation scope should be expanded as part of this remediation.

## Gate decision

`code-gate` is **not passed**. Implementation requires bounded Developer remediation followed by a fresh independent Code Review.
