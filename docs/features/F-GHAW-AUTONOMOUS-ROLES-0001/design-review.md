# Design Review — F-GHAW-AUTONOMOUS-ROLES-0001

Verdict: **REWORK**

Severity summary:

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

## Review basis

The Design was independently checked against the approved Requirement, Requirement Review notes, current Provider/Role Routing implementation, current Developer worker/result adapter, Feature Manifest schema/remediation semantics, and gh-aw Safe Output security model.

The overall architecture is directionally correct: exact role+stage runtime routing, separate read-only Gate-role workers, immutable candidate SHA binding, role-worker registry, strict result schemas, and trusted verdict translation all preserve the intended authority boundaries.

## DR-MAJOR-1 — Autonomous implementation lacks a guaranteed approvable implementation artifact

The Design requires Reviewer PASS to approve the implementation artifact, but its Developer candidate-persistence section only guarantees two new artifacts: a PR URL and immutable head SHA/commit URL.

The current autonomous Developer result path does not guarantee that a draft `implementation-v1` (or any equivalent implementation artifact) exists in the Feature Manifest. Therefore the Reviewer PASS translator has no stable artifact target to approve for an autonomous implementation candidate. Hard-coding `implementation-v1` would break autonomous and other compatible paths; skipping artifact approval would violate the approved Requirement's lifecycle semantics.

### Required remediation

Design must define a deterministic **implementation candidate artifact** produced by the trusted Developer result collector whenever autonomous implementation completes. The artifact must:

- be recorded through a normal Feature Event `artifact-record` change;
- have type/identity that unambiguously represents the reviewed implementation candidate;
- be `draft` before independent review;
- be bound to the trusted PR/candidate head evidence;
- be the artifact the Code Reviewer PASS translator approves;
- coexist with existing manual `implementation-v1` artifacts without id collision or special-case assumptions;
- preserve historical candidate artifacts when a new implementation/remediation candidate supersedes the old one.

The Gate-role translator must resolve the candidate implementation artifact from current trusted Manifest/candidate context rather than hard-code a specific artifact id.

## Requirement Review MINOR closure status

- Candidate PR/head immutable binding: addressed by Design.
- Reviewer/QA read-only output path: addressed by dedicated role workers and Safe Output comments.

## Conclusion

Design cannot PASS until DR-MAJOR-1 is repaired and independently re-reviewed. No implementation should begin from Design v1.
