# Feature Bootstrap and Event Inbox

This layer defines how a new Feature enters AI-SDLC and how workers report lifecycle changes without editing authoritative state directly.

## State layout

```text
state/
├── features/
│   └── F-123.yaml
└── events/
    └── F-123/
        ├── EVT-F123-REQ-START.yaml
        ├── EVT-F123-REQ-DONE.yaml
        └── ...
```

`state/features/<feature-id>.yaml` is authoritative lifecycle state.

`state/events/<feature-id>/<event-id>.yaml` is an append-oriented input/audit surface. Event identity is also recorded inside the Feature Manifest as `applied_events`, so idempotency does not depend on GitHub filenames alone.

## Bootstrap

A bootstrap input identifies the Feature id/title/risk, optional system-of-record Issue, Workflow Profile and creation timestamp.

`bootstrap_feature.py` creates a Feature Manifest by:

1. loading the selected Workflow Profile;
2. setting the first stage to `READY` and later stages to `TODO`;
3. creating every referenced Gate in `PENDING`;
4. initializing empty task/artifact/evidence/event ledgers;
5. initializing `revision: 0`;
6. semantically validating the result.

The new manifest can immediately be passed to the Orchestrator to obtain the first `DISPATCH` action.

## Event identity and replay safety

New Feature Events should contain a stable explicit `id`.

For backward compatibility with existing `0.1.x` documents, the core transition engine still accepts an event without `id`; it derives a deterministic `legacy-...` identity from canonical event content. The effective identity is appended to `applied_events`, and replaying the same identity returns `INVALID` before lifecycle state is modified.

## Revision and stale-event safety

A Feature Manifest has a monotonic integer `revision`. Commander exposes the current revision to workers.

A repository Event Inbox event must declare the revision it was prepared against:

```yaml
expected_revision: 7
```

If the current Manifest revision is not 7, the event is stale and returns `INVALID` without modifying state. A valid event based on revision 7 produces revision 8.

This is optimistic concurrency, not a distributed lock. Parallel engineering work remains allowed; only authoritative Feature-state mutations are serialized. A stale worker must re-read current state and confirm that its result is still valid before regenerating the event.

The generic low-level Feature Event protocol keeps `expected_revision` optional for `0.1.x` compatibility. The repository Event Inbox requires it for persisted events.

See `docs/optimistic-concurrency.md` for the complete model.

## Event inbox rules

The repository Event Inbox is stricter than the generic Feature Event protocol: Inbox events must provide an explicit `id` and `expected_revision`.

`ingest_feature_event.py` accepts only paths shaped as:

```text
state/events/<feature-id>/<event-id>.yaml
```

The directory Feature id must equal `event.feature_id`, and the filename stem must equal the explicit `event.id`.

The inbox validator then delegates to the vendor-neutral Feature Event transition engine and GitHub Persistence Plan generator.

## Worker contract

A worker such as ChatGPT Web must not directly edit the Feature Manifest to claim progress.

Instead it should write an event such as:

```yaml
version: 0.1.0
id: EVT-F123-DESIGN-DONE
feature_id: F-123
expected_revision: 7
occurred_at: '2026-08-07T12:00:00Z'
changes:
  - kind: stage
    id: design
    status: DONE
```

Before submitting, compare `expected_revision` with the latest Feature Manifest. If it changed, do not simply substitute the newer number: re-read state and confirm that the proposed change remains valid.

Gate verdicts still require explicit Evidence in the same or prior event; a worker's self-report is not sufficient evidence by itself.

## End-to-end loop

```text
Feature Bootstrap
      ↓
Feature Manifest (revision N)
      ↓
Orchestrator → Runtime Router → Task Package
      ↓
Worker reads revision N
      ↓
Feature Event expected_revision=N
      ↓
Inbox validation
      ↓
Transition Engine
      ↓
Persistence Plan N → N+1
      ↓
Git remote-write precondition
      ↓
Updated Feature Manifest (revision N+1)
      ↓
Next Orchestrator state
```
