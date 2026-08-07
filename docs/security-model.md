# AI-SDLC security model

AI-SDLC coordinates humans, model-driven workers and repository automation. Its security model assumes that target-repository content and agent output can be wrong or malicious, while the pinned AI-SDLC control implementation and explicitly approved GitHub workflow configuration form the trusted computing base.

The central rule is:

> Untrusted workers may propose artifacts, evidence and Feature Events, but they must not receive privileged repository credentials or replace the code that validates and persists authoritative state.

## Security goals

AI-SDLC aims to preserve:

- **control-plane integrity** — lifecycle, Gate, routing and persistence rules execute from reviewed AI-SDLC code;
- **repository integrity** — writes are limited to explicit scopes and fresh branches;
- **Feature-state integrity** — Manifest transitions are schema/semantically validated, replay-safe and revision-safe;
- **least privilege** — read-only planning and write-capable persistence use separate permission envelopes;
- **execution isolation** — target repository code is not executed merely because a privileged control workflow is running;
- **supply-chain reproducibility** — external GitHub Actions are pinned to immutable commit SHAs;
- **auditability** — decisions, evidence, revisions and persistence preconditions are durable facts;
- **human accountability** — high-impact approval/merge/release decisions may remain explicit human Gates.

## Assets

Important assets include:

1. repository source code and history;
2. Feature Manifests and Event Inbox records;
3. requirements, designs, reviews and Evidence;
4. branches, pull requests and protected default branches;
5. `GITHUB_TOKEN`, installation tokens and future runtime credentials;
6. the pinned AI-SDLC Action/control-plane source;
7. Project Adapter and Dispatch Policy configuration;
8. generated Task Packages and prompts;
9. workflow logs and uploaded artifacts;
10. future autonomous runtime workspaces, sessions and sandboxes.

## Trust boundaries

### 1. Trusted AI-SDLC control plane

The trusted boundary contains a reviewed, immutable AI-SDLC revision:

```text
AI-SDLC @ full commit SHA
├── schemas
├── semantic validators
├── Commander / Orchestrator
├── Runtime Router
├── Transition Engine
├── Persistence logic
└── default Dispatch Policy
```

GitHub-native privileged workflows additionally load control code from the repository default branch into a dedicated `runtime/` checkout rather than executing the selected target branch.

### 2. Target repository workspace

Target repository content is data from the control plane's perspective, even when it is owned by the same organization.

Potentially untrusted content includes:

- source code;
- Feature branches and pull requests;
- `.ai-sdlc/project.yaml`;
- repository-local optional Dispatch Policy overrides;
- Markdown/documents that enter worker context;
- Feature Events and Evidence references.

A target branch must not be able to replace `scripts/`, schemas or dependencies that execute under a write-capable token.

### 3. Agent / model worker

ChatGPT Web, coding agents and future autonomous runtimes are workers, not authorities.

Workers may:

- read approved context;
- produce code/artifacts;
- produce proposed Evidence;
- emit Feature Events through the defined protocol.

Workers must not be trusted merely because they report success. Gate verdicts and deterministic CI Evidence remain separate control inputs.

### 4. GitHub automation boundary

GitHub Actions provides runner isolation and `GITHUB_TOKEN` permission scoping. AI-SDLC does not assume that a called action can elevate its caller's token: the caller workflow defines the permission envelope.

Read-only Plan workflows and write-capable Bootstrap/Persist workflows therefore remain separate.

### 5. Human approval boundary

Risk policy may require humans for requirement/design/security/merge/release Gates. Human approval is not replaced by a worker declaring its own work acceptable.

## Threat actors and failure sources

The model considers:

- a malicious contributor controlling a Feature branch or pull request;
- prompt-injected or compromised repository documentation;
- a compromised or misbehaving AI worker;
- an honest worker operating on stale state;
- duplicated/retried workflow runs;
- a compromised third-party GitHub Action release/tag;
- a maintainer accidentally broadening workflow permissions;
- malicious path/symlink input intended to escape allowed state directories;
- accidental or malicious leakage through logs/artifacts;
- a future Runtime Adapter that exposes excessive host/network/secret capability.

## Threats and mitigations

### Privileged workflow executes target-branch control code

