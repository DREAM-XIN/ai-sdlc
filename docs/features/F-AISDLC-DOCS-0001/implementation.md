# Implementation — F-AISDLC-DOCS-0001

## Result

Implemented the approved end-user documentation information architecture on `feature/F-AISDLC-DOCS-0001`.

Feature PR: #194

## User-facing files

- `README.md` — prominent Getting Started navigation near the top.
- `docs/getting-started.md` — primary first-time-user entry and complete operating loop.
- `docs/new-project-setup.md` — target installation, Project Adapter, caller workflows, immutable pins, credentials, preflights, and public/private differences.
- `docs/feature-lifecycle-guide.md` — complete `F-DEMO-LOGIN-0001` tutorial, daily lifecycle loop, real Issue Comment command syntax, and `standard-feature` / `small-change` guidance.
- `docs/role-guide.md` — role authority boundaries and copyable single-role ChatGPT prompts.
- `docs/autonomous-development.md` — manual vs trusted gh-aw execution, bounded worker branch/Draft PR boundaries, credentials, and preflights.
- `docs/troubleshooting.md` — symptom-oriented installation/lifecycle/autonomous troubleshooting and FAQ.

## Accuracy sources used

User-facing examples were checked against the published v0.2.0 baseline `44e68d4ec6517135b0008ba4cf14fdb625f9481d` and the current repository implementation, including:

- `templates/github/ai-sdlc-installation-check.yml`
- `templates/github/ai-sdlc-bootstrap.yml`
- `templates/github/ai-sdlc-plan.yml`
- `templates/github/ai-sdlc-persist.yml`
- `templates/github/ai-sdlc-command.yml`
- `templates/project-adapter.yaml`
- `profiles/standard-feature.yaml`
- `profiles/small-change.yaml`
- `spec/feature-bootstrap.schema.json`
- `spec/feature-event.schema.json`
- `spec/feature-manifest.schema.json`
- `scripts/bootstrap_feature.py`
- `scripts/apply_feature_event.py`
- `scripts/orchestrator_state.py`
- existing Project Adapter, installation/preflight, public-target transport, autonomous execution, optimistic concurrency, and security documentation.

## Important behavior documented explicitly

- GitHub is the durable system of record.
- ordinary workers do not directly mutate the authoritative Feature Manifest.
- Gate PASS requires Evidence and independent authority.
- Feature branch and autonomous worker implementation branch are different objects.
- installed Action references use reviewed immutable full commit SHAs.
- private and public target transport are different while the control repository is private.
- the v0.2.0 public target lifecycle bridge supports Bootstrap/Plan/Persist, but the installed Action-based Installation Check caller itself cannot execute from a public target while the control repository remains private; the implemented deterministic target validator is documented for that installation-validation gap.
- manual ChatGPT Web and autonomous gh-aw preserve the same lifecycle authority boundaries.

## PR validation

PR #194 triggers the repository's normal `Validate AI-SDLC protocol` and `Required PR Gate` workflows. Their final results are Verification-stage evidence and are not treated as successful merely because the implementation exists.
