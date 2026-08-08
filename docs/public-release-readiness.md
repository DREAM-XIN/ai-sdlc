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
2. Run a full Git-history secret scan from a complete clone (`fetch-depth: 0`). Do not rely on the normal shallow Actions checkout for this check.
3. Review or delete historical GitHub Actions runs and downloadable artifacts that must not become public. Historical logs must be treated as publishable data after the visibility change.
4. Verify that no repository file contains real runtime credentials. Secret *names* and examples are allowed; secret values are not.
5. Record the current default branch, branch protection/rulesets, required status checks, environments, repository variables, and Actions settings so they can be revalidated after the visibility change.
6. Confirm that runtime credentials remain stored only in GitHub Secrets or the external credential system that owns them.
7. Confirm that the target-install templates still pin `DREAM-XIN/ai-sdlc/.github/actions/control` to a reviewed full 40-character commit SHA.

## Current-tree scanner

Run:

```bash
python scripts/validate_public_readiness.py
```

The validator fails on obvious committed credential formats and tracked private-key/certificate file extensions. It deliberately does not claim to audit removed Git history or historical Actions logs.

## Recommended full-history scan

Use a complete local clone and a dedicated history-aware secret scanner before changing visibility. The exact scanner is an operator choice; the important requirement is that all refs and reachable historical objects are included.

A minimal prerequisite is:

```bash
git fetch --all --tags --prune
```

Then run the selected history scanner against all refs. Any confirmed credential discovered in history must be revoked/rotated even if the history is later rewritten.

## Visibility change

Only after the checks above pass:

1. Change repository visibility from Private to Public in GitHub repository settings.
2. Immediately re-check repository Actions settings and branch/ruleset protection.
3. Re-run the normal validation workflow on `main`.
4. Verify a target repository can execute `plan` using an immutable direct Action reference.
5. Verify a mutating lifecycle operation still respects Feature-branch protection and optimistic revision rules.

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
- full-history scan is reviewed;
- historical Actions/log exposure is accepted or cleaned;
- license is present;
- repository protection/settings are revalidated;
- direct immutable-SHA lifecycle execution is green against a real target;
- the private-control compatibility bridge can be retired without changing lifecycle semantics.
