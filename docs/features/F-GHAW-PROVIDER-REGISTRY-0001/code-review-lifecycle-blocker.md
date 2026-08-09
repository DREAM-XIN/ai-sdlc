# Code Review Lifecycle Blocker — F-GHAW-PROVIDER-REGISTRY-0001

Role: independent Code Reviewer

## Finding

During legal persistence of the re-review PASS, the control plane exposed a lifecycle validator defect unrelated to Provider Registry business logic.

A completed remediation task retains `source_stage: code-review` as durable history. `validate_feature_manifest.py` currently rejects any remediation whose `source_stage` is DONE/SKIPPED, even when the remediation task itself is already DONE. Therefore the legitimate sequence:

`review REWORK -> remediation task -> remediation DONE -> independent re-review PASS -> review DONE`

cannot produce a valid Manifest.

This contradicts the repository's own remediation model: completed remediation remains durable task history while the independent review resumes and may eventually complete.

## Required bounded fix

- Keep the existing prohibition while a remediation task is unfinished.
- Allow a remediation task with `status: DONE` to reference a source review stage that later becomes DONE/SKIPPED.
- Add a deterministic regression proving an unfinished remediation still blocks source review completion and a completed remediation allows independent review completion.
- Do not weaken Gate Evidence, revision, role, or stage-transition authority.

This is a control-plane correctness blocker to completing the current legal lifecycle, not a reason to bypass Persist or edit the Feature Manifest directly.
