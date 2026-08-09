# Requirement — AI-SDLC end-user documentation

Feature: `F-AISDLC-DOCS-0001`

Issue: `#193`

Profile: `standard-feature`

## User problem

AI-SDLC v0.2.0 has detailed architecture, protocol, security, Project Adapter, cross-repository installation, autonomous execution, and release documentation, but a first-time user still has to assemble the operating sequence from implementation-oriented documents. The repository README explains what AI-SDLC is more clearly than how to start using it.

## Goal

Provide one obvious end-user path from README to a complete first Feature so a user can install AI-SDLC in a GitHub repository, run a standard lifecycle, choose the correct role at each stage, use manual or autonomous execution safely, and troubleshoot common failures without first learning control-plane internals.

## Required user journeys

1. **New user / quick start** — understand control plane vs target repository, install the minimum target contract, pin the control plane to a reviewed full commit SHA, run Installation Check, and know what to do next.
2. **First Feature** — create an Issue, non-default Feature branch, Bootstrap input and Manifest; Plan; execute Requirement through Acceptance; persist Feature Events; re-plan; reach `DONE`; merge the Feature branch.
3. **Daily Feature operation** — understand `standard-feature` vs `small-change`, current stage, Task Package, Artifact, Evidence, Feature Event, Persist, Gate, revision, and branch boundaries.
4. **Role operation** — use one independent worker context per Product, Requirement Reviewer, Architect, Design Reviewer, Orchestrator, Developer, Code Reviewer, QA, and Acceptance Product Owner role.
5. **Autonomous development** — understand the trusted gh-aw flow, bounded implementation branch and Draft PR, allowed Developer actions, forbidden lifecycle-authority actions, credentials, and preflights for private targets.
6. **Troubleshooting** — diagnose installation, identity, pinning, revision, Persist, stage/Gate, autonomous dispatch, Runtime App, control token, provider/engine preflight, Draft PR/state divergence, and independent review/verification issues.

## Deliverables

- `docs/getting-started.md`
- `docs/new-project-setup.md`
- `docs/feature-lifecycle-guide.md`
- `docs/role-guide.md`
- `docs/autonomous-development.md`
- `docs/troubleshooting.md`
- a prominent Getting Started navigation section in `README.md`

File names may change only if the final navigation remains equally clear and avoids duplication with existing documentation.

## Accuracy requirements

All executable examples must be derived from current v0.2.0 repository implementation, especially:

- `templates/github/ai-sdlc-installation-check.yml`
- `templates/github/ai-sdlc-bootstrap.yml`
- `templates/github/ai-sdlc-plan.yml`
- `templates/github/ai-sdlc-persist.yml`
- `templates/github/ai-sdlc-command.yml`
- `templates/project-adapter.yaml`
- `profiles/standard-feature.yaml`
- `profiles/small-change.yaml`
- `docs/cross-repository-installation.md`
- `docs/cross-repository-installation-preflight.md`
- `docs/public-target-lifecycle-transport.md`
- `docs/cross-repository-autonomous-execution.md`
- `docs/security-model.md`
- `docs/optimistic-concurrency.md`

Do not document features that are not implemented.

## Required concepts

The user documentation must clearly state that:

- GitHub is the durable system of record.
- `state/features/<feature-id>.yaml` is authoritative lifecycle state.
- workers submit Artifacts, Evidence, and Feature Events; ordinary workers do not directly edit the authoritative Manifest.
- Persist validates and applies events using optimistic `expected_revision` checks.
- a worker cannot approve its own work, PASS its own Gate, skip independent Verification, merge, or release merely because it completed implementation.
- the Feature branch is the durable Feature integration branch and is not the same as a bounded autonomous worker implementation branch.
- target repositories keep project-specific configuration, durable state, caller workflows, and source; they do not copy the full trusted control plane.
- installed AI-SDLC Action references must use a reviewed immutable 40-character commit SHA, not `main`, a release branch, or a moving tag.
- public and private target lifecycle transport differ when the trusted control repository is private.
- manual ChatGPT Web and autonomous gh-aw are different execution modes that preserve the same lifecycle authority rules.

## First Feature tutorial

Use `F-DEMO-LOGIN-0001` / `Add user login` as the primary example. Show the complete standard-feature sequence exactly as defined in `profiles/standard-feature.yaml`:

`requirement → requirement-review → design → design-review → plan → implementation → code-review → verification → acceptance`

The tutorial must include the real Issue Comment command forms supported by `templates/github/ai-sdlc-command.yml` for bootstrap, plan, persist, and gh-aw dispatch.

## Profile guidance

Explain `standard-feature` and `small-change` without inventing stages. `standard-feature` must show the nine stages above. `small-change` must show the actual four stages:

`requirement → implementation → review → verification`

## Acceptance criteria

A person unfamiliar with AI-SDLC can open README, follow the end-user path, install a new repository correctly, create and run a first Feature, understand authority boundaries and execution modes, and identify the appropriate troubleshooting path without reading all internal architecture documents first.
