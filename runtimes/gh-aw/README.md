# gh-aw Runtime Adapter

This directory defines the AI-SDLC reference adapter for GitHub Agentic Workflows (`github/gh-aw`).

The adapter treats gh-aw as an **execution runtime**, not as the AI-SDLC workflow/state engine.

```text
AI-SDLC Commander
      ↓
Runtime Router → gh-aw/autonomous
      ↓
gh-aw Dispatch Plan
      ↓
reserve Feature stage: READY → WORKING
      ↓
compiled gh-aw worker on trusted default branch
      ↓
structured Worker Result
      ↓
AI-SDLC Feature Event
      ↓
Transition / Persistence
      ↓
next independent stage / Gate
```

## Upstream dependency

Use the official GitHub Agentic Workflows project and documentation:

- `https://github.com/github/gh-aw`
- `https://github.github.com/gh-aw/`
- `https://github.github.com/gh-aw/reference/safe-outputs/`

AI-SDLC does not fork or reimplement the gh-aw compiler, safe-output handlers, sandboxing or coding-agent engines.

## Trusted worker workflow

The compiled gh-aw worker workflow must be installed in the repository's **trusted default branch**.

Default filename expected by this adapter:

```text
.github/workflows/ai-sdlc-gh-aw-worker.lock.yml
```

The dispatch transport invokes that trusted workflow definition and passes the Feature branch as a separate `target_ref` input.

Do not dispatch the worker definition from an untrusted Feature branch. Otherwise that branch could replace the runtime harness/permissions that are supposed to validate and execute the task.

## Required worker inputs

The compiled worker workflow must expose `workflow_dispatch` inputs compatible with:

```text
feature_id
expected_revision
target_ref
stage
role
task_payload
```

`task_payload` is compact JSON using contract `ai-sdlc-task-v0.1`. It includes the canonical AI-SDLC Task and, when present, Project Adapter context.

The worker must treat these values as task context, not authority to rewrite the Feature Manifest.

## State reservation before execution

The v0.1 adapter serializes autonomous gh-aw execution to one worker at a time.

Before the worker is triggered, AI-SDLC persists a START event:

```text
stage READY → WORKING
revision N → N+1
```

The worker receives:

```text
expected_revision = N+1
```

This prevents two automated workers from both claiming the same global Feature-state revision while parallel autonomous result merge semantics are still undefined.

The single-worker restriction applies only to this reference adapter. It does not prohibit humans/manual workers from doing engineering work in parallel; it limits automatic authoritative result ingestion.

## Worker result contract

The worker returns a `gh-aw-worker-result-v0.1` object validated by `worker-result.schema.json`.

Example:

```json
{
  "version": "0.1.0",
  "id": "GHAW-F123-IMPLEMENTATION-42",
  "feature_id": "F-123",
  "task_id": "F-123-IMPLEMENTATION",
  "stage": "implementation",
  "expected_revision": 8,
  "status": "COMPLETED",
  "occurred_at": "2026-08-07T14:00:00Z",
  "artifacts": [
    {
      "id": "ART-PR-42",
      "type": "pull-request",
      "uri": "https://github.com/org/repo/pull/42"
    }
  ],
  "evidence": [
    {
      "id": "EVID-CI-42",
      "type": "ci",
      "status": "pass",
      "uri": "https://github.com/org/repo/actions/runs/123"
    }
  ]
}
```

`BLOCKED` and `FAILED` results must include `reason`.

## Result semantics

The adapter converts results to proposed AI-SDLC Feature Events:

```text
COMPLETED → assigned stage DONE
BLOCKED   → assigned stage BLOCKED
FAILED    → assigned stage BLOCKED
```

A `COMPLETED` worker result may close only its assigned **work stage**. It does **not**:

- PASS a Gate;
- approve an independent review stage;
- accept/release/merge the Feature;
- rewrite the Feature Manifest directly.

For example, when the `requirement` stage becomes DONE, Commander may then dispatch the separate `requirement-review` stage. Review independence therefore remains intact.

## Safe-output boundary

A gh-aw worker should use gh-aw's official safe-output mechanism for repository mutations and handoff rather than giving the model unrestricted repository write credentials.

For AI-SDLC lifecycle handoff, the worker/result gateway should deliver the structured Worker Result to:

```text
.github/workflows/ai-sdlc-gh-aw-result.yml
```

The result gateway converts the result into a Feature Event and runs the existing Event Inbox, revision and Git write-precondition validators. The worker itself never updates `state/features/*.yaml`.

Exact gh-aw source/frontmatter syntax is intentionally not duplicated here; compile the worker using the currently installed gh-aw version and its official safe-output documentation. The AI-SDLC contract starts at the compiled workflow's inputs and structured result.

## Dispatch failure semantics

Actual execution performs state reservation before `workflow_dispatch`.

If the GitHub workflow dispatch fails after the START event is durably persisted:

- the Feature remains `WORKING`;
- the dispatch job fails visibly;
- AI-SDLC does not guess a rollback or BLOCKED transition;
- an operator may retry the trusted worker explicitly, or submit a reviewed recovery event such as `WORKING → READY` before replanning.

This makes transport failure separate from engineering-result failure.

## GitHub permissions

The reference dispatch workflow separates permissions:

```text
plan job     contents: read
execute job  contents: write, actions: write
```

`contents: write` is needed only to persist the READY → WORKING reservation. `actions: write` is needed only to invoke the compiled worker workflow.

The result gateway never needs `actions: write`.

Autonomous gh-aw dispatch/result persistence is restricted to non-default Feature branches in v0.1.

## Security requirements

Before enabling a gh-aw worker, review `docs/security-model.md`, especially the Runtime Adapter checklist.

Required properties include:

- trusted worker definition on default branch;
- target branch treated as data/workspace;
- immutable third-party Action/runtime dependencies;
- no raw Feature Manifest write authority for the worker;
- revision-aware result handoff;
- Git remote-branch write precondition;
- safe-output/tool capability controls;
- bounded scope and explicit retry limits;
- independent review/Gate policy after worker completion.

## Current maturity

The AI-SDLC adapter/transport is a reference implementation boundary. A specific gh-aw worker must still be authored/compiled and dogfooded against this contract before Milestone #3 can be declared fully complete.
