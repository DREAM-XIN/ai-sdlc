# Independent Code Review — F-OPERATOR-VERTICAL-LOOP-0001

## Verdict

**REWORK — 0 BLOCKER / 2 MAJOR / 0 MINOR**

Review role: independent Code Reviewer.

Reviewed the authoritative Feature state, Issue #216, approved Requirement, approved Design v2 / Design Re-review, Plan, implementation artifacts/evidence, PR #217 implementation diff, and the exact reviewed PR head `598f3318f2566f4da07bdad241045fca95736cee`.

The Developer's functional runtime candidate is `dc88354429e1a81468ca78971cc3c51f30c2af62`. Comparing that candidate to the reviewed head shows only implementation documentation/evidence plus lifecycle Event/Manifest changes; no runtime source/test/schema files changed after the validated candidate. The candidate's Protocol, Public Runtime and Required PR Gate runs are green as recorded in implementation evidence.

Those positives are necessary, but the implementation still contains two authority/recovery defects that prevent Code Gate PASS.

## MAJOR-1 — Parallel callback ingress can bypass the trusted callback coordinator

The approved Design requires callback handling to flow through one trusted path:

`durable launch binding → trusted callback normalization → durable callback record → durable role-independence reconstruction → collected-output validation → translator → trusted Persist`.

The production composition now correctly creates `TrustedVerticalCallbackCoordinator`, but `TrustedVerticalExecutor` still exposes a separate public `handle_worker_callback(...)` method. That method remains reachable through `TrustedRecoveringVerticalExecutor.__getattr__`, and the runtime bundle exposes the executor object.

This alternate path is materially weaker than the coordinator path:

- it accepts a caller-supplied `RoleIndependencePolicy` instead of deriving policy from accepted durable callback history;
- `content_loader` defaults to `None`, so collected-output bytes do not have to be reloaded and re-hashed before translation;
- it records callback state through the generic `plan_callback(...)` path and does not run `_validate_durable_dispatch_binding(...)` from `plan_vertical_callback_record(...)`;
- it validates generation, then trusts the supplied `TrustedDispatchContext`/receipt bindings rather than requiring the exact durable semantic reservation + launch authorization binding enforced by the coordinator path.

A caller that reaches this method can therefore bypass structural guarantees that the Design claims are mandatory for Reviewer/QA independence, exact dispatch correlation and collector provenance. The fact that the intended production adapter uses `TrustedVerticalCallbackCoordinator` is not sufficient while a second callable lifecycle-driving ingress remains exposed by the same production executor bundle.

### Required remediation

1. Remove the parallel `TrustedVerticalExecutor.handle_worker_callback(...)` lifecycle-driving ingress, or make it a non-authoritative private helper that cannot be invoked without the coordinator's durable binding/policy/content-validation path.
2. Ensure the production runtime exposes exactly one callback-to-lifecycle translation boundary.
3. Role-independence policy must always be reconstructed from trusted durable facts; no caller-supplied policy object may authorize Reviewer/QA PASS.
4. Collected repository outputs used as Feature artifact/evidence must always be reloaded/rehashed through the trusted collector loader before translation; `None` must not silently skip this check in a production-capable path.
5. Add a deterministic adversarial test proving an alternate/direct callback invocation cannot bypass durable dispatch binding, role independence or content digest validation.

## MAJOR-2 — Repeated REWORK loses candidate-author identity history and does not identify the latest remediation deterministically

The approved Requirement explicitly allows repeated Reviewer REWORK cycles while policy permits them, and requires a Reviewer that conflicts with candidate author/remediation identity to fail independence.

The current durable independence reconstruction collapses history into only three scalar fields:

- `developer_identity`;
- `reviewer_identity`;
- `remediation_developer_identity`.

`derive_role_independence_policy(...)` overwrites `remediation_developer_identity` for each later accepted remediation callback and overwrites `reviewer_identity` for each later accepted review callback. After two or more REWORK/remediation cycles, earlier remediation Developers disappear from the forbidden identity set even though their changes remain part of the current candidate. A later Reviewer or QA can therefore reuse an earlier remediation Developer identity and still pass the reconstructed policy.

The controller also determines the "latest" completed remediation with:

`sorted(completed, key=lambda row: str(row["id"]))[-1]`

but remediation ids end in a content hash. Lexicographic id order is not chronological lifecycle order, so after multiple remediation cycles the selected `CODE_REREVIEW` task identity can point at an older completed remediation rather than the one that produced the current candidate.

Together these defects weaken the exact candidate/role provenance model specifically in the repeated-REWORK path the Requirement says may continue.

### Required remediation

1. Reconstruct durable candidate-author/remediation identity history as an ordered lineage or forbidden identity set, not one overwriteable remediation identity scalar.
2. A fresh Reviewer must be checked against all Developer/remediation identities that contributed to the current candidate lineage as required by policy.
3. QA separation must use the corresponding durable candidate/review lineage rather than only the last scalar identities.
4. Determine the remediation predecessor for fresh re-review from authoritative lifecycle/journal order or an explicit durable predecessor relation; do not infer chronology from hashed task ids.
5. Add deterministic coverage with at least two REWORK/remediation cycles proving:
   - an earlier remediation Developer cannot later satisfy fresh Review or QA for a candidate containing that work;
   - the re-review task binds the actual latest remediation/candidate lineage;
   - restart reconstruction yields the same forbidden identity lineage and task predecessor.

## Reviewed positives

The following implementation aspects are consistent with the approved design and should be preserved during remediation:

- Worker role schemas are strict and reject arbitrary Event/Manifest/Gate/URI/path authority fields;
- trusted translators have bounded role-specific lifecycle changes;
- QA PASS stops at Acceptance READY / release-gate PENDING;
- collector receipts bind Operation/generation/profile/dispatch/repository/Feature/revision/candidate and support byte digest checks on the coordinator path;
- Reviewer/QA candidate head is revalidated before translation/Persist;
- the vertical loop reuses the existing semantic reservation, stable external dispatch key, launch authorization, cancellation, UNKNOWN and Persist linearization semantics;
- restart reconciliation records exact translated Feature Events for Persist acknowledgement recovery;
- Issue #219 effect-lineage semantics and Issue #221 real-runtime release proof remain correctly outside this Feature.

## Gate decision

`code-gate` remains **PENDING**. No PASS or waiver is authorized by this review.

A separate Developer remediation must address both MAJOR findings and produce a new runtime candidate plus deterministic evidence. A fresh independent Code Re-review must re-read the resulting exact head and verify closure before Code Gate PASS is possible.
