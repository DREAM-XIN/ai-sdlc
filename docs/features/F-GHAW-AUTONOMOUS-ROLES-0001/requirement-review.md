# Requirement Review — F-GHAW-AUTONOMOUS-ROLES-0001

Verdict: **PASS_WITH_NOTES**

Severity summary:

- BLOCKER: 0
- MAJOR: 0
- MINOR: 2

## Review basis

The Requirement was reviewed against the current AI-SDLC role guide, existing autonomous Developer boundary, trusted gh-aw result adapter/collector behavior, merged role-routing policy, and standard-feature Gate separation.

The scope is appropriately bounded to autonomous Code Reviewer and Verification QA. It explicitly preserves Feature Manifest/Event/Gate authority, keeps Product/Architect/Acceptance manual, prohibits worker self-approval, and requires role-specific structured results rather than reusing generic Developer `COMPLETED => stage DONE` semantics.

## MINOR-1 — Candidate identity must be concrete in Design

The Requirement correctly requires Reviewer and QA results to bind to the trusted PR/head candidate, but Design must define the exact immutable identity and advancement rule. At minimum it should carry a trusted PR number plus head SHA/ref or an equivalent immutable candidate identity.

QA must verify the same candidate that passed Code Review. If the candidate changes after Code Review, Verification must fail closed or require a new independent review path rather than silently verifying a newer head.

This is non-blocking because AC2, AC3 and AC8 already require candidate identity binding; the remaining work is concrete interface design.

## MINOR-2 — Reviewer/QA must not inherit Developer write-style Safe Output

The existing autonomous Developer worker is intentionally a write-producing worker and requires a Safe Output Draft PR. That contract is not suitable for independent Reviewer/QA.

Design must provide a read-only review/verification worker path or equivalent bounded evidence-return mechanism that does not create source PRs, does not obtain source write authority, and does not require a worker to modify the target repository merely to submit its verdict.

This is non-blocking because AC9 and the Worker Capability Boundaries already prohibit implementation edits by Reviewer/QA; Design must make the mechanism enforce that intent.

## Security and authority assessment

PASS:

- autonomous roles remain role/stage bounded;
- target-controlled provider/model/profile/worker/verdict selectors remain prohibited;
- experimental profiles remain out of default production routes;
- workers have zero direct Manifest/Event/Gate/merge/release authority;
- Reviewer REWORK preserves durable remediation and re-review separation;
- QA cannot perform Acceptance;
- existing Developer compatibility is explicitly required.

## Conclusion

The Requirement is clear, testable, appropriately scoped and safe enough to proceed to Design. The two MINOR notes are mandatory Design-review checkpoints but do not block `requirement-gate` PASS.
