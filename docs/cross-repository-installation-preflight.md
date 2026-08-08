# Cross-repository installation preflight

Run the target installation preflight on the AI-SDLC installation pull request and after any change to the Project Adapter or installed AI-SDLC caller workflows. The preflight is intentionally available before any Feature Manifest exists.

## Install the caller

Copy `templates/github/ai-sdlc-installation-check.yml` to `.github/workflows/ai-sdlc-installation-check.yml` in the target repository. Replace every `REPLACE_WITH_AI_SDLC_FULL_SHA` marker in all installed AI-SDLC workflows with the same reviewed 40-character control-plane commit SHA.

The installation check uses the shared control action with:

```yaml
with:
  operation: validate-installation
  repository: ${{ github.repository }}
  default_branch: ${{ github.event.repository.default_branch }}
  project_path: .ai-sdlc/project.yaml
```

It requires only `contents: read`. It does not create a Feature, mutate lifecycle state, push a branch, dispatch an autonomous worker, or require AI-provider credentials.

## What is validated

The preflight fails closed when any of the following are invalid:

1. `.ai-sdlc/project.yaml` schema or semantic rules, including supported command purposes and ownership/path constraints.
2. `repository.full_name` and `repository.default_branch` when the caller supplies live repository metadata.
3. Every path listed in `context.rules` and `context.read`; each is treated as required installed context and must resolve to a regular non-symlink file inside the target workspace.
4. Repository-wide `AGENTS.md`; the installed cross-repository worker/Commander contract requires it and the Project Adapter must list it under `context.rules`.
5. Each configured command `cwd`; it must resolve to a real non-symlink directory inside the target workspace.
6. AI-SDLC caller pins under `.github/workflows/ai-sdlc-*.yml` / `.yaml`; unresolved installation placeholders and moving refs such as `main`, release branches, or tags are rejected. `DREAM-XIN/ai-sdlc` references must use a full 40-character commit SHA.

`context.read` is not optional in the current Project Adapter protocol. If a document is genuinely optional, do not list it there until an explicit optional-context representation is added to the schema.

## Local/reference invocation

When working inside a checkout of the trusted control repository, the same validator can be exercised directly against a target workspace:

```bash
python scripts/validate_target_installation.py \
  --workspace /path/to/target \
  --project .ai-sdlc/project.yaml \
  --repository owner/repo \
  --default-branch main
```

A successful result reports `"outcome": "READY"`. Any validation error exits non-zero and reports an actionable `installation:` or `project-adapter:` diagnostic.

## Recommended order

1. Add `AGENTS.md` and `.ai-sdlc/project.yaml`.
2. Add the installation-check and lifecycle caller workflows.
3. Replace all AI-SDLC placeholders with a reviewed immutable control-plane SHA.
4. Run **AI-SDLC Installation Check** and require it to pass on the installation PR.
5. Only then create a non-default Feature branch and Bootstrap the first Feature.
6. For autonomous execution, separately run the existing cross-repository Runtime GitHub App preflight before the first `dispatch-gh-aw`.

The installation preflight validates the durable target contract. It intentionally does not repair or certify the target application's own build/lint/test baseline; those remain normal project Features and verification evidence.
