# PR #242 Strict Existing-Ref Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task by task, with superpowers:test-driven-development for each behavior change.

**Goal:** Close PR #242's exact-head BLOCKER and MAJOR by refusing to adopt any existing Operator Store ref that is not the exact initialization-only root, and by positively verifying the update rule's bounded parameter.

**Architecture:** Keep protection proof and Git-history validation fail-closed and separate. The ruleset verifier derives the applying update-rule owners from the branch-rule endpoint, then validates the matching full rule objects in each repository-ruleset detail. The provisioner treats an existing state ref as reusable only after object-level Git inspection proves one parentless commit whose recursive tree is exactly the trusted bootstrap marker. Rejection never rewrites, deletes, force-pushes, or migrates the ref.

**Tech Stack:** Python 3.12 standard library, Git plumbing commands, deterministic executable validators, GitHub Actions.

---

## Chunk 1: Bound the update-rule proof

### Task 1: Add adversarial update-rule regression cases

**Files:**
- Modify: `scripts/operator_store_github_ruleset_protection.py`
- Modify: `scripts/validate_operator_store_ruleset_protection.py`
- Modify: `scripts/validate_operator_store_ruleset_remediation.py`

**Step 1: Write the failing tests**

Extend the deterministic fake-ruleset coverage so the same ruleset detail can be mutated independently of the applied-rule summary. Assert:

- `update_allows_fetch_and_merge: false` remains `PROTECTED`;
- explicit `true` is `UNPROTECTED`;
- a missing `parameters` object is `UNKNOWN`;
- missing `update_allows_fetch_and_merge` is `UNKNOWN`;
- non-boolean values such as `0`, `"false"`, and `null` are `UNKNOWN`;
- `rules` missing, non-list, or containing any non-object entry is `UNKNOWN`;
- a detail that omits the update rule even though the applied-rule endpoint attributes update to that ruleset is `UNKNOWN`;
- a detail that contains an update rule while that ruleset's applied-rule summary omits update is `UNKNOWN`;
- one or more matching update-rule entries whose parameters are all exact boolean `false` remain eligible for `PROTECTED`;
- when multiple matching update-rule entries exist, the order-independent precedence is: any explicit boolean `true` makes the result `UNPROTECTED`; otherwise any malformed entry makes it `UNKNOWN`; otherwise all exact boolean `false` values remain eligible for `PROTECTED`;
- exercise both orderings of a mixed explicit-`true` plus malformed update-rule list and require `UNPROTECTED` in both cases.
- exercise more than 100 applied-rule rows with a permissive update rule only on page 2 and require `UNPROTECTED`;
- require `UNKNOWN` when any required later page fails, is malformed, or repeats a full page without making progress.

Keep the existing provenance and bypass tests unchanged.

**Step 2: Run the focused validators and confirm RED**

Run:

```bash
python3 scripts/validate_operator_store_ruleset_protection.py
python3 scripts/validate_operator_store_ruleset_remediation.py
```

Expected: at least the explicit-`true` case fails because the current verifier classifies it as `PROTECTED`; malformed/mismatched detail cases also fail to return `UNKNOWN`.

**Step 3: Implement the smallest fail-closed rule-detail validator**

Modify `scripts/operator_store_github_ruleset_protection.py`:

- add a helper that receives the applying rule types for one ruleset and its detail;
- retrieve applied branch rules page by page with explicit `per_page=100&page=N` until a page contains fewer than 100 rows; fail `UNKNOWN` on any page error, malformed page, or repeated full-page response so completeness is positively established;
- require `detail["rules"]` to be a list;
- require every element of `detail["rules"]` to be an object;
- for every ruleset whose applied rules include `update`, require one or more matching detail rules;
- require every matching update rule's `parameters` to be a dictionary;
- require every `update_allows_fetch_and_merge` value to be exactly a Python `bool`;
- require the applied summary and detail to agree symmetrically on whether each inspected ruleset contributes update;
- return a tri-state result with order-independent precedence: any explicit `True` becomes `UNPROTECTED`; otherwise absent, malformed, or endpoint-mismatched evidence becomes `UNKNOWN`; only otherwise may all matching exact boolean `False` rules contribute to `PROTECTED`;
- evaluate this before a `PROTECTED` receipt is minted;
- retain the exact Integration-only bypass and repository-provenance checks.

