# Work Unit Plan — Feature #8

Planning issue: #15

## WU-1 — Protocol + validator
Issue: #16
Depends on: design approval
Allowed scope: `spec/task-execution.schema.json`, `scripts/validate_transition.py`, execution examples, validation wiring.
DoD: schema validates; legal transitions pass; illegal transitions and invariant violations fail deterministically.

## WU-2 — Manual runtime persistence documentation
Issue: #17
Depends on: design approval
Allowed scope: feature documentation and `runtimes/chatgpt-web/` documentation.
DoD: persistence mapping, operator transition guidance and evidence rules documented.

## WU-3 — Fixtures + CI coverage
Issue: #18
Depends on: WU-1
Allowed scope: `examples/execution/**`, validation wiring.
DoD: positive and negative fixtures run in CI.

## DAG
WU-1 and WU-2 may proceed in parallel. WU-3 starts after WU-1. Final review/verification #13 depends on WU-1, WU-2 and WU-3.

## Scope ownership
- WU-1 primary writer: protocol/validator files.
- WU-2 primary writer: documentation files.
- WU-3 primary writer: fixtures/validation coverage.

No two work units require concurrent primary edits to the same file except validation wiring, which is owned by WU-3 after WU-1 completes.
