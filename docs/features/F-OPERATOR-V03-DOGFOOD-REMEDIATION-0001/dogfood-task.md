# v0.3 real release dogfood fixture — review_remediation

Feature: `F-OPERATOR-V03-DOGFOOD-REMEDIATION-0001`  
Fixed ref: `dogfood/v0.3-review-remediation-0001`
Scenario task artifact: `dogfood-scenario-task`

This scenario intentionally requires a real remediation round trip. On the initial Developer pass, create a minimal documentation-only candidate containing exactly `dogfood_review_state: initial-needs-remediation` and do not claim it is final. The independent Reviewer must treat that marker as a MAJOR REWORK because the accepted final state is `dogfood_review_state: remediated`. On the remediation Developer pass, replace the initial marker with the final marker and make no unrelated changes; independent re-review and QA may then PASS.

This release-only slot is independent from all Issue #221 fault-injection fixtures. It must not
be reset, force-pushed, recycled, or merged as a product change. Worker/model output is evidence
only; lifecycle authority remains the protected Operator Store plus canonical Feature Persist.
Product Acceptance is not performed by this fixture; the Feature may become `acceptance: READY`
while the dogfood Operation itself reaches its reviewed terminal status.
