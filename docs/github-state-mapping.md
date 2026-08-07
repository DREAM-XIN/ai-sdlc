# GitHub state mapping

The first dogfood cycle showed that one GitHub Issue per gate or evidence record creates unnecessary bookkeeping. AI-SDLC therefore uses a lightweight mapping.

## Canonical mapping

- **Feature**: one GitHub Issue for intent, discussion and acceptance context.
- **Actionable work unit**: a sub-issue or linked Issue when it benefits from ownership, discussion or independent scheduling.
- **Implementation**: branch + pull request.
- **Requirement/design/review/test artifacts**: versioned repository files referenced by the Feature Manifest.
- **Gate result**: Feature Manifest entry plus the durable source that justifies it (review artifact, check run, approval record or test result).
- **CI evidence**: GitHub check/workflow URL referenced by the Feature Manifest.
- **Task execution state**: repository-backed execution record or runtime-specific durable state, indexed from the Feature Manifest.

## When to create an Issue

Create an Issue when the item is independently actionable, assignable, discussable or schedulable.

Do not create an Issue solely because a protocol object exists.

In particular, a gate, evidence item or approval record normally does **not** require its own Issue.

## Feature Manifest

The Feature Manifest is the compact lifecycle index. It does not replace source artifacts or GitHub checks. It points to them and records current normalized state.

Recommended path:

```text
docs/features/<feature-id>/feature.yaml
```

A future orchestrator may synthesize or update this manifest from GitHub events, but the protocol does not require a particular storage engine.

## System of record

GitHub remains the reference system of record, but "GitHub" means the combination of repository content, Issues, PRs, reviews and Actions/checks—not Issues alone.
