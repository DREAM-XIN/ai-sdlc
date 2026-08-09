# Lifecycle Remediation — F-GHAW-PROVIDER-REGISTRY-0001

Role: Implementation Developer

Remediation task: `F-GHAW-PROVIDER-REGISTRY-0001-LIFECYCLE-REMEDIATION-1`

## Root cause

The Manifest semantic validator rejected a remediation task whenever its `source_stage` was DONE/SKIPPED, regardless of whether the remediation task itself was already DONE. That made the intended lifecycle impossible to close after a legitimate review REWORK and completed remediation.

## Fix

`scripts/validate_feature_manifest.py` now preserves the blocking invariant only for unfinished remediation tasks:

- unfinished remediation + source review already DONE/SKIPPED -> invalid;
- completed remediation history + source review later DONE/SKIPPED -> valid.

No stage, Gate, revision, Evidence, role, or merge/release authority was relaxed.

Added `scripts/validate_remediation_review_completion.py`, executed from the standard `scripts/validate.py` suite. The regression proves both sides:

1. an unfinished remediation prevents the independent source review from completing;
2. once the remediation is DONE, independent review can PASS its Gate, become DONE, and retain the completed remediation record as durable history.

## Validation

Validated source head: `43fe164e480d3557d6d77f11edf1f70e19db9921`

- `Validate AI-SDLC protocol` run `31307889986` — SUCCESS; its `validate.py` step executed the new closed-loop remediation regression successfully.
- `Required PR Gate` run `31307889987` — SUCCESS.
- `Validate Public Runtime Distribution` run `31307889991` — SUCCESS.
- `Validate AI-SDLC gh-aw Worker Compile` run `31307890027` — SUCCESS.

## Authority boundary

This remediation only repairs the trusted lifecycle validator so the existing remediation model can complete. It does not itself approve Code Review or PASS `code-gate`.
