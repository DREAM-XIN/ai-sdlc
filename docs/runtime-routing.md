# Runtime routing and manual dispatch

The orchestrator state engine decides **what is runnable**. The runtime router decides **where a runnable action should execute**. The manual dispatcher turns a routed `chatgpt-web/manual` action into a portable Task Package and copy-ready prompt.

## Boundary

```text
Feature Manifest + Workflow Profile
              |
              v
     Orchestrator State Engine
              |
       DISPATCH action(s)
              |
              v
         Runtime Router
              |
       runtime + mode
              |
        +-----+----------------+
        |                      |
        v                      v
chatgpt-web/manual       future runtimes
        |
        v
    Task Package
        |
        v
 copy-ready prompt
```

No component in this flow calls an LLM or controls a browser.

## Dispatch policy

`dispatch/default.yaml` is configuration, not model state. It contains:

- ordered-priority runtime routes based on `stage`, `role`, and/or `risk`;
- portable task templates keyed by workflow stage;
- goals, required reads, scope, expected outputs, and Definition of Done.

The router selects all matching rules at the highest priority. If those rules disagree on runtime or mode, routing is `INVALID`; the system never guesses.

The v0.1 default routes all core roles to `chatgpt-web/manual`. Future policies may route selected implementation or verification stages to `gh-aw`, Codex, Claude, Agent Orchestrator, or human execution without changing the orchestrator state engine.

## Manual dispatch

Example:

```bash
python scripts/manual_dispatch.py path/to/feature-manifest.yaml \
  --repository org/repo \
  --manifest-ref path/to/feature-manifest.yaml \
  --format prompts
```

For every concurrently runnable stage, the dispatcher:

1. selects a runtime deterministically;
2. loads the stage task template;
3. builds a canonical `Task`;
4. validates the Task schema;
5. renders a portable Task Package;
6. validates the Task Package schema;
7. renders a copy-ready prompt for ChatGPT Web.

Parallel runnable stages produce independent task IDs and independent packages, so they may be executed in separate conversations.

## Authority and completion

The generated prompt is transport only. Chat history is not durable project state. Workers must read the referenced GitHub/repository context and write durable outputs back to the system of record. Completion remains evidence- and Gate-driven.

## Extension rule

A new runtime should normally be introduced as a new adapter/dispatcher behind the same routing result. Do not add runtime-specific logic to `orchestrator_state.py`.
