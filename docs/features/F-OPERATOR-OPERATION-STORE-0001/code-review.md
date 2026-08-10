# Independent Code Review — F-OPERATOR-OPERATION-STORE-0001

## Verdict

**REWORK — 0 BLOCKER / 2 MAJOR / 0 MINOR**

Review role: independent Code Reviewer.

Reviewed approved Requirement v2, Design v2, Plan, implementation evidence, PR #215 implementation diff, and the exact runtime candidate `9418094c485f89c663de4bc4c7621d943a96c237`. The current lifecycle head after Implementation completion is runtime-equivalent to that candidate: subsequent changes are implementation documentation/evidence and Feature lifecycle state only.

The deterministic suites and required CI are green, but they currently validate a local-Git simulation rather than the required durable remote control-repository semantics. Therefore the green CI is necessary evidence but not sufficient to PASS the code gate.

## MAJOR-1 — Production Git adapter does not persist the Operator Store to the remote control repository

`GitStateRefBackend.commit()` creates blobs/tree/commit and then executes local:

```text
git update-ref <state-ref> <new-commit> <expected-old-sha>
```

against the trusted checkout. It never updates/pushes the remote control-repository state ref.

Consequences:

- in an ephemeral GitHub Actions checkout, the Operation Store disappears when the runner exits;
- two runners do not contend on one shared remote ref and therefore do not obtain the required repository-level compare-and-set semantics;
- the current local temp-repository test proves only local ref atomicity, not durable cross-runner state;
- `operation.start`, launch/Persist reservations and UNKNOWN state can be reported as durably committed even though only a process-local clone changed.

This violates the approved Requirement/Design and the frozen Release Spec requirement that v0.3 uses a repository-backed trusted Operation Store on a dedicated protected control-plane state ref with exact ref CAS.

### Required remediation

Implement a trusted **remote** state-ref persistence path. It must:

1. read/fetch the current remote state-ref identity;
2. build the bounded commit as a descendant of that exact state commit;
3. atomically update the remote ref only if the expected remote SHA remains current (for example exact `--force-with-lease=<ref>:<expected-sha>` semantics or an equivalent trusted API CAS);
4. on remote conflict, fetch/re-read remote Store state and re-run the semantic planner;
5. prove state survives a fresh checkout/process and concurrent writers converge/fence correctly;
6. never use blind force or silently rewrite unrelated state history.

The local backend may remain as a deterministic fixture/helper, but it cannot be the production durability implementation.

## MAJOR-2 — No concrete production verifier proves the remote state ref is actually protected

`scripts/operator_store_protection.py` defines the verifier protocol and a `StaticProtectionVerifier`; `scripts/operator_store_runtime.py` accepts an injected verifier. There is no concrete production verifier in this Feature that inspects trusted repository rules/branch protection/ruleset state and produces `PROTECTED` only from positive remote evidence.

As implemented, a caller with access to trusted runtime composition can instantiate `StaticProtectionVerifier(status=PROTECTED)` and obtain a write-enabling receipt without proving the remote ref is protected. This means the Design Review remediation requirement—actual, fail-closed protection verification before semantic writes—is not yet enforced by a production path.

### Required remediation

Add a production protection verifier/control adapter that:

1. obtains protection/ruleset state from trusted control-plane/repository authority, not Feature/client/Worker input;
2. binds the result to exact repository + state ref (+ policy/ruleset identity/digest where available);
3. returns `PROTECTED` only on positive proof; inspection failure/ambiguity is `UNKNOWN`, absence is `UNPROTECTED`;
4. keeps `StaticProtectionVerifier` test-only or otherwise prevents it from serving as the normal production write-enabling authority;
5. includes deterministic fixtures for PROTECTED / UNPROTECTED / UNKNOWN and binding mismatch plus a production-boundary test showing semantic writes remain unavailable without positive remote proof.

Protection provisioning may remain installation/control authority as approved by Design v2; this Feature still needs the concrete verification path consumed by the production Store runtime.

## Reviewed positives

The following implementation aspects are consistent with the approved design and should be preserved during remediation:

- semantic-effect identity excludes Operation generation;
- stable external dispatch key is reservation-bound;
- reservations and claims are create-once immutable artifacts;
- only projection cache is replaceable;
- UNKNOWN stays BLOCKED across generation takeover;
- launch and Persist linearization are distinct;
- exact pre-linearized post-cancel correlation is permitted while new work is fenced;
- canonical backing is limited to `operation.start/status/cancel`;
- `operator.inbox`, `operation.resume`, Decision and Notification write semantics remain honestly unavailable;
- structured Store errors are preserved across the canonical boundary;
- existing protocol/public-runtime/cross-repository regressions are green on the reviewed runtime candidate.

## Gate decision

Code gate remains **PENDING**. No PASS or waiver is authorized by this review.

A Developer remediation must produce a new runtime candidate and new exact-head evidence. A fresh independent re-review must verify the remote durability and protection findings before code-gate PASS is possible.
