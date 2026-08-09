# Code Review — F-GHAW-AUTONOMOUS-ROLES-0001

Role: independent Code Reviewer

Candidate: PR #203

Reviewed lifecycle state: revision 16, `code-review: WORKING`, `code-gate: PENDING`.

## Verdict

**REWORK**

- BLOCKER: 0
- MAJOR: 1
- MINOR: 0

## Findings

### CR-MAJOR-1 — Gate-result provenance is not bound to the exact trusted role-worker run/task

The new Gate-result collector correctly re-fetches the target PR, checks the immutable candidate head, checks Feature/revision/role/stage identity, fetches the Safe Output comment from the exact PR, requires a Bot author type, validates a closed machine envelope, and translates the result through trusted lifecycle code.

However, the collector currently accepts provenance based only on the target comment plus `user.type == Bot`. It does **not** cryptographically/operationally bind that comment to the exact trusted Reviewer/QA role-worker workflow run that was dispatched for the lifecycle task, and the worker result `task_id` is not independently checked against a trusted dispatch task identity.

That leaves a material provenance gap: another Bot-authored comment on the same PR could satisfy the machine envelope and identity fields without proving that it came from the authorized role-worker execution for this task. This conflicts with the approved Requirement's trusted task-identity validation and Plan WU-7's requirement to verify role-worker run/workflow provenance before lifecycle translation.

### Required remediation

Keep the existing candidate/head/schema/least-privilege design, but bind Gate result ingestion to trusted execution provenance:

1. carry a trusted Gate task identity into the role-worker workflow as an explicit input;
2. have the role-worker conclusion pass its exact control-repository Actions run identity and workflow identity/path to the collector;
3. have the collector query the control repository through trusted GitHub credentials and verify the run exists, is the expected role-worker workflow for `(role, stage, profile)`, belongs to the trusted control repository, and corresponds to the supplied run/workflow identity;
4. validate the result payload `task_id` against the trusted task identity rather than accepting a model-selected value;
5. add deterministic negative tests for wrong task id, wrong run id, wrong workflow identity, non-role-worker workflow, and Bot-comment-only provenance;
6. preserve Reviewer/QA read-only source boundaries, candidate SHA checks, profile routing, and all existing Developer compatibility.

## Positive observations

The implementation otherwise demonstrates the approved architecture well:

- autonomous routing is restricted to `developer+implementation`, `reviewer+code-review`, and `qa+verification`;
- Reviewer/QA workers are separate generated/strict-compiled read-only workers and expose only bounded `add-comment` Safe Output;
- candidate PR/head identity is trusted-control derived and checked again before dispatch and before persistence;
- Reviewer/QA result schemas are closed and generic Developer `COMPLETED => stage DONE` cannot complete Gate roles;
- Reviewer REWORK/QA verdict translation remains in trusted code and workers have no direct Gate authority;
- the latest reviewed head has green Protocol, Public Runtime, Required Gate, and 12-target strict worker compile CI.

## Gate decision

`code-gate` must remain **PENDING** until CR-MAJOR-1 is remediated and independently re-reviewed.
