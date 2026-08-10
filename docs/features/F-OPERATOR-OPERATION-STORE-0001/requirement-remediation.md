# Requirement Remediation — F-OPERATOR-OPERATION-STORE-0001

## Role

Product remediation for `F-OPERATOR-OPERATION-STORE-0001-REQ-REMEDIATION-1`.

## Source finding

Independent Requirement Review identified MAJOR-1: the frozen canonical `operator.inbox` response has all-or-nothing success semantics across `operations`, `decisions`, and `notifications`, so enabling only the Operations portion would either fake empty Decision/Notification state or silently redefine the approved canonical API.

## Remediation

A revised `requirement-v2` was created with the following bounded correction:

- retain a trusted internal unfinished-Operation query primitive for later inbox composition;
- allow durable backing only for `operation.start`, `operation.status`, and `operation.cancel` within this Feature;
- keep canonical `operator.inbox` unavailable until a later independently reviewed workstream can truthfully satisfy complete operations + decisions + notifications semantics, unless an independently approved canonical API revision introduces explicit partial availability;
- explicitly prohibit returning empty Decision/Notification arrays as a substitute for missing backing;
- keep `operation.resume`, Decision writes, Notification writes, and corresponding product behavior deferred.

All other durable Operation Store, CAS, semantic-effect reservation, launch/Persist linearization, receipt correlation, UNKNOWN inheritance, and authority requirements remain preserved.

## Result

MAJOR-1 is addressed at Requirement level. A fresh independent Requirement Re-review is required before `requirement-gate` may PASS.
