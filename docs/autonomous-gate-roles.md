# Autonomous Code Review and Verification

AI-SDLC can execute two independent Gate roles through bounded gh-aw workers without giving the model direct lifecycle authority:

- `reviewer + code-review`;
- `qa + verification`.

The existing autonomous Developer path remains separate. Product, Requirement Review, Architect, Design Review, Orchestrator and Product Acceptance remain manual in this capability set.

## Runtime and profile routing

Trusted routing is role-and-stage specific:

```text
developer + implementation -> codex -> copilot
reviewer  + code-review    -> claude -> copilot
qa        + verification   -> gemini -> copilot
```

The arrow describes static-readiness fallback. It does not mean a failed live inference request automatically retries on the next provider.

Target Issue Comments and Project Adapters cannot choose the provider, model, profile, worker workflow, credential, candidate order, verdict or experimental opt-in.

## Independence is a control boundary

Autonomous Reviewer and QA are not continuations of the Developer worker. They use separate compiled workers, separate result contracts and separate lifecycle stages.

A Gate-role worker:

- checks out the exact trusted candidate commit SHA;
- has read-only agent permissions;
- may inspect repository, Feature Issue, PR/diff, approved artifacts and CI evidence;
- may post exactly one bounded result comment through gh-aw `add-comment` Safe Output;
- cannot create a PR, push a branch, edit source, edit Feature state, merge or release.

The Safe Output comment is explicitly non-authoritative. The worker cannot directly set a Gate to PASS.

## Immutable candidate binding

The trusted Developer result collector resolves the implementation PR through GitHub and records an implementation candidate plus immutable head artifact. The model does not choose the trusted head SHA.

Before Reviewer or QA dispatch, the gateway binds the task to:

```text
candidate PR number + exact 40-character head SHA
```

The worker checks out that SHA instead of a mutable branch head.

The gateway checks the current PR head immediately before dispatch. The Gate-result collector checks it again when receiving the recommendation and once more immediately before lifecycle persistence. If the PR moves at any of those boundaries, the stale review/verification result is rejected and the new candidate must go through the required independent stage again.

Historical candidates remain durable. A later candidate supersedes the previous current candidate instead of silently rebinding old evidence to new code.

A manual implementation artifact can participate in the same resolver only when trusted lifecycle context has explicitly bound it to a canonical PR artifact and one immutable `implementation-head`. A documentation-only `implementation-v1` is never guessed to be a PR candidate; manual Code Review/QA remains available when no trusted candidate binding exists.

## Reviewer result contract

The Code Reviewer worker can recommend only:

- `PASS`;
- `REWORK`;
- `BLOCKED`.

A valid PASS recommendation must have pass Evidence and no BLOCKER/MAJOR finding. The trusted collector then validates the result schema, Feature/revision, role/stage, target repository/ref, PR/head identity and current Manifest before constructing the normal Feature Event that can:

- approve the exact reviewed implementation candidate;
- record the reviewed candidate head;
- PASS `code-gate` with Evidence;
- mark `code-review` DONE;
- make `verification` READY.

The worker itself performs none of those writes.

For REWORK, the collector records truthful review Evidence and creates a bounded Developer remediation task. The Reviewer does not implement the fix. After remediation, a fresh independent Code Review is required.

## QA result contract

The Verification QA worker can recommend only:

- `PASS`;
- `FAIL`;
- `BLOCKED`.

QA can run only against the exact implementation candidate already approved by Code Review and the matching reviewed-candidate head.

A valid PASS may be translated by the trusted collector into:

- durable Verification Evidence;
- `verification-gate` PASS;
- `verification` DONE;
- `acceptance` READY.

QA has no `release-gate` authority. Product Acceptance remains a later manual role for `standard-feature`.

A FAIL/BLOCKED result never advances Acceptance and never lets QA edit implementation while self-verifying.

## Safe Output and trusted collection

The Gate worker posts one machine envelope plus a human summary to the trusted candidate PR. The comment is only transport.

The control collector re-fetches the comment and candidate through GitHub, verifies the comment belongs to the expected PR and was written through the trusted Bot/Safe Output path, requires exactly one machine envelope, validates the closed role-specific schema, and normalizes Evidence to durable trusted references.

Only then does trusted code construct a Feature Event. The Event still passes through normal event ingestion, transition validation, optimistic `expected_revision` checking, Manifest validation and Persist.

This preserves the core invariant:

```text
model recommendation != authoritative Gate state
```

## Same-repository and cross-repository execution

Both gateways enforce the same candidate rules.

For a cross-repository target, the control repository mints a short-lived Runtime App token scoped to the exact target repository. PR-head checks use that exact-target token. Gate workers still run from trusted compiled workflows and receive only the target identity, role/stage task package and immutable candidate identity required for the assignment.

The target cannot inject a different worker or verdict through the Issue Comment command surface.

## Failure and retry behavior

If a Reviewer requests REWORK:

```text
Reviewer recommendation
  -> trusted REWORK Event
  -> Developer remediation task
  -> new candidate
  -> independent Reviewer again
```

If a candidate head moves before or during Gate collection, the stale verdict is rejected. Do not manually rewrite the Manifest to keep the old PASS.

If a provider is statically unavailable, trusted profile routing may select its configured fallback. A runtime provider failure after dispatch is not treated as automatic cross-provider retry.

## Manual fallback

Manual Code Review and QA remain valid lifecycle execution modes. Use the same independent-role rules and durable Evidence requirements. Autonomous routing changes the execution transport, not the Gate semantics.

Product Acceptance remains manual and is still required after Verification for the `standard-feature` profile.

## Security checklist

For autonomous Reviewer/QA, verify:

- [ ] the role/stage pair is exactly `reviewer/code-review` or `qa/verification`;
- [ ] candidate PR/head is present in trusted state;
- [ ] current PR head equals the candidate SHA;
- [ ] the compiled role worker is registered for the selected profile;
- [ ] the worker source/lock contains no create-PR or push Safe Output;
- [ ] the result comment is treated as non-authoritative;
- [ ] the trusted collector validates the closed result schema and expected revision;
- [ ] candidate head is re-checked before persistence;
- [ ] Reviewer REWORK routes to Developer remediation rather than self-fix;
- [ ] QA never advances `release-gate`;
- [ ] Acceptance remains independent/manual.

See also [Autonomous development](autonomous-development.md), [Role guide](role-guide.md), [Cross-repository autonomous execution](cross-repository-autonomous-execution.md), and [Security model](security-model.md).
