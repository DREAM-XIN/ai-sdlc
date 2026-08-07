# Deterministic orchestrator state engine

The state engine is the first Commander/Orchestrator core. It does not call an LLM and does not mutate GitHub.

## Inputs

1. A validated Feature Manifest.
2. The Workflow Profile named by `manifest.workflow.profile`.

## Outputs

Machine-readable JSON with one of five outcomes:

- `DISPATCH` — one or more stages are ready to execute.
- `WAIT` — an eligible stage is already WORKING or REVIEW.
- `BLOCKED` — workflow or stage is explicitly blocked.
- `COMPLETE` — workflow is DONE or CANCELLED.
- `INVALID` — manifest/profile state is inconsistent.

A dispatch action contains:

```json
{
  "stage": "design",
  "role": "architect",
  "gate": null,
  "parallel": false
}
```

## Dependency rule

A stage is eligible only when every `depends_on` stage in the Workflow Profile is `DONE` or `SKIPPED` in the Feature Manifest.

The engine may return multiple `DISPATCH` actions when a DAG contains multiple dependency-satisfied stages.

## Boundary

The engine decides **what is runnable**, not **how it is executed**.

A later Runtime Router can map the returned role/stage to:

- ChatGPT Web task-package rendering;
- human execution;
- `gh-aw` agentic workflow;
- coding-agent orchestrator runtime.

This separation keeps workflow correctness deterministic and runtime selection replaceable.

## CLI

```bash
python scripts/orchestrator_state.py docs/features/F-123/feature.yaml
```

The CLI exits with code `2` for `INVALID`; other outcomes are emitted as JSON on stdout.
