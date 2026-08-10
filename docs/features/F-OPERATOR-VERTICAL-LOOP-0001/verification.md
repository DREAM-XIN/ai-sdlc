# Independent Verification QA — F-OPERATOR-VERTICAL-LOOP-0001

## Role and exact candidate

Role: independent Verification QA.

QA re-read the authoritative Feature Manifest, approved Requirement, approved Design v2, Plan, implementation evidence, Code Review/Re-review evidence, PR #217 current candidate state, deterministic validators and exact-head CI.

QA candidate head at verification start:

`f1f2b86d51b0eefca83d63e9b5a9ac9f64c0fd54`

Manifest at verification start:

- revision: `20`
- current stage: `verification`
- implementation: DONE
- code-review: DONE
- code-gate: PASS
- verification: READY
- verification-gate: PENDING

## Verdict

**REWORK — 0 BLOCKER / 1 MAJOR / 0 MINOR**

The implementation contains the missing deterministic scenario code, but the authoritative repository validation entrypoint does not execute two validators required to substantiate Verification PASS.

## QA-MAJOR-1 — Required completion/recovery validators are not wired into authoritative validation

Approved Requirement §17 requires deterministic verification of the full happy/rework loop, callback/lost-ack behavior, launch reconciliation, cancellation ordering, CAS semantic re-plan, restart reconstruction, lost Persist acknowledgement reconciliation, profile honesty and regressions. Requirement §18 requires independent QA to deterministically demonstrate happy and rework vertical paths, restart recovery, exact-binding fences, role independence, duplicate/lost-ack safety and bounded translator authority.

Approved Plan WU6 requires the scenario matrix to be wired into `scripts/validate.py` (the concrete implementation split the matrix across several validator modules, which is acceptable only if the authoritative validation path actually executes them).

Two current branch validators contain material required coverage but are not imported or called by `scripts/validate.py`:

1. `scripts/validate_operator_vertical_completion.py`
   - executes Reviewer REWORK → remediation Developer → fresh Reviewer PASS → QA PASS end to end;
   - proves the fresh candidate head is used after remediation;
   - proves QA PASS leaves Feature Acceptance READY and `release-gate` PENDING.
2. `scripts/validate_operator_vertical_reconcile.py`
   - executes NOT_LAUNCHED / LAUNCHED / UNKNOWN launch acknowledgement recovery;
   - cancellation fencing around missing launch;
   - callback binding/replay recovery;
   - Persist linearization/lost-ack reconciliation;
   - cancellation before/after Persist linearization.

Current `scripts/validate.py` imports and runs `validate_operator_vertical`, `validate_operator_vertical_recovery`, `validate_operator_vertical_remediation`, and `validate_operator_vertical_gh_aw`, but it does not import/run `validate_operator_vertical_completion` or `validate_operator_vertical_reconcile`.

Therefore exact-head Protocol run `31368588158` successfully executed `python scripts/validate.py`, but that green run did not execute the two modules above. Public Runtime run `31368588177` and Required PR Gate run `31368588135` are also green, but they do not replace the missing deterministic execution evidence.

This is a Verification evidence/integration defect, not a request to expand Feature scope.

### Required remediation

1. Wire both `validate_operator_vertical_completion.main` and `validate_operator_vertical_reconcile.main` (or semantically identical coverage) into the authoritative validation path used by `python scripts/validate.py` / required CI.
2. Keep the existing focused remediation, recovery, gh-aw, Store and protocol regressions enabled.
3. Produce a new exact candidate head and exact-head CI where Protocol, Public Runtime and Required PR Gate all succeed with the two validators actually executed.
4. Do not absorb Issue #219 Effect Lineage / UNKNOWN Resolution, Issue #221 real-runtime fault injection, Decision/Notification, complete inbox, Product Acceptance, release-gate authority or overall v0.3 release readiness.

## Checks that did pass QA inspection

The following are not findings and should be preserved:

- the full remediation→fresh re-review→QA path is implemented in the completion validator;
- QA PASS is structurally bounded to verification-gate PASS / verification DONE / acceptance READY and leaves release-gate PENDING;
- Worker authority-bearing fields are rejected by closed schemas;
- durable callback binding and collected-output digest validation are implemented;
- repeated-REWORK contributor/reviewer lineage remediation is present and focused regression exists;
- Store-level cancellation, UNKNOWN inheritance, Persist ordering and CAS semantic re-plan regressions exist;
- current exact-head Protocol, Public Runtime and Required PR Gate workflows are green for `f1f2b86d51b0eefca83d63e9b5a9ac9f64c0fd54`.

Those positives are insufficient for Verification PASS until the omitted required validators are part of the authoritative executed suite.

## Gate decision

`verification-gate`: **FAIL**.

Verification SHALL be BLOCKED with a bounded Developer remediation task. QA does not implement the fix, does not self-reverify, does not advance Acceptance, and does not touch `release-gate`.
