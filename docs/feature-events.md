# Feature events and deterministic lifecycle updates

Workers and CI should not replace an entire Feature Manifest. They submit a small, explicit Feature Event; the transition engine validates the event, applies legal changes, recomputes workflow summary state, and semantically validates the resulting manifest.

```text
Feature Manifest
      +
Feature Event
      |
      v
apply_feature_event.py
      |
      +-- invalid transition -> INVALID
      |
      v
Updated Feature Manifest
      |
      v
orchestrator_state.py
      |
      v
next DISPATCH / WAIT / BLOCKED / COMPLETE
```

## Event changes

v0.1 supports three change kinds:

- `stage`: move one stage through the legal lifecycle (`READY`, `WORKING`, `REVIEW`, `DONE`, `BLOCKED`, `SKIPPED`). A BLOCKED event must include a reason.
- `gate`: move a known Gate among `PENDING`, `PASS`, `FAIL`, and `WAIVED`. PASS/FAIL/WAIVED require durable evidence references.
- `evidence`: append one uniquely identified durable evidence record.

Evidence in an event is appended before Gate changes, so the same event can introduce evidence and then reference it from a Gate.

## Safety properties

- Feature identity cannot change.
- Unknown Stage/Gate IDs fail closed.
- Terminal Stage states cannot silently reopen.
- Gate reopen is explicit via `PASS|FAIL|WAIVED -> PENDING`.
- Duplicate evidence IDs fail closed.
- The resulting Feature Manifest must pass semantic validation.
- `current_stage` is recomputed as a navigation summary; scheduling still uses Stage statuses and Workflow Profile dependencies.

## Closed loop

A typical manual ChatGPT Web turn becomes:

1. Commander computes `DISPATCH(design, architect)`.
2. Runtime Router generates a ChatGPT Web Task Package.
3. The worker starts; a Stage event changes `design: READY -> WORKING`.
4. Durable outputs/evidence are written to GitHub.
5. Completion event changes `design: WORKING -> DONE`.
6. The updated manifest is fed back to the Commander.
7. Commander deterministically dispatches `design-review`.

The Feature Event itself should be persisted or reconstructable by the GitHub adapter for auditability; browser chat remains transport, not state.
