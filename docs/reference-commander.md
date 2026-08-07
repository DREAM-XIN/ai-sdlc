# Reference Commander

`scripts/commander.py` is the reference control-plane entry point for AI-SDLC. It composes the existing deterministic engines; it does not replace them and does not execute an LLM.

## Commands

### Bootstrap a Feature

```bash
python scripts/commander.py bootstrap feature-bootstrap.yaml \
  --output state/features/F-123.yaml
```

The command delegates to the Feature Bootstrap engine and produces the initial authoritative Feature Manifest.

### Plan the next action

```bash
python scripts/commander.py plan state/features/F-123.yaml \
  --repository org/repo
```

The JSON output is a validated Commander Plan with one of:

```text
DISPATCH
WAIT
BLOCKED
COMPLETE
INVALID
```

For `chatgpt-web/manual`, each dispatch includes:

- canonical Task;
- portable Task Package;
- copy-ready prompt.

To print only available ChatGPT Web prompts:

```bash
python scripts/commander.py plan state/features/F-123.yaml \
  --repository org/repo \
  --format prompts
```

For other runtimes such as a future `gh-aw/autonomous` or Agent Orchestrator adapter, Commander only returns the selected route/runtime. It deliberately does not fabricate an executable payload for a runtime whose adapter is not part of the reference implementation.

### Ingest a worker result

Workers write a Feature Event to the repository Event Inbox rather than editing the Feature Manifest directly.

```bash
python scripts/commander.py ingest \
  state/features/F-123.yaml \
  state/events/F-123/EVT-F123-REQ-DONE.yaml \
  --event-path state/events/F-123/EVT-F123-REQ-DONE.yaml \
  --repository org/repo \
  --manifest-path state/features/F-123.yaml \
  --target-ref feature/F-123 \
  --issue 123
```

The result is the same deterministic GitHub Persistence Plan produced by the lower-level Event Inbox/Persistence adapter.

## Embedded API

The reference CLI also exposes reusable Python functions:

```python
commander_bootstrap(...)
build_commander_plan(...)
commander_ingest(...)
```

A future Web console, GitHub integration, or central orchestrator can import these functions instead of spawning CLI subprocesses.

## Control-plane boundary

```text
                    Reference Commander
                            |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
 Feature Bootstrap   Orchestrator + Router   Event Inbox
        |                   |                   |
        v                   v                   v
 Feature Manifest    Commander Plan       Persistence Plan
                            |
             +--------------+--------------+
             |                             |
             v                             v
      ChatGPT Web/manual           future runtime adapter
      Task/Package/Prompt          gh-aw / AO / others
```

Commander never treats a model response as authoritative state. Feature Manifest, Feature Events, Gate Evidence, and deterministic validation remain the source of truth.
