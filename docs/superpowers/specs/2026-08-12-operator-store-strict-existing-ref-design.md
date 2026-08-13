# PR #242 Strict Existing-Ref Adoption and Ruleset Verification Design

**Status:** Approved design, pending implementation-plan approval  
**Date:** 2026-08-12  
**Scope:** Issue #241 / PR #242 only  
**Approved approach:** Strict fail-closed bootstrap adoption

## Context

PR #242 provisions the protected Operator Store state ref for personal repositories. Exact-head review of `c34821ff464230fab7744166484dc4d7c04b1eb9` found two gaps:

1. an already-existing state ref is returned unchanged after current ruleset protection is verified, without proving that its current tree is an empty initialization state;
2. the protection verifier checks the presence of an `update` rule and the exclusive writer bypass, but does not verify that `update_allows_fetch_and_merge` is exactly `false`.

The first gap can wrap protection around pre-existing semantic Store history and then report successful provisioning. The second can classify a broader-than-approved update rule as `PROTECTED`.

## Goals

- Never enable or report successful provisioning for an unknown or semantically populated pre-existing state ref.
- Preserve safe, idempotent re-execution for the exact initialization-only bootstrap state.
- Positively prove the bounded update-rule parameter used by the trusted writer policy.
- Fail closed on missing, malformed, ambiguous, mismatched, or permissive evidence.
- Preserve the existing authority boundary: only trusted install/control code provisions rulesets and the state ref; clients, Features and Workers gain no new authority.

## Non-goals

- Migrating an existing semantic Operator Store.
- Attesting that arbitrary historical Store writes occurred under protection.
- Repairing, rewriting, deleting, force-updating, or quarantining an existing ref.
- Changing Operation Store semantic formats, runtime CAS behavior, Feature lifecycle authority, or release readiness.
- Provisioning the live repository state ref as part of this change.

## Considered approaches

### A. Strict initialization-only adoption — selected

An existing state ref is accepted only when its exact current commit and tree represent the empty bootstrap state defined by this repository. Any semantic history or ambiguous shape is rejected.

This is the smallest safe change for Issue #241. It makes no unverifiable historical-authority claim and preserves idempotency before the Store begins normal operation.

### B. Scan and accept an existing semantic Store — rejected

Schema validation can show that stored JSON is well formed, but cannot prove that historical writes were authorized or protected when created. Accepting it would retain the review blocker.

### C. Signed installation/migration attestation — deferred

A separately signed installation ledger could authorize an existing Store, but it adds new credential, rotation, recovery and migration protocols outside Issue #241. If needed, it must be a separate reviewed workstream.

## Design

### 1. Existing-ref classification

Add one internal validation boundary in `scripts/operator_store_github_ruleset_provision.py` for an existing remote state ref.

After the exact ref is resolved, the validator must inspect that exact SHA without checking out or rewriting it. It accepts the ref only when all of these statements are true:

- the commit has no parents;
- the complete recursive tree contains exactly one regular blob;
- that blob path is exactly `state/operator/v1/.bootstrap`;
- the blob content is exactly `ai-sdlc-operator-store-bootstrap-v1\n`;
- there are no other paths, including semantic Store JSON.

Commit author, committer and message may be checked for diagnostics but are not security evidence because Git identities are forgeable. The accepted security property is the exact empty tree/root shape, not claimed authorship.

If any Git inspection fails, any output is ambiguous, the commit has parents, or the tree/content differs, raise `RulesetProvisioningError`. Do not return an adopted SHA and do not report successful provisioning.

### 2. Provisioning flow and partial state

The trusted flow remains:

1. create or reconcile the two bounded rulesets;
2. positively verify pre-targeted protection;
3. resolve the exact state ref;
4. if absent, create the current initialization-only bootstrap;
5. if present, run strict initialization-only validation;
6. re-verify protection and return success.

