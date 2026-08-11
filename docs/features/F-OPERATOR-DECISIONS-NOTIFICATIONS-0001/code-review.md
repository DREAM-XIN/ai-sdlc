# Code Review — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## Role and reviewed candidate

Role: independent Code Reviewer.

Feature: `F-OPERATOR-DECISIONS-NOTIFICATIONS-0001` / Issue #229 / PR #230.

Reviewed exact PR head: `60d07aa453432735d233f0794921606833f6a398`.

Functional implementation candidate: `e2d1d9ea60e34f64df6fdec50f60384f1845bc37`.

The delta from functional candidate to reviewed head contains only refreshed Developer evidence plus Code Review START/lifecycle materialization; no runtime/schema/test implementation code changed after `e2d1d9ea60e34f64df6fdec50f60384f1845bc37`.

Exact functional candidate CI:

- Validate AI-SDLC protocol `31451664032` — SUCCESS;
- Validate Public Runtime Distribution `31451664049` — SUCCESS;
- Required PR Gate `31451664028` — SUCCESS.

Authoritative lifecycle at review: revision `13`, `implementation: DONE`, `code-review: WORKING`, `code-gate: PENDING`.

## Verdict

**REWORK — 0 BLOCKER / 1 MAJOR / 1 MINOR**

The implementation is broadly aligned with the approved Requirement/Design and its deterministic validation is authoritative, but one production cross-repository policy-binding defect prevents the Feature from satisfying the accepted control-repository/target-repository architecture. One additional generation-fence precision issue should be fixed in the same remediation.

## What is correct

The reviewed implementation correctly establishes:

- immutable Decision/Notification records and append-only journal facts on the accepted protected Operator Store;
- exact bounded choice/action semantics rather than fuzzy natural-language authorization;
- trusted principal/client/scope context rather than caller payload authority;
- fresh Feature/revision/ref/candidate and protected policy validation on response;
- immediate trusted-clock expiry rejection plus deterministic durable expiry reconcile;
- four required durable Notification types, semantic deduplication and exact idempotent acknowledgement;
- new-session inbox reconstruction from durable state;
- no direct Gate, Feature Manifest, launch or Persist authority from a Decision;
- MCP write surface remains unchanged/read-only;
- authoritative validators wired into `scripts/validate.py`, including takeover/cancel stale-Decision coverage;
- final functional candidate is regression-green on Protocol, Public Runtime and Required PR Gate.

## MAJOR-1 — Decision policy verifier conflates control Store repository with target Feature repository

The frozen v0.3 architecture explicitly places the repository-backed Operator Store in the **trusted control repository**, while each Operation/Decision separately binds a `target_repository` for the Feature being operated.

`TrustedOperatorStoreConfig.repository` is the trusted Operator Store repository identity. `build_trusted_vertical_runtime(...)` requires `decision_policy_verifier.repository == config.store.repository`, so the verifier is correctly composed against the protected control Store repository.

However `ProtectedDecisionPolicyVerifier.verify_current(...)` then rejects unless its `target_repository` argument equals that same `self.repository`. Production callers pass `feature.repository` as `target_repository`.

Therefore a legitimate cross-repository Operation whose Store lives in control repository A and whose Feature lives in target repository B fails `POLICY_DENIED` before a Decision can be created/responded. Same-repository dogfood and current tests hide this because A == B.

This violates the approved Requirement's target-repository binding, the Release Spec's control-repository Store architecture, and the explicit requirement that existing cross-repository behavior remain regression-green.

Required remediation:

- separate **control/store repository binding** from **target Feature repository policy scope**;
- keep verifier/source/state-ref authority bound to trusted control/installation configuration;
- allow policy to resolve bounded rules for an explicitly trusted target repository without accepting that target repository from untrusted client/Worker payload;
- bind the effective Decision policy digest to the exact target repository + Feature/ref + restriction overlay;
- add deterministic tests where control repository and target repository differ, proving valid cross-repo Decision creation/response/inbox succeeds while untrusted/unauthorized target selection fails closed.

Do not solve this by removing repository binding entirely or by allowing the Feature branch/client to choose a broader policy source.

## MINOR-1 — authorization consumption lacks an explicit current-generation fence

`plan_consume_decision_authorization(...)` revalidates Feature/policy/expiry and cancellation, but it does not explicitly compare the Decision's bound `operation_generation` with the current Operation projection generation before appending `decision.authorization-consumed`.

After a generation takeover, the current reducer ultimately rejects an old-generation appended fact during projection rebuild, so this path is fail-closed today. But the failure is accidental/invariant-driven and surfaces as an internal Store error instead of the intentional `SUPERSEDED_GENERATION` contract.

Required remediation:

- explicitly re-read current Operation projection before authorization consumption;
- reject generation mismatch with `SUPERSEDED_GENERATION` before constructing any mutation;
- retain cancellation precedence and existing exact Feature/policy/expiry/action checks;
- add a deterministic resolved-Decision → takeover → authorization-consume adversarial test proving no mutation and the bounded generation error.

## Release boundary

This review does not PASS Code Gate. It does not claim QA, Product Acceptance, #221 real-runtime fault injection, second-adapter completion, #218 release evidence synchronization, or v0.3 release readiness.

Next legal role: Developer remediation addressing exactly MAJOR-1 and MINOR-1, followed by a fresh independent Code Re-review bound to the resulting exact candidate.