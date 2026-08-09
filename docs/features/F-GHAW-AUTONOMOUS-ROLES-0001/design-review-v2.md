# Design Review v2 — F-GHAW-AUTONOMOUS-ROLES-0001

Verdict: **PASS_WITH_NOTES**

Severity summary:

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1

## DR-MAJOR-1 closure

PASS.

Design v2 introduces a deterministic draft implementation candidate artifact produced by trusted autonomous Developer result persistence. It is bound to the collector-resolved PR/head identity, coexists with manual implementation artifacts, preserves superseded history, and gives Reviewer PASS an exact artifact to approve without hard-coded ids.

QA is explicitly bound to the same approved reviewed candidate/head tuple and fails closed on head movement.

## Requirement Review notes

PASS:

- immutable PR/head candidate binding is concrete;
- Reviewer/QA use separate read-only worker variants and non-code Safe Output transport rather than inheriting Developer's mandatory create-PR path.

## MINOR-1 — Static capability guard for Gate-role workers

Implementation must add a deterministic validator over source and compiled Gate-role workers proving that Reviewer/QA variants do not expose code-writing Safe Outputs (`create-pull-request`, `push-to-pull-request-branch`, or equivalent source mutation capabilities) and that their result comment target is the trusted candidate PR/repository.

This is non-blocking because the Design already requires the boundary; the validator is an implementation hardening requirement and Design-review checkpoint for Code Review/QA.

## Authority assessment

PASS:

- exact role+stage autonomous routes prevent Requirement/Design Review automation;
- provider routing remains trusted and experimental providers remain excluded;
- Gate workers cannot directly persist Feature state;
- trusted collector performs role-specific verdict translation;
- stale revision/candidate mismatch fails closed;
- remediation preserves independent re-review;
- QA cannot perform Acceptance;
- merge/release authority remains unchanged.

## Conclusion

Design v2 is approved for implementation planning, subject to MINOR-1 being implemented and independently checked later.
