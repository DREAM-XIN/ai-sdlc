# ChatGPT Web Manual Runtime

The ChatGPT Web runtime models a web conversation as a **manual transport runtime**, not as an API-controlled agent.

## Why

A workflow should not depend on browser automation or private UI behavior. Instead, the orchestrator creates a portable task package that a human can hand to a ChatGPT Web conversation. The conversation reads durable GitHub context and writes results back through supported GitHub capabilities.

## Contract

Runtime configuration:

```yaml
id: chatgpt-web
mode: manual
provider: openai-chatgpt-web
requires_human_transport: true
supports_repository_write: true
supports_pr: true
```

## Dispatch lifecycle

1. Orchestrator emits a task conforming to `spec/task.schema.json`.
2. A task package is rendered for the target role.
3. Human opens or selects a ChatGPT Web conversation and supplies the package.
4. Worker reads referenced repository artifacts and performs the scoped task.
5. Worker writes durable outputs to GitHub.
6. Orchestrator observes expected artifacts/evidence and evaluates the next gate.

## Completion rule

Conversation text is not completion evidence. Completion requires the expected durable artifact and any required evidence referenced by the task.

## Non-goals

- Browser automation of chatgpt.com.
- Sharing hidden conversation state between workers.
- Treating a ChatGPT tab as the workflow database.
