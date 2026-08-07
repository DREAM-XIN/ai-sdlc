# Design — Manual Runtime Task State Tracking

Feature: #8
Design issue: #10

## Context
AI-SDLC already defines Task, Runtime, Artifact, Evidence and Gate contracts plus a ChatGPT Web manual transport. Missing is a canonical execution-attempt state model between dispatch and gate evaluation.

## Decision
Add a new protocol primitive: `TaskExecution`. It represents one execution attempt for one Task on one Runtime.

### Canonical states
`READY`, `STARTED`, `BLOCKED`, `SUBMITTED`, `COMPLETED`, `FAILED`.

### Allowed transitions
- READY -> STARTED
- READY -> FAILED
- STARTED -> BLOCKED
- STARTED -> SUBMITTED
- STARTED -> FAILED
- BLOCKED -> STARTED
- BLOCKED -> FAILED
- SUBMITTED -> STARTED (rework requested)
- SUBMITTED -> COMPLETED
- SUBMITTED -> FAILED

COMPLETED and FAILED are terminal.

## Data model
A TaskExecution record contains:
- `id`: execution identifier
- `feature_id`: optional feature correlation
- `task_id`: canonical Task id
- `runtime_id`: canonical Runtime id
- `state`: current state
- `previous_state`: required after initial creation
- `updated_at`: RFC3339 timestamp
- `reason`: state-transition explanation; required for BLOCKED
- `failure_detail`: required for FAILED
- `evidence`: non-empty array required for COMPLETED
- `actor`: optional human/agent identity

The schema models a snapshot after a transition. Transition legality is enforced by a deterministic validator because JSON Schema alone is not the best place to express state-machine history semantics.

## Invariants
1. BLOCKED requires `reason`.
2. FAILED requires `failure_detail`.
3. COMPLETED requires at least one `evidence` reference.
4. For non-READY records, `previous_state` is required.
5. A validator rejects a transition not present in the allowlist.
6. Identity fields (`task_id`, `runtime_id`, `id`) may not change between before/after snapshots.

## Persistence mapping
Canonical records are repository/GitHub durable data, not chat state. Reference implementation stores examples as YAML/JSON artifacts; consuming projects may persist a current snapshot in an issue body/comment, repository state file, database, or Project field as long as it maps losslessly to the protocol record.

GitHub labels are discovery aids only. The adapter must be able to reconstruct the canonical state record.

## CLI
`scripts/validate_transition.py BEFORE AFTER`

The CLI:
1. validates both documents against `spec/task-execution.schema.json`;
2. confirms stable identity;
3. checks the allowed transition table;
4. relies on schema conditionals for state-specific required metadata;
5. exits non-zero on failure.

## Compatibility
This is additive to v0.1. Existing Task/Runtime schemas are unchanged. A workflow may ignore TaskExecution until it adopts execution-state tracking.

## Verification strategy
CI will cover:
- READY -> STARTED success;
- STARTED -> BLOCKED success with reason;
- BLOCKED -> STARTED success;
- STARTED -> SUBMITTED success;
- SUBMITTED -> COMPLETED success with evidence;
- illegal READY -> COMPLETED failure;
- BLOCKED without reason schema failure;
- FAILED without failure detail schema failure;
- COMPLETED without evidence schema failure;
- identity mutation failure.

## Risks
- Too many GitHub Issues for workflow bookkeeping. Mitigation: protocol artifacts stay canonical while integrations may collapse bookkeeping into comments/Project fields.
- Retry semantics can become complex. v0.1 treats a retry after terminal failure as a new TaskExecution record; first-class attempt lineage can be added later.
