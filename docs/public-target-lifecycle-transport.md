# Public target lifecycle transport

## Problem

GitHub does not allow a public caller repository to download an Action or reusable workflow from a private repository. When `DREAM-XIN/ai-sdlc` is private, the installed lifecycle Action model therefore cannot execute `plan`, `bootstrap`, or `persist` directly from a public target repository.

This is a transport limitation, not a lifecycle-protocol exception. Public targets must still use the same Commander, Feature Event, Transition Engine, optimistic revision checks, Gate rules, persistence plan, semantic validation, and non-default-branch write protection.

## Transport split

AI-SDLC supports two lifecycle transports:

```text
private target
  -> installed caller workflow
  -> pinned private AI-SDLC Action
  -> target Feature branch

public target
  -> public-safe command bridge
  -> trusted private AI-SDLC workflow_dispatch
  -> exact-target GitHub App installation token
  -> target Feature branch
```

The public path is control-initiated. The public repository never downloads code from the private control repository.

## Trusted workflow

`.github/workflows/ai-sdlc-cross-repo-lifecycle.yml` runs in the private control repository. It:

1. binds `target_repository`, `target_ref`, lifecycle operation, and state paths from workflow inputs;
2. validates the target identity and repository-relative YAML paths;
3. verifies the trusted Runtime GitHub App configuration;
4. mints a short-lived installation token restricted to exactly one target repository;
5. uses `contents: read` for planning and non-persisting validation;
6. mints `contents: write` only for durable `bootstrap` or `persist` operations;
7. checks out the exact target ref into an explicit workspace;
8. invokes the same trusted `.github/actions/control` lifecycle implementation used by private installed callers;
9. keeps default-branch writes denied;
10. uploads the normal Commander/bootstrap/persistence artifacts and propagates the lifecycle exit code.

The shared control Action accepts `workspace_path`, which defaults to `.` for existing installed callers and is containment-checked before a trusted cross-repository workflow can target a separate checkout.

## Public command routing

The installed `templates/github/ai-sdlc-command.yml` remains read-only with respect to repository contents. It selects transport from the caller repository visibility exposed by the GitHub event:

- private target: dispatch the installed `ai-sdlc-plan.yml`, `ai-sdlc-bootstrap.yml`, or `ai-sdlc-persist.yml` workflow in the target repository;
- public target: dispatch `ai-sdlc-cross-repo-lifecycle.yml` in `DREAM-XIN/ai-sdlc` using `AI_SDLC_CONTROL_DISPATCH_TOKEN`;
- autonomous Developer handoff: continue using the trusted control-repository profile gateway.

The target repository identity is always taken from `GITHUB_REPOSITORY`; command syntax cannot select an arbitrary repository.

## Credentials

Public lifecycle transport uses the same separated trust boundaries as cross-repository autonomous execution:

- target -> control: `AI_SDLC_CONTROL_DISPATCH_TOKEN`, scoped to Actions/metadata access in the private control repository;
- control -> target: `AI_SDLC_RUNTIME_APP_CLIENT_ID` and `AI_SDLC_RUNTIME_APP_PRIVATE_KEY`, with the GitHub App installed on the exact opted-in target repository.

Do not replace either boundary with a broad classic PAT.

## Installation note for public repositories

A public target must not rely on the private installed lifecycle Actions as its executable transport. Install the public-safe command bridge and configure the control dispatch credential. Lifecycle commands then run in the private control repository and write back through the Runtime GitHub App.

Existing private targets remain compatible with the pinned installed Action model.
