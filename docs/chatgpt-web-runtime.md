# ChatGPT Web Manual Runtime

The ChatGPT Web runtime is a **manual transport runtime**. AI-SDLC does not automate or scrape the ChatGPT browser UI. The orchestrator renders a portable task package, a human opens or selects a ChatGPT conversation, and the worker writes durable results back to the system of record.

## Lifecycle

1. Orchestrator selects `runtime=chatgpt-web`.
2. Render a task package conforming to `spec/task-package.schema.json`.
3. Human opens an independent ChatGPT Web conversation and supplies the task package.
4. Worker reads the durable GitHub context referenced by the package.
5. Worker performs only the bounded role/task.
6. Worker writes required artifacts/evidence back to GitHub.
7. Orchestrator detects completion from GitHub state, never from conversation memory.
8. Gate evaluation decides PASS, REWORK, BLOCKED, or ESCALATE.

## Transport states

- `ready`: package is rendered and waiting for dispatch.
- `started`: human has dispatched the package to a conversation.
- `blocked`: worker recorded a durable blocker.
- `submitted`: expected artifacts/evidence exist and await gate evaluation.
- `completed`: relevant gate passed.
- `failed`: attempts exhausted or unrecoverable transport failure occurred.

Transport state is not equivalent to SDLC state. A worker may submit a task while the corresponding gate still fails.

## Prompt composition

A rendered prompt should contain, in order:

1. Role contract.
2. Task goal and Definition of Done.
3. Repository and context references.
4. Allowed and forbidden scope.
5. Execution instructions.
6. Write-back requirements.
7. Blocker/escalation protocol.

Do not paste large repository contents into the prompt when the worker can read the authoritative GitHub artifact directly.

## Completion rule

A ChatGPT conversation saying "done" has no protocol meaning. Completion is derived from durable facts such as:

- required artifact exists;
- linked PR exists;
- deterministic checks are passing;
- required review evidence exists;
- blocker set is empty;
- relevant gate evaluates to PASS.

## Security and governance

- Never place secrets in task packages.
- The worker must honor repository permissions and branch protections.
- High-risk changes may require human approval gates even when all automated checks pass.
- Browser automation of chatgpt.com is intentionally outside the reference implementation.
