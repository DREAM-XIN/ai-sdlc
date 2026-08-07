# GitHub Persistence Adapter

GitHub is the reference system of record for AI-SDLC, but lifecycle business rules remain vendor-neutral.

## Boundary

```text
Feature Manifest + Feature Event
            |
            v
    transition engine
            |
            v
 GitHub Persistence Plan
            |
    +-------+--------+
    |                |
    v                v
GitHub Action      future adapters
(file/branch)      GitLab/Jira/etc.
```

The persistence adapter does not decide whether a stage is complete, whether a Gate passes, or which runtime should execute next. Those decisions are made by the deterministic protocol engines before a persistence plan exists.

## Persistence Plan

`scripts/github_persistence.py` applies a Feature Event and emits a validated plan containing:

- updated Feature Manifest content and SHA-256 digest;
- repository, manifest path, and explicit target ref;
- an `update-file` mutation;
- a status `check-run` projection;
- an optional idempotency-friendly Feature Issue status comment marker.

An invalid event produces `INVALID` and no plan.

## GitHub Action

`.github/workflows/ai-sdlc-persist.yml` is an explicit `workflow_dispatch` transport for persistence. It:

1. checks out the requested ref;
2. refuses the repository default branch unless `allow_default_branch=true` is explicitly supplied;
3. builds a persistence plan;
4. materializes and validates the new manifest;
5. uploads the plan as an Actions artifact;
6. pushes only when `dry_run=false`.

The workflow intentionally does not execute issue comments or Check Run mutations yet. Those remain projections in the plan until a dedicated safe-output/write adapter is added.

## gh-aw bridge

`examples/gh-aw/ai-sdlc-gateway.md` demonstrates the intended optional bridge to GitHub Agentic Workflows.

The bridge follows two current gh-aw properties:

- agentic execution can request validated GitHub operations through `safe-outputs`, keeping the agent portion read-only;
- `dispatch-workflow` can hand work to an allowlisted workflow that declares `workflow_dispatch`.

Reference documentation:

- https://github.github.com/gh-aw/reference/safe-outputs/
- https://github.github.com/gh-aw/guides/reusing-workflows/

AI-SDLC keeps `gh-aw` optional. Manual ChatGPT Web tasks can continue to use the deterministic Runtime Router and Task Package path without starting any hosted coding agent.

## Security rules

- No LLM receives repository write credentials from the protocol layer.
- Persistence targets an explicit ref.
- Direct default-branch persistence is denied by default.
- Invalid Feature Events cannot produce a write plan.
- The materialized manifest is semantically revalidated before any push.
- Merge remains a separate repository policy/Human Gate.