**Threat:** a Feature branch modifies AI-SDLC scripts/dependencies and a write-capable workflow checks out that branch and executes them with `contents: write`.

**Mitigations:**

- native workflows use separate `runtime/` and `workspace/` checkouts;
- trusted runtime comes from the default branch;
- target branches are workspace data only;
- shared cross-repository automation executes scripts/dependencies from `${{ github.action_path }}`;
- CI rejects regression to checkout-root `python scripts/...` execution in write transports.

### Mutable GitHub Action supply chain

**Threat:** `uses: owner/action@vN`, a tag or `@main` moves after review and different code executes without a repository change.

**Mitigations:**

- active external Actions are pinned to full 40-character commit SHAs;
- reviewed release version is retained as an inline comment;
- caller templates use an explicit `REPLACE_WITH_AI_SDLC_FULL_SHA` installation placeholder;
- CI rejects mutable external Action refs in active workflows/actions/templates.

Reviewed official dependency pins for this baseline:

```text
actions/checkout v7.0.0
9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0

actions/setup-python v7.0.0
5fda3b95a4ea91299a34e894583c3862153e4b97

actions/upload-artifact v7.0.1
043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
```

Changing an external Action SHA is a dependency update and should receive normal review/CI rather than automatic mutable-tag adoption.

### Over-privileged workflow token

**Threat:** a planning or validation workflow unnecessarily obtains write capability.

**Mitigations:**

- Plan/validation paths use `contents: read`;
- Bootstrap/Persist request explicit `contents: write` only when their operation requires a possible write;
- caller workflows own the token permission envelope;
- `permissions: write-all` is forbidden by CI;
- `secrets: inherit` is forbidden by the v0.1 workflow policy.

### Dangerous privileged trigger

**Threat:** a workflow triggered by attacker-controlled PR content runs with a privileged token, for example through careless `pull_request_target`/`workflow_run` use.

**Mitigations:**

- v0.1 active control workflows forbid `pull_request_target` and `workflow_run`;
- any normal `pull_request` workflow containing `contents: write` is rejected by CI;
- write transports are explicit operator-triggered workflows or caller-controlled operations.

### Path traversal or symlink escape

**Threat:** a path input targets `.github/workflows`, secrets, or a path outside the checkout instead of AI-SDLC state.

**Mitigations:**

- Manifest writes are restricted to `state/features/*.yaml|yml`;
- Event Inbox paths follow `state/events/<feature>/<event>.yaml`;
- repository-relative path checks reject parent traversal and absolute paths;
- cross-repository Action validates resolved paths remain under `GITHUB_WORKSPACE`;
- required input files must be regular files and not symlinks;
- Project Adapter paths use POSIX repository-relative semantics.

### Feature Event replay / stale worker

**Threat:** a duplicated event is applied twice or a worker submits a result produced from an outdated Feature state.

**Mitigations:**

- stable event IDs and `applied_events` provide replay protection;
- Manifest revision is monotonic;
- repository Inbox requires `expected_revision`;
- stale revision produces `INVALID` without state mutation;
- prompts tell workers which revision they must reference.

### Repository branch races

**Threat:** state is validated from one branch SHA, then another workflow advances the remote branch before push.

**Mitigations:**

- write transports compare local checkout `HEAD` to the live remote target branch immediately before persistence;
- mismatch fails closed as a stale workspace;
- normal Git non-fast-forward rejection remains a final safeguard.

### Agent self-attestation / fabricated success

**Threat:** a worker says tests passed or declares its own code reviewed.

**Mitigations:**

- Worker output is not authoritative workflow state;
- Gates require durable Evidence;
- deterministic CI/checks are independent Evidence sources;
- high-risk work uses independent review contexts/models/humans;
- reviewer and implementation roles are logically separate.

### Prompt injection from repository content

**Threat:** repository documentation tells a model to ignore its task, expose credentials or make unauthorized changes.

**Mitigations:**

- Project Adapter explicitly defines normative project rules and durable reads;
- Task Package defines role, goal, allowed/forbidden scope and Definition of Done;
- manual ChatGPT Web workers do not receive GitHub write credentials from AI-SDLC;
- privileged writes are revalidated by deterministic control-plane code;
- autonomous runtimes must enforce sandbox/tool/secret policy independently of model text.

