# Code Re-review — F-OPERATOR-OPERATION-STORE-0001

## Role

Fresh independent Code Reviewer after `F-OPERATOR-OPERATION-STORE-0001-CODE-REMEDIATION-1`.

## Context

- authoritative state at re-review start: revision `22`, `code-review: WORKING`, `code-gate: PENDING`;
- approved Requirement: `requirement-v2`;
- approved Design: `design-v2`;
- prior independent Code Review: `evidence-code-review-v1` (REWORK, 2 MAJOR);
- remediation evidence: `evidence-code-remediation-v1`;
- exact remediation runtime candidate: `0a6cd5d19f51aef1ded3c6610740e0fc57cc4ba1`;
- current lifecycle head at equivalence check: `a264f8f4af6981c578b3d21c7c546e36de395295`.

The compare from the green remediation runtime candidate to the lifecycle head contains only the remediation document, remediation-DONE Event, and Manifest state update; no runtime/test/dependency files changed.

## Verdict

**PASS**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 0

## Prior MAJOR-1 — remote durable CAS — CLOSED

The production Store no longer treats a runner-local branch ref as the durable authority.

`RemoteGitStateRefBackend`:

- reads the configured remote Operator state ref with `ls-remote`;
- fetches that exact SHA into a private tracking ref;
- reconstructs the Store snapshot from that exact durable remote commit;
- builds the candidate commit with the exact remote SHA as parent;
- re-reads the remote ref immediately before write;
- pushes without force to the exact state ref;
- treats remote ref movement/non-fast-forward rejection as `CasConflict`;
- re-fetches and semantically re-plans through `commit_replanned` instead of replaying stale bytes;
- confirms the remote durable SHA after write.

The deterministic runtime validator uses one bare remote plus independent writer checkouts and a third fresh clone. It proves state survives the first runner, is visible from another checkout, rejects a stale concurrent writer, successfully re-plans from the new remote head, and is reconstructable from a fresh clone.

This closes the durability/shared-authority defect from the first review.

## Prior MAJOR-2 — concrete protection verification — CLOSED

A concrete `GitHubBranchProtectionVerifier` now obtains trusted protection state from GitHub's branch-protection API for the exact configured repository/ref.

It fails closed:

- `404` → `UNPROTECTED`;
- inspection/API failure → `UNKNOWN`;
- missing push restrictions, force-push allowance, deletion allowance, or missing configured Operator App → `UNPROTECTED`;
- only positive policy evidence returns `PROTECTED`.

The receipt remains repository/ref-bound and is checked again by the Store writer before semantic commit.

`StaticProtectionVerifier` is explicitly test-only and the normal production composition rejects any verifier marked `test_only`. A concrete GitHub production builder wires `RemoteGitStateRefBackend` together with `GitHubBranchProtectionVerifier` from trusted control configuration.

This closes the prior self-attested/static-protection defect.

## Regression and boundary checks

### Immutable state — PASS

Operation events, semantic reservations, dispatch claims and Feature claims remain create-once immutable. Only projection cache paths are replaceable.

### Generation/effect identity — PASS

Semantic-effect identity remains generation-independent. Takeover does not manufacture a new external dispatch identity.

### UNKNOWN safety — PASS

Unresolved `UNKNOWN` remains BLOCKED across generation takeover and prevents speculative new launch/dispatch/Persist decisions.

### Launch/cancel ordering — PASS

`dispatch.launch.authorized` remains the launch linearization point. Cancellation cannot authorize a new launch; exact post-cancel receipt/callback correlation for a previously authorized dispatch remains allowed.

### Persist ordering — PASS

Persist requested/linearized/confirmed are distinct immutable facts. Confirmation requires prior linearization and exact Feature Event correlation; cancellation does not erase an already-linearized Persist.

### Canonical API scope — PASS

Only `operation.start`, `operation.status`, and `operation.cancel` gain Store backing. `operator.inbox`, `operation.resume`, Decision and Notification capabilities remain unavailable.

### MCP/authority boundary — PASS

No MCP write tools were added. Store code does not edit Feature Manifests or Gates and remains orchestration metadata rather than lifecycle authority.

## Exact-head validation evidence

On remediation runtime candidate `0a6cd5d19f51aef1ded3c6610740e0fc57cc4ba1`:

- Validate AI-SDLC protocol — SUCCESS (`31355460908`);
- Validate Public Runtime Distribution — SUCCESS (`31355460912`);
- Required PR Gate — SUCCESS (`31355460962`).

The later lifecycle-only commits do not alter the validated runtime tree.

## Gate recommendation

`code-gate`: **PASS**.

Approve `implementation-v1` with this re-review evidence and advance to independent Verification QA.