Do not accept truthy/falsy coercions.

**Step 4: Re-run the focused validators and confirm GREEN**

Run both commands from Step 2. Expected: both exit 0 and print their existing success summaries plus the new bounded-update assertions.

**Step 5: Commit**

```bash
git add scripts/operator_store_github_ruleset_protection.py scripts/validate_operator_store_ruleset_protection.py scripts/validate_operator_store_ruleset_remediation.py
git commit -m "fix(operator): verify bounded Store update rules"
```

## Chunk 2: Refuse unknown existing Store history

### Task 2: Add strict existing-ref regression cases

**Files:**
- Modify: `scripts/operator_store_github_ruleset_provision.py`
- Modify: `scripts/validate_operator_store_ruleset_protection.py`
- Modify: `scripts/validate_operator_store_ruleset_remediation.py`

**Step 1: Add reusable Git fixture helpers**

Create temporary bare remotes and writer clones with deterministic Git identity. Add helpers that create and push a chosen commit to `refs/heads/ai-sdlc-operator-state`, return the before SHA, and inspect the after SHA.

Use separate seeder and fresh writer clones so the writer starts without the seeded commit in its object database. Fixtures must cover:

- parentless commit with exactly `state/operator/v1/.bootstrap` and exact content `ai-sdlc-operator-store-bootstrap-v1\n`;
- parentless commit with semantic Store JSON;
- parentless commit with the exact marker plus an extra path;
- parentless commit with the marker path but wrong bytes;
- a commit with a parent, even if its tip tree contains only the exact marker;
- malformed/uninspectable Git object or command failure.

**Step 2: Write the failing tests**

For every invalid fixture:

- call `provision_operator_store_state_ref(...)`;
- require `RulesetProvisioningError`;
- query the remote ref after failure;
- require the SHA is byte-for-byte unchanged;
- require no delete, force push, replacement, or migration occurred.

For the exact parentless marker fixture:

- require `created_state_ref is False`;
- require the returned SHA equals the pre-existing SHA;
- require the remote SHA remains unchanged.

Keep the absent-ref bootstrap test proving a new parentless marker-only commit is created after protection.

**Step 3: Run the focused validators and confirm RED**

Run:

```bash
python3 scripts/validate_operator_store_ruleset_protection.py
python3 scripts/validate_operator_store_ruleset_remediation.py
```

Expected: the semantic JSON, extra-path, wrong-marker, and parented-history cases fail because current `bootstrap_state_ref()` returns any existing SHA without inspecting it.

**Step 4: Implement exact existing-ref validation**

Modify `scripts/operator_store_github_ruleset_provision.py` with a small helper invoked when `_remote_ref_sha()` finds a SHA.

The helper must:

1. validate the SHA shape;
2. acquire the exact object advertised by the remote without updating the remote, force-pushing, or trusting a local symbolic ref (for example, fetch the exact state ref with `--no-tags --no-write-fetch-head` into the object database);
3. require the advertised SHA object to exist after acquisition, then re-read the remote state ref and require it still equals the advertised SHA;
4. inspect the exact commit object and require exactly zero parents;
5. recursively list the commit tree and require exactly one regular blob at `BOOTSTRAP_MARKER_PATH`;
6. read that blob and require exact `BOOTSTRAP_MARKER` bytes;
7. raise `RulesetProvisioningError` on any fetch/Git command failure, ref race, parsing ambiguity, parent, extra/missing path, wrong mode/type, or content mismatch;
8. re-read the remote state ref immediately before success and return the same SHA only if it is still unchanged.