Residual risk remains: a model may still produce harmful source-code changes within its allowed scope. Code review, tests, branch protection and human merge Gates remain necessary.

### Log / artifact disclosure

**Threat:** private repository data or secrets are printed into workflow logs or uploaded artifacts visible to broader collaborators.

**Mitigations:**

- do not put credentials in Feature Events, Task Packages or Project Adapter files;
- do not intentionally echo secret values;
- private Action sharing is treated as code distribution, not a secret channel;
- artifacts should contain plans/prompts/evidence needed for review, not environment dumps;
- future adapters must classify sensitive outputs before publishing them.

### Private Action sharing

**Threat:** enabling private Action access widens who can indirectly observe Action behavior/log output.

**Mitigations:**

- sharing is limited to repositories owned by the same approved owner in the current model;
- caller token remains scoped to caller repository;
- production callers pin an immutable AI-SDLC commit;
- sensitive control-plane secrets must never be logged.

## GitHub dependency policy

For active `.github/workflows/**`, `.github/actions/**` and production caller templates:

1. local actions may use `./...`;
2. all external actions must use a full lowercase 40-character Git commit SHA;
3. caller templates may contain only the specifically marked `REPLACE_WITH_AI_SDLC_FULL_SHA` self-install placeholder;
4. mutable tags/branches such as `@main`, `@master`, `@v4`, `@latest` are invalid;
5. version comments are documentation only and do not replace the immutable SHA.

`python scripts/validate_action_security.py` enforces this policy in CI.

## Runtime Adapter security checklist

Every new autonomous/assisted Runtime Adapter (including future gh-aw or Agent Orchestrator adapters) must answer the following before activation.

### Identity and scope

- What durable AI-SDLC Task/Feature IDs identify the execution?
- Which repositories/branches/worktrees may it access?
- What file/path scope may it modify?
- Can one execution affect another worker's workspace?

### Credentials

- Which credentials/tokens are available?
- Are credentials scoped to the minimum repository/action?
- Can the model read raw secrets, or only invoke capability-limited tools?
- Can credentials be exfiltrated through network, logs, artifacts or PR content?

### Execution isolation

- Is code executed in a separate worktree/container/sandbox?
- Is the filesystem writable outside assigned scope?
- What network destinations are allowed?
- Are commands taken from trusted configuration or model-generated shell text?
- How are resource/time limits enforced?

### Source and dependency trust

- What adapter/runtime version is pinned?
- Are third-party Actions/images/packages immutable or integrity-verified?
- Can a target branch replace the runtime harness?
- Is dependency update review independent of normal Feature work?

### Output and persistence

- Does the runtime return Artifact/Evidence/Feature Event rather than directly rewriting authoritative Manifest state?
- Is every persistence request revalidated by the deterministic transition/persistence layer?
- Are revision and Git remote-write preconditions preserved?
- Does the adapter distinguish worker assertion from independent Evidence?

### Feedback loops

- Can PR comments/CI failures contain untrusted instructions that the agent will execute?
- Are feedback sources authenticated and scoped to the active Task/PR?
- Is there a retry/iteration limit?
- Can a malicious comment cause scope expansion or secret access?

### Human gates and blast radius

- Which operations require human approval?
- Can the runtime merge, deploy, change permissions or modify workflow files?
- What is the maximum repository/production blast radius if the model misbehaves?
- Is rollback/recovery deterministic and auditable?

A Runtime Adapter that cannot answer these questions should remain routing-only rather than executable.

## Residual risks

AI-SDLC does not eliminate:

- vulnerabilities in GitHub-hosted runners or GitHub itself;
- vulnerabilities inside a pinned third-party Action SHA;
- malicious source-code changes that pass insufficient tests/review;
- compromised human maintainer accounts;
- leakage caused by intentionally publishing sensitive data into repository artifacts/logs;
- weaknesses in future external runtime infrastructure.

The model reduces avoidable orchestration and automation risk; repository security, branch protection, code review, secret hygiene and production controls remain required.

## Security change process

A change touching any of the following should receive explicit security review:

- workflow triggers or token permissions;
- external Action SHA/version;
- trusted-runtime/workspace boundary;
- file/path validation;
- Feature transition/concurrency semantics;
- secret/environment handling;
- autonomous Runtime Adapter capabilities;
- default-branch/merge/release automation.
