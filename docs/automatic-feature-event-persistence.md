# Automatic Feature Event persistence

AI-SDLC v0.2 adds an automatic persistence path for trusted Feature Event commits while keeping the event-sourced Feature Manifest as the only authoritative workflow state.

## Automatic path

`AI-SDLC Persist Feature Event` listens for pushes that touch `state/events/**/*.yaml` or `state/events/**/*.yml`.

For a normal branch push containing a new Event the workflow:

1. checks out the trusted AI-SDLC control plane from the repository default branch;
2. checks out the pushed target branch with full git history;
3. resolves the pushed git range;
4. requires exactly one not-yet-applied Feature Event;
5. derives `state/features/<feature-id>.yaml` from `state/events/<feature-id>/<event-id>.yaml`;
6. validates that the event and manifest paths belong to the same Feature;
7. runs the existing trusted Feature Event ingest/materialization path;
8. validates the materialized Feature Manifest;
9. runs the existing optimistic git-write precondition; and
10. commits only the Feature Manifest back to the same branch.

The persistence commit changes `state/features/**`, not `state/events/**`, so it does not recursively trigger automatic persistence.

## Archive idempotency

A completed Feature may later archive its durable Event YAML files into another branch or into `main`. Those files are historical evidence, not new lifecycle mutations. Such a push may contain one or many Feature Event files.

The automatic resolver classifies the push as a successful `noop` only when **every** changed Event is provably already applied:

- the Event is schema-valid;
- the directory Feature id, Event `feature_id`, filename, and Event `id` agree;
- the matching Feature Manifest exists and has the same Feature id;
- `expected_revision` is a non-negative integer;
- the Event id appears exactly once in `manifest.applied_events`; and
- its zero-based position in `applied_events` is exactly the Event's `expected_revision`.

For example, an Event with `expected_revision: 2` is an idempotent replay only when `manifest.applied_events[2]` is that exact Event id and the Manifest revision is at least 3.

When the whole push satisfies that proof, the workflow reports that no persistence mutation is required and skips default-branch write protection, ingest, materialization, and git write steps. This allows a final archive merge into `main` to stay green without granting automatic write authority to the default branch.

## Fail-closed behavior

Automatic persistence refuses to continue when:

- the push is the branch-creation push (the `before` SHA is all zeroes);
- the push contains zero eligible Feature Event files;
- the push contains more than one Event and at least one of them is not an exact already-applied replay;
- an applied Event id appears at a different revision slot than its `expected_revision`;
- an Event id is duplicated in `applied_events`;
- the inferred Feature Manifest path or Feature id does not match the Event;
- a new Event targets the default branch, because automatic mutation mode never sets `allow_default_branch=true`;
- the new Event fails protocol validation or its `expected_revision` does not match the authoritative Manifest; or
- the optimistic remote branch precondition detects that the branch moved before the write.

These constraints intentionally distinguish archive idempotency from error suppression. Invalid, stale, reordered, mixed archive/new batches, or ambiguous Event pushes remain failures.

## Manual recovery path

`workflow_dispatch` remains available and keeps the existing inputs:

- `manifest_path`
- `event_path`
- `target_ref`
- optional `feature_issue`
- `dry_run`
- `allow_default_branch`

Use the manual path for recovery, branch-creation cases, or an explicitly reviewed default-branch write. Manual persistence still runs through the same trusted ingest, Manifest validation, and optimistic git-write precondition. The archive `noop` classification is specific to automatic push handling; it does not silently change manual recovery semantics.

## Cross-repository installs

`templates/github/ai-sdlc-persist.yml` exposes the same behavior for installed repositories. Automatic pushes are classified by the pinned `.github/actions/resolve-event-push` action from the same immutable AI-SDLC commit as the pinned control action. New Events then invoke `.github/actions/control`; proven archive no-ops skip the write action entirely.