Use non-mutating remote operations and read-only Git plumbing after acquisition (for example `cat-file`, `rev-list --parents -n 1`, and `ls-tree -r -z`). The fetch may populate only the local object database; it must not update the remote or a durable local branch/ref. Do not inspect an ambiguous symbolic ref when the exact advertised SHA is available. Do not treat author, committer, or message text as security evidence.

**Step 5: Re-run the focused validators and confirm GREEN**

Run both focused validators. Expected: exit 0; every invalid pre-seeded ref is rejected without SHA change; exact root reuse and absent-ref bootstrap pass.

**Step 6: Commit**

```bash
git add scripts/operator_store_github_ruleset_provision.py scripts/validate_operator_store_ruleset_protection.py scripts/validate_operator_store_ruleset_remediation.py
git commit -m "fix(operator): reject untrusted existing Store refs"
```

## Chunk 3: Document and verify the strict boundary

### Task 3: Update trusted provisioning documentation

**Files:**
- Modify: `docs/operator-store-provisioning.md`

**Step 1: Update the ruleset proof contract**

Document that all pages of applying branch rules are consumed before proof evaluation. Each applying update ruleset is accepted only when its full detail contains one or more corresponding update rules and every `update_allows_fetch_and_merge` value is the boolean `false`. Apply order-independent precedence: any explicit boolean `true` is `UNPROTECTED`; otherwise zero matching rules, missing or malformed data, non-object rule entries, incomplete pagination, or either-direction endpoint mismatch is `UNKNOWN`.

**Step 2: Update bootstrap/idempotency wording**

Replace “an existing state ref is never recreated” with the strict first-install boundary:

- an existing ref is reused only if it is the exact parentless initialization root;
- all other histories fail closed and remain byte-for-byte unchanged;
- progressed or unknown Stores require a separately reviewed migration/attestation workflow;
- this command remains a first-install/bootstrap reconciler, not a migration tool.

**Step 3: Run the focused and repository validators**

Run:

```bash
python3 scripts/validate_operator_store_ruleset_protection.py
python3 scripts/validate_operator_store_ruleset_remediation.py
python3 scripts/validate_operator_store_runtime.py
python3 scripts/validate_public_runtime_distribution.py
python3 scripts/validate.py
```

Expected: all commands exit 0.

**Step 4: Review the final diff**

Confirm the semantic diff is limited to:

- fail-closed update-rule detail verification;
- fail-closed existing-ref inspection;
- deterministic adversarial tests;
- documentation of the approved boundary.

Confirm no workflow credential changes, no live GitHub ruleset calls, no state-ref provisioning, no Feature Manifest changes, and no release artifact changes.

**Step 5: Commit**

```bash
git add docs/operator-store-provisioning.md
git commit -m "docs(operator): define strict Store bootstrap reuse"
```

## Chunk 4: Exact-head release evidence

### Task 4: Publish and review the tested candidate

**Files:**
- No additional source changes unless review finds a concrete defect.

**Step 1: Publish only if the PR branch head is unchanged**

Re-read PR #242. Build the commit on the then-current head and move `installation/operator-store-ruleset-241` with a non-force fast-forward update. Abort on head drift.

**Step 2: Wait for fresh exact-head CI**

Require success for every PR-required workflow on the new exact head. At minimum verify the exact check names `Required PR Gate`, `Validate AI-SDLC protocol`, `Validate Public Runtime Distribution`, `Validate AI-SDLC gh-aw Worker Compile`, and `Validate stale callback reconciliation`. Historical green runs do not count.

**Step 3: Request independent exact-head review**

The reviewer must verify the approved spec, code, adversarial tests, and fresh CI. Merge remains blocked unless the new exact head receives PASS with 0 BLOCKER and 0 MAJOR.

**Step 4: Preserve authority boundaries**

Do not merge PR #242, create live repository rulesets, create or rewrite `refs/heads/ai-sdlc-operator-state`, change `VERSION`, create `release/v0.3.0.yaml`, or claim v0.3 release readiness. Those remain explicit human decisions.
