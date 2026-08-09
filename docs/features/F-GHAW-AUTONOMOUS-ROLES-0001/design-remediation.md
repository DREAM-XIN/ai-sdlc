# Design Remediation Evidence — F-GHAW-AUTONOMOUS-ROLES-0001

Task: `F-GHAW-AUTONOMOUS-ROLES-0001-DESIGN-REMEDIATION-1`

Status: **PASS / completed by Architect**

## Finding addressed

DR-MAJOR-1 identified that autonomous Developer completion did not guarantee an approvable implementation artifact, while Reviewer PASS required implementation artifact approval.

## Remediation

Design v2 now requires trusted Developer result persistence to create a deterministic draft implementation candidate artifact bound to the Safe Output PR and collector-resolved immutable head SHA.

The Reviewer PASS translator resolves that exact candidate artifact from trusted Manifest/candidate context and approves the resolved id. It never hard-codes `implementation-v1`.

Manual implementation artifacts remain compatible. New/remediated autonomous candidates receive new deterministic identities; prior drafts are superseded without erasing history. QA is bound to the approved reviewed candidate/head tuple and fails closed on head movement.

## Verification expectations added

- candidate artifact creation and PR/head binding;
- manual/autonomous coexistence;
- zero/multiple/ambiguous candidate rejection;
- resolved-id Reviewer approval;
- REWORK no-approval behavior;
- supersession/history preservation;
- QA same-candidate enforcement.

## Scope

No Provider, routing, autonomous-role scope, Gate authority or merge/release boundary was expanded by this remediation.
