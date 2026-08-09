# Design v2 — Autonomous Reviewer/QA candidate artifact remediation

Feature: `F-GHAW-AUTONOMOUS-ROLES-0001`

Status: candidate Design v2

This document incorporates `design.md` in full except where explicitly superseded below. All unchanged sections of Design v1 remain normative. The following sections replace/extend Design v1 to close DR-MAJOR-1.

## A. Canonical implementation candidate artifact

Every autonomous Developer `COMPLETED` result must produce, through the trusted conclusion/collector and normal Feature Event path, a deterministic **draft implementation candidate artifact** in addition to the PR/head identity artifacts.

For an implementation result whose trusted expected result revision is `R`, the collector derives deterministic identities such as:

- implementation candidate artifact id: `implementation-candidate-r<R>`;
- type: `implementation`;
- URI: canonical target PR URL;
- status: `draft`;
- candidate head evidence/artifact: immutable GitHub commit URL for the PR head SHA resolved by the trusted collector.

The exact id format is implementation-defined but must be deterministic from trusted Feature/revision/candidate context, unique in the Manifest, and covered by tests. The agent/model does not choose the artifact id, PR number, URL or SHA.

The Developer worker result transport may report the Safe Output PR URL, but the control collector resolves the PR through GitHub and verifies target repository/base branch/head before constructing artifact-record changes.

## B. Manual and autonomous compatibility

The candidate resolver does not hard-code `implementation-v1`.

For manual execution, an existing draft implementation artifact such as `implementation-v1` remains valid when the current Feature lifecycle/candidate context identifies it as the implementation under review.

For autonomous execution, the trusted collector records `implementation-candidate-r<R>` (or equivalent deterministic id).

A shared candidate resolver receives the current Manifest plus trusted candidate PR/head identity and must return **exactly one** draft implementation artifact bound to that candidate. Zero or multiple matches fail closed.

No branch may infer the artifact from a provider/profile name.

## C. Candidate binding representation

The existing Manifest artifact schema remains unchanged. Binding is represented using multiple durable records created in the same trusted result Event:

1. draft implementation artifact — type `implementation`, URI = canonical PR URL;
2. implementation PR artifact — type `pull-request`, URI = same canonical PR URL when a distinct operational artifact is useful;
3. immutable candidate-head Evidence/artifact — URI = canonical commit URL containing the exact head SHA.

The trusted collector validates that these records refer to the same repository/PR/head before constructing the Event. Reviewer/QA dispatch resolves the pair atomically from current Manifest plus GitHub state.

If the implementation PR head changes, the previous candidate artifact remains historical and is not silently rebound to the new SHA.

## D. Supersession semantics

When a later autonomous implementation or remediation produces a new candidate:

- the prior implementation candidate is preserved in history;
- if still `draft`, the trusted candidate-update Event marks it `superseded` before/with recording the new draft candidate when transition/schema rules permit;
- an already approved candidate is never rewritten to point to a new PR/head;
- the new candidate receives a new deterministic artifact id tied to the new trusted revision;
- Code Review must run again against the new candidate.

Candidate selection always requires exactly one current non-superseded implementation artifact compatible with the trusted candidate identity.

## E. Reviewer PASS artifact approval

Design v1 section 10 PASS semantics are replaced with:

A valid Reviewer PASS collector first resolves the exact reviewed implementation candidate artifact from:

- current Manifest;
- trusted candidate PR URL/number;
- candidate head evidence;
- current Reviewer dispatch context.

It then constructs a normal Feature Event that:

- persists review Evidence as pass;
- records the reviewed candidate head Evidence/artifact;
- changes **that resolved candidate artifact id** from `draft` to `approved` with the review Evidence;
- passes `code-gate` with the same Evidence;
- marks `code-review` DONE;
- makes `verification` READY.

The translator must never assume an artifact id such as `implementation-v1` and must fail closed if candidate resolution is ambiguous or if the artifact is already superseded.

## F. Reviewer REWORK / remediation candidate behavior

Reviewer REWORK preserves the failed candidate artifact and failed review Evidence. It does not approve the candidate.

A subsequent Developer remediation result creates a new draft implementation candidate artifact bound to the remediation PR/head (or updated trusted PR/head according to the approved implementation mechanics), while the prior draft candidate becomes `superseded` when permitted. The fresh independent Reviewer dispatch must resolve and review only the new current candidate.

Historical failed candidate/review evidence remains durable.

## G. QA candidate behavior

QA can run only after Reviewer PASS has approved the exact implementation candidate artifact. The trusted QA gateway resolves:

- the approved implementation artifact;
- the reviewed-candidate head evidence;
- current PR head through GitHub.

All three must identify the same immutable candidate SHA. A changed PR head or a different implementation artifact fails closed and requires new Code Review before Verification.

## H. Deterministic tests added by v2

In addition to Design v1 tests:

1. autonomous Developer result produces exactly one deterministic draft implementation candidate artifact;
2. PR/head mismatch prevents candidate artifact creation;
3. manual `implementation-v1` and autonomous candidate artifacts can coexist without id collision;
4. candidate resolver finds exactly one artifact and rejects zero/multiple/ambiguous/superseded matches;
5. Reviewer PASS approves the resolved artifact id rather than a hard-coded id;
6. Reviewer REWORK leaves failed candidate unapproved;
7. remediation/new implementation supersedes prior draft candidate without mutating history;
8. QA accepts only the approved reviewed candidate/head tuple;
9. candidate head movement after Code Review fails closed.

## I. DR-MAJOR-1 closure

This v2 amendment guarantees that autonomous implementation has an approvable lifecycle artifact, preserves manual compatibility, and binds Code Review/Verification to immutable trusted candidate identity without changing the Feature Manifest schema or introducing provider-specific control branches.
