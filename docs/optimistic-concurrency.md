# Optimistic concurrency

AI-SDLC permits workers to execute independent work in parallel, but authoritative Feature-state mutations are protected by a monotonic Manifest revision and a Git branch precondition.

## Feature revision

A newly bootstrapped Feature Manifest starts with:

```yaml
revision: 0
```

Every successful Feature Event increments the revision exactly once.

A repository Event Inbox event must declare the revision it was prepared against:

```yaml
version: 0.1.0
id: EVT-F123-DESIGN-DONE
feature_id: F-123
expected_revision: 7
occurred_at: '2026-08-07T13:30:00Z'
changes:
  - kind: stage
    id: design
    status: DONE
```

If the authoritative Manifest is still revision 7, the transition may proceed and the result is revision 8. If another event has already advanced the Manifest, the event is rejected as stale without mutating state.

The low-level Feature Event schema keeps `expected_revision` optional for compatibility with earlier 0.1.x documents. The repository Event Inbox requires it for new persistence operations.

## Worker behavior

Commander exposes the current revision and includes it in manual ChatGPT Web instructions.

A worker should:

1. read Manifest revision N;
2. perform the assigned work;
3. prepare artifacts/evidence;
4. emit a Feature Event with `expected_revision: N`;
5. if the event is rejected as stale, re-read the current Manifest and related artifacts;
6. decide whether the result still applies before generating a new event.

Do not fix a stale event by mechanically changing only the revision number. The state may have changed in a way that invalidates the worker's assumptions.

## Parallel work

Revision control does not prevent backend and frontend workers from working simultaneously.

Example:

```text
Manifest revision 12
      │
      ├── backend worker reads 12
      └── frontend worker reads 12

backend event expected=12 → PASS → revision 13
frontend event expected=12 → STALE
frontend re-reads revision 13
frontend confirms result remains valid
frontend event expected=13 → PASS → revision 14
```

This serializes only the durable state mutations, not the underlying engineering work.

## Persistence Plan precondition

A GitHub Persistence Plan records both source and result identity:

```text
source_revision
source_sha256
revision
sha256
```

The update-file mutation also carries `source_sha256`. This makes the intended compare-and-set boundary auditable even though the current GitHub transport persists state through Git commits.

## Git remote branch precondition

Revision safety protects the Feature Manifest. A second race can still happen after the plan is built but before Git push if another workflow advances the target branch.

Write-capable transports therefore run `verify_git_write_precondition.py` before committing/pushing. It verifies:

```text
checkout HEAD SHA == live origin target-branch SHA
```

If the remote branch has advanced, the workflow fails closed as `STALE` and tells the operator to refresh state. Git non-fast-forward rejection remains a final safety net rather than the primary concurrency mechanism.

The same guard is used by:

- the shared cross-repository Control Action for persisted bootstrap/event operations;
- the GitHub-native Feature Event persistence workflow;
- the GitHub-native Commander bootstrap persistence path.

## Why both checks exist

```text
Feature revision
    protects semantic lifecycle state

Git branch SHA
    protects repository write freshness
```

A valid expected revision is not sufficient if the checked-out branch itself is stale, and a fresh Git checkout is not sufficient if a worker's event was produced from an older Feature revision.

Both checks must pass before a persisted state mutation is considered safe.
