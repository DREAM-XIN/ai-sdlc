# Project Adapter

The Project Adapter is the small target-repository contract that lets the same AI-SDLC control plane operate across different stacks without project-specific code in the Orchestrator.

Canonical location:

```text
.ai-sdlc/project.yaml
```

The adapter is **data, not executable shell**. Verification commands are represented as argv arrays so a future execution runtime can apply its own sandboxing and process policy.

## Minimal target repository layout

```text
my-project/
├── .ai-sdlc/
│   └── project.yaml
├── state/
│   ├── features/
│   │   └── F-123.yaml
│   └── events/
│       └── F-123/
│           └── EVT-F123-REQ-DONE.yaml
├── AGENTS.md
├── README.md
└── <project source>
```

Only `.ai-sdlc/project.yaml` is project-specific configuration required by the adapter contract. The control-plane implementation, schemas, default Dispatch Policy, Commander and validators remain in the trusted AI-SDLC runtime.

## Example

```yaml
version: 0.1.0
project:
  id: payments-service
  name: Payments Service
repository:
  provider: github
  full_name: acme/payments-service
  default_branch: main
defaults:
  workflow_profile: standard-feature
  runtime_policy: default
  required_commands: [test, lint]
context:
  rules:
    - AGENTS.md
    - CONTRIBUTING.md
  read:
    - README.md
    - docs/architecture.md
commands:
  - id: test
    purpose: test
    argv: [python, -m, pytest]
    cwd: .
  - id: lint
    purpose: lint
    argv: [python, -m, ruff, check, .]
    cwd: .
ownership:
  - id: service
    role: developer
    roots: [src, tests]
```

## Fields

### `project`

Stable project identity and human-readable metadata. `project.id` is a portable identifier and should not change when the repository is renamed.

### `repository`

Optional system-of-record metadata. The Reference Commander can derive its repository name from `repository.full_name` when `--repository` is not supplied. GitHub names use `owner/name` form.

### `defaults`

- `workflow_profile`: the normal Feature workflow profile for the project.
- `runtime_policy`: symbolic runtime-routing policy identifier. `default` means the trusted AI-SDLC default Dispatch Policy.
- `required_commands`: command ids that form the project's normal deterministic verification baseline.

A Feature Manifest still owns the actual workflow profile for an active Feature. The Project Adapter default does not override Feature state.

### `context`

Repository-relative durable context that workers should read:

- `rules`: normative repository rules such as `AGENTS.md` or `CONTRIBUTING.md`;
- `read`: architecture, domain or project documents useful for work.

When the adapter is supplied to Commander, these paths are added to ChatGPT Web Task Packages.

### `commands`

Each command contains:

- a stable `id`;
- a `purpose` (`build`, `test`, `lint`, `typecheck`, `format`, `security`, `package`, or `custom`);
- `argv`, an argument vector rather than a shell string;
- repository-relative `cwd`.

The adapter does not execute these commands. An execution runtime must apply its own sandbox, environment and secret policy before running them.

### `ownership`

Ownership boundaries are repository-relative path roots. Overlapping roots owned by different entries are rejected unless at least one boundary is explicitly marked `shared: true`.

This prevents the adapter from silently assigning the same files to multiple parallel writers.

## Semantic validation

`project_adapter.py` performs checks that are awkward or unsafe to express only in JSON Schema:

- duplicate command and ownership ids;
- references to missing required commands;
- absolute paths, parent traversal and non-POSIX separators;
- malformed GitHub `owner/name` identifiers;
- ambiguous ownership overlaps;
- control characters in argv values.

Example:

```bash
python scripts/project_adapter.py .ai-sdlc/project.yaml
```

## Commander integration

Local/reference use:

```bash
python scripts/commander.py plan state/features/F-123.yaml \
  --project .ai-sdlc/project.yaml
```

If `repository.full_name` is present, `--repository` is optional.

The generated manual Task Package will include:

- `.ai-sdlc/project.yaml` in required reads;
- adapter `context.read` documents;
- adapter `context.rules` as project rules;
- an instruction to respect ownership boundaries;
- the ids of required deterministic verification commands.

The adapter does not alter the Feature state machine or Gate semantics.

## GitHub-native behavior

`AI-SDLC Commander` automatically loads `workspace/.ai-sdlc/project.yaml` when present. The normal Dispatch Policy comes from the trusted AI-SDLC runtime. A target-repository policy override is optional and must be explicitly supplied.

This separation is intentional:

```text
trusted AI-SDLC runtime
  ├── Commander / validators
  └── default Dispatch Policy

untrusted/target repository workspace
  ├── .ai-sdlc/project.yaml
  ├── optional policy override
  ├── source code
  └── state/
```

A target repository must never be able to replace trusted control-plane code merely by changing its Feature branch.

## Examples

- `examples/project-adapters/generic.yaml`
- `examples/project-adapters/java-spring-vue.yaml`
- `templates/project-adapter.yaml`

## Future extensions

The v0.1 adapter intentionally does not define secrets, arbitrary environment variables, container images or shell hooks. Those capabilities belong to restricted Runtime Adapter policies, not portable project metadata.
