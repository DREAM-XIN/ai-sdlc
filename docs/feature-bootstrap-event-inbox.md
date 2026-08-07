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

A bootstrap input identifies:

- Feature id, title, risk, and optional system-of-record Issue;
- Workflow Profile;
- creation timestamp.

`bootstrap_feature.py` creates a Feature Manifest by:

1. loading the selected Workflow Profile;
2. setting the first stage to `READY` and later stages to `TODO`;
3. creating every referenced Gate in `PENDING`;
4. initializing empty task/artifact/evidence/event ledgers;
5. semantically validating the result.

The new manifest can immediately be passed to `orchestrator_state.py` to obtain the first `DISPATCH` action.

## Event identity and replay safety

Every Feature Event contains a stable `id`.

After a successful transition, that id is appended to `applied_events` in the Feature Manifest. Applying the same id again returns `INVALID` before lifecycle state is modified.

This protects against repeated ChatGPT submissions, retried CI jobs, duplicated webhooks, and manual replay.

## Event inbox rules

`ingest_feature_event.py` accepts only paths shaped as:

```text
state/events/<feature-id>/<event-id>.yaml
```

The directory Feature id must equal `event.feature_id`, and the filename stem must equal `event.id`.

The inbox validator then delegates to the vendor-neutral Feature Event transition engine and GitHub Persistence Plan generator.

## Worker contract

A worker such as ChatGPT Web must not directly edit the Feature Manifest to claim progress.

Instead it should write an event such as:

```yaml
version: 0.1.0
id: EVT-F123-DESIGN-DONE
feature_id: F-123
occurred_at: '2026-08-07T12:00:00Z'
changes:
  - kind: stage
    id: design
    status: DONE
```

Gate verdicts still require explicit Evidence in the same or prior event; a worker's self-report is not sufficient evidence by itself.

## End-to-end loop

```text
Feature Bootstrap
      ↓
Feature Manifest
      ↓
Orchestrator → Runtime Router → Task Package
      ↓
Worker
      ↓
Feature Event in state/events/
      ↓
Inbox validation
      ↓
Transition Engine
      ↓
Persistence Plan
      ↓
Updated Feature Manifest
      ↓
Next Orchestrator state
```
