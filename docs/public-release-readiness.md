# Public repository readiness

This checklist is the authority for changing `DREAM-XIN/ai-sdlc` from private to public.

The visibility change is intentionally separated from code preparation. The repository must remain private until the pre-change checks below are complete.

## Why this exists

Making the control-plane repository public simplifies target installation because target repositories can reference the immutable AI-SDLC control Action directly. It also changes the exposure boundary: Git history, repository contents, Actions history/logs, artifacts that remain available, Issues, Pull Requests, and other repository metadata may become visible according to GitHub's visibility rules.

Current-tree validation alone is therefore necessary but not sufficient.

## Required pre-change checks

1. Merge a green Public-readiness preparation change that includes:
   - an explicit open-source license;
   - current tracked-tree secret scanning in CI;
   - existing GitHub Actions security validation.
2. Run the `Public Release Audit` workflow from the commit that is intended to become Public. It must finish with `PUBLIC-READY-AUDIT: PASS` and publish `public-release-audit: success` on that exact commit.
3. Review or delete historical GitHub Actions runs and downloadable artifacts that must not become public. Historical logs must be treated as publishable data after the visibility change.
4. Verify that no repository file contains real runtime credentials. Secret *names* and examples are allowed; secret values are not.
5. Run `Public Settings Snapshot`, retain its sanitized result, and manually capture every administration endpoint that it reports as unavailable. The combined snapshot must cover the current default branch, branch protection/rulesets, required status checks, environments, repository variables, and Actions settings so those settings can be revalidated after the visibility change.
6. Confirm that runtime credentials remain stored only in GitHub Secrets or the external credential system that owns them.
7. Confirm that the target-install templates still pin `DREAM-XIN/ai-sdlc/.github/actions/control` to a reviewed full 40-character commit SHA.
8. Review non-secret information exposure separately, including names and engineering details of Private dogfood repositories referenced by Issues, Pull Requests, docs, logs, or artifacts.

## Current-tree scanner

Run:

```bash
python scripts/validate_public_readiness.py
```

The validator fails on obvious committed credential formats and tracked private-key/certificate file extensions. It deliberately does not claim to audit removed Git history or historical Actions logs.

## Automated historical audit

The `Public Release Audit` workflow is the reproducible pre-publication gate. It:

- checks out complete repository history with `fetch-depth: 0`;
- fetches branches, tags, and GitHub Pull Request head/merge refs that may become inspectable after publication;
- runs `scripts/validate_public_history.py` against all reachable Git blobs;
- enumerates historical Actions runs through the GitHub API;
- downloads every retained run log archive that GitHub still makes available;
- enumerates retained repository Actions artifacts and downloads every non-expired artifact within the configured size limit;
- recursively scans ZIP content, including nested ZIPs, without printing matched credential values;
- fails closed when an artifact or archive entry exceeds the configured scan limit;
- publishes a combined result to the workflow job summary as `PUBLIC-READY-AUDIT: PASS` or `PUBLIC-READY-AUDIT: BLOCKED`;
- publishes the same decision as commit status context `public-release-audit`, so the exact `main` publication-candidate SHA can be verified independently of workflow-run discovery.

The workflow intentionally does not upload its audit reports as new artifacts, because doing so would create additional material that itself becomes part of the publication surface.

The historical scanners detect obvious GitHub, OpenAI/DeepSeek-style, Anthropic, Google, AWS, and private-key credential formats. A green automated result is a strong technical gate but does not replace human review of non-secret business or dogfood information exposure.

## Sanitized repository-settings snapshot

Run `Public Settings Snapshot` before changing visibility. It executes `scripts/snapshot_public_settings.py` using a read-only workflow token and is deliberately safe for retained Actions logs:

- GitHub Secrets are never queried;
- repository-variable values are never read or printed;
- repository-variable names, when readable, are represented only by count plus a SHA-256 fingerprint of the sorted names;
- Environment names are represented only by count plus a SHA-256 fingerprint of the sorted names;
- user, reviewer, and team identities are not emitted;
- administration endpoints that `GITHUB_TOKEN` cannot read are reported as HTTP status plus explicit manual follow-up rather than guessed.

`SETTINGS-SNAPSHOT: COMPLETE` means the workflow could record every required non-secret administration category that its contract covers. `SETTINGS-SNAPSHOT: PARTIAL` is not a release failure by itself, but every item named in `manual_follow_up` remains a mandatory operator check in the GitHub repository Settings UI before changing visibility.

The snapshot is intended for before/after comparison, not as a replacement for GitHub's authoritative Settings pages. In particular, rulesets and organization-level policy can apply independently of classic branch protection, so `default_branch.protected: false` must not be interpreted as proof that no rule constrains `main` when the rulesets endpoint is unavailable.

## Local full-history scan

The Git-history component can also be run from a complete local clone:

```bash
git fetch --all --tags --prune
git fetch --force origin \
  '+refs/pull/*/head:refs/remotes/pull/*/head' \
  '+refs/pull/*/merge:refs/remotes/pull/*/merge'
python scripts/validate_public_history.py \
  --json-output public-history-audit.json \
  --markdown-output public-history-audit.md
```

Any confirmed credential discovered in history must be revoked/rotated even if the history is later rewritten.

## Visibility change

Only after the checks above pass:

1. Change repository visibility from Private to Public in GitHub repository settings.
2. Immediately re-run `Public Settings Snapshot` and manually re-check every administration category that was unavailable before the change.
3. Re-check repository Actions settings and branch/ruleset protection against the pre-change snapshot.
4. Re-run the normal validation workflow on `main`.
5. Verify a target repository can execute `plan` using an immutable direct Action reference.
6. Verify a mutating lifecycle operation still respects Feature-branch protection and optimistic revision rules.

## Public transport simplification

The current repository contains a compatibility bridge for a Public target calling a Private control repository. That bridge must not be removed before the visibility change because it is the working transport while the control repository is private.

After the repository is Public and direct Action access is proven:

- keep the lifecycle protocol unchanged;
- keep `plan`, `bootstrap`, and `persist` implemented by the same trusted control Action;
- prefer the target-installed workflows that call `DREAM-XIN/ai-sdlc/.github/actions/control@<full-sha>` directly;
- remove the Public-target-only dispatch through the private-control lifecycle workflow once no supported installation depends on it;
- remove the target-side `AI_SDLC_CONTROL_DISPATCH_TOKEN` requirement once the compatibility bridge is removed;
- retain GitHub App credentials only for central autonomous/cross-repository execution paths that genuinely need credentials to operate on another repository.

This is a transport migration only. It must not change Feature Event semantics, transition authority, persistence validation, revision checks, Gates, or Feature-branch write rules.

## Rollback boundary

If direct Action execution fails after visibility changes, do not bypass AI-SDLC state transitions or edit Feature Manifests manually. Restore the previous supported transport or revert the transport migration while keeping lifecycle state authoritative in the target repository.

## Completion criteria

Public conversion is complete only when:

- current-tree scan is green;
- `Public Release Audit` is green and `public-release-audit: success` is present on the exact publication-candidate commit;
- full-history findings are reviewed;
- historical Actions/log/artifact exposure is accepted or cleaned;
- non-secret Private-dogfood information exposure is explicitly accepted or anonymized;
- license is present;
- the sanitized Settings snapshot plus manual follow-up forms a complete pre-change settings record;
- repository protection/settings are revalidated after the visibility change;
- direct immutable-SHA lifecycle execution is green against a real target;
- the private-control compatibility bridge can be retired without changing lifecycle semantics.