A rejected existing ref may now have newly applied protection around it. This is safe partial progress: protection is fail-safe and future runs remain idempotent, but the command fails and the Store stays unavailable. The code must not delete, reset, force-push, migrate, or write semantic state while recovering from this condition.

The command is therefore a first-install/bootstrap reconciler, not a general existing-Store migration tool. Documentation must state that a Store which has progressed beyond the exact bootstrap root requires a separate reviewed migration/attestation path and is intentionally rejected here.

### 3. Update-rule parameter proof

Extend `GitHubRulesetProtectionVerifier` to inspect detailed ruleset `rules`, rather than relying only on the applied-rule type rows.

For every ruleset reported as contributing an active `update` rule:

- the detailed rules payload must be a list of rule objects;
- at least one corresponding `update` rule must be present;
- every corresponding `update` rule must contain a parameters object;
- `parameters.update_allows_fetch_and_merge` must be the boolean `false`.

Classification is fail-closed:

- missing/malformed/mismatched details produce `UNKNOWN`;
- an explicit permissive value (`true`) produces `UNPROTECTED`;
- only exact `false`, together with all existing provenance and bypass checks, may contribute to `PROTECTED`.

The existing positive requirements remain unchanged:

- exact repository provenance;
- active branch-targeted rulesets;
- the unique trusted Integration as the only auditable writer bypass;
- zero-bypass deletion and non-fast-forward protection.

### 4. Error and authority boundaries

- All new failures occur before a successful provisioning result is returned.
- No new token, App id, repository, ref, expected SHA, or migration authority becomes client-selectable.
- Protection receipts remain evidence of current policy only; they do not attest historical writes.
- The bootstrap marker remains excluded from semantic Store snapshots.
- No workflow may interpret a rejected existing ref as `PROTECTED` installation success.

## Test strategy

Use test-first red/green cycles.

### Existing-ref adversarial tests

Add production-like bare-remote tests that create the remote ref before provisioning:

- root commit containing semantic Store JSON is rejected;
- bootstrap marker plus any extra path is rejected;
- incorrect marker content is rejected;
- a commit with a parent is rejected, even when its final tree contains only the marker;
- malformed/unresolvable Git inspection is rejected;
- the exact parentless marker-only root is accepted idempotently;
- rejection performs no force update, delete, semantic write, or ref rewrite.

The tests must assert that the remote SHA is unchanged after rejection.

### Ruleset parameter tests

Mutate detailed ruleset responses independently of the provisioner request:

- `update_allows_fetch_and_merge: false` remains `PROTECTED`;
- explicit `true` is `UNPROTECTED`;
- missing parameters, missing key, wrong type, malformed rules list, or applied/detail mismatch is `UNKNOWN`;
- all pre-existing provenance, bypass and integrity negative cases continue to pass.

### Verification

At minimum, the new exact head must run:

- ruleset protection validation;
- ruleset remediation validation;
- Operator Store runtime validation;
- authoritative repository validation;
- Public Runtime Distribution validation;
- Required PR Gate.

All GitHub checks and review evidence must bind the resulting exact PR head or its documented pull-request merge candidate. Historical green runs and reviews do not authorize the new head.

## Acceptance criteria

Implementation is acceptable only when:

- every adversarial existing-ref case fails before successful provisioning and leaves the exact ref unchanged;
- only the exact initialization-only root can be reused;
- permissive or ambiguous update-rule parameters cannot yield `PROTECTED`;
- existing positive protection, writer bypass, integrity and clean bootstrap tests remain green;
- documentation clearly excludes semantic existing-Store migration;
- fresh CI is green on the new candidate;
- a fresh independent exact-head review reports no BLOCKER or MAJOR findings.

## Release boundary

Passing this work makes PR #242 eligible for human merge consideration only. It does not provision the live state ref, complete Issue #221, count as dogfood, approve PR #233, change `VERSION`, create `release/v0.3.0.yaml`, or claim v0.3 release readiness.
