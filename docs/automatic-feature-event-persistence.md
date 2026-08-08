# Automatic Feature Event persistence

AI-SDLC v0.2 adds an automatic persistence path for trusted Feature Event commits while keeping the event-sourced Feature Manifest as the only authoritative workflow state.

## Automatic path

`AI-SDLC Persist Feature Event` now listens for pushes that touch `state/events/**/*.yaml` or `state/events/**/*.yml`.

For a normal branch push the workflow:

1. checks out the trusted AI-SDLC control plane from the repository default branch;
2. checks out the pushed target branch with full git history;
3. resolves the push range and requires exactly one added or modified Feature Event;
4. derives `state/features/<feature-id>.yaml` from `state/events/<feature-id>/<event-id>.yaml`;
5. validates that the event and manifest paths belong to the same Feature;
6. runs the existing trusted Feature Event ingest/materialization path;
7. validates the materialized Feature Manifest;
8. runs the existing optimistic git-write precondition; and
9. commits only the Feature Manifest back to the same branch.

The persistence commit changes `state/features/**`, not `state/events/**`, so it does not recursively trigger automatic persistence.

## Fail-closed behavior

Automatic persistence refuses to continue when:

- the push is the branch-creation push (the `before` SHA is all zeroes);
- the push contains zero eligible Feature Event files;
- the push contains more than one eligible Feature Event file;
- the inferred Feature Manifest path does not match the Feature id in the event path;
- the target is the default branch, because automatic mode never sets `allow_default_branch=true`;
- the event fails protocol validation or its `expected_revision` does not match the authoritative manifest; or
- the optimistic remote branch precondition detects that the branch moved before the write.

These constraints intentionally prefer an explicit retry/recovery action over guessing ordering or silently applying ambiguous event batches.

## Manual recovery path

`workflow_dispatch` remains available and keeps the existing inputs:

- `manifest_path`
- `event_path`
- `target_ref`
- optional `feature_issue`
- `dry_run`
- `allow_default_branch`

Use the manual path for recovery, branch-creation cases, or an explicitly reviewed default-branch write. Manual persistence still runs through the same trusted ingest, manifest validation, and optimistic git-write precondition.

## Cross-repository installs

`templates/github/ai-sdlc-persist.yml` exposes the same two modes for installed repositories. Automatic pushes are resolved inside the caller workflow before invoking the pinned AI-SDLC control action; the actual persistence semantics remain inside the trusted pinned control action.
