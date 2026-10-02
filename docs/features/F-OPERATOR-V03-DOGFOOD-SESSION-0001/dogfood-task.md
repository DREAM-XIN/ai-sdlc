# v0.3 real release dogfood fixture — session_recovery

Feature: `F-OPERATOR-V03-DOGFOOD-SESSION-0001`  
Fixed ref: `dogfood/v0.3-session-recovery-0001`
Scenario task artifact: `dogfood-scenario-task`

Create one minimal documentation-only candidate containing `dogfood_session_choice: PENDING_USER`. The release controller intentionally ends its original client session after the first durable external stop. A fresh session then requests the protected `NEEDS_AUTHORIZATION` Decision for this explicit choice and must rediscover the same Operation plus its pending Decision/Notification through the production Responses read surface, without replaying operation.start; the frozen scenario must finish with the same durable Operation at `NEEDS_USER`.

This release-only slot is independent from all Issue #221 fault-injection fixtures. It must not
be reset, force-pushed, recycled, or merged as a product change. Worker/model output is evidence
only; lifecycle authority remains the protected Operator Store plus canonical Feature Persist.
Product Acceptance is not performed by this fixture; the Feature may become `acceptance: READY`
while the dogfood Operation itself reaches its reviewed terminal status.
