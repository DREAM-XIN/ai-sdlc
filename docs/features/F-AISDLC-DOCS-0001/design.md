# Design — AI-SDLC end-user documentation

Feature: `F-AISDLC-DOCS-0001`

## Design objective

Create a task-oriented documentation layer on top of the existing implementation-oriented AI-SDLC documentation. The new layer should answer “what do I do next?” while linking to existing deep references for protocol, security, transport, and runtime details.

## Information architecture

The user-facing entry path is:

```text
README
  -> docs/getting-started.md
       -> docs/new-project-setup.md
       -> docs/feature-lifecycle-guide.md
       -> docs/role-guide.md
       -> docs/autonomous-development.md
       -> docs/troubleshooting.md
```

Each document has one primary job:

- `getting-started.md`: mental model plus the shortest complete path from new repository to first Feature.
- `new-project-setup.md`: installation details, file layout, Project Adapter, caller workflows, immutable pins, Installation Check, public/private target differences.
- `feature-lifecycle-guide.md`: end-to-end first Feature tutorial and daily Feature loop, including real command syntax and both supported lifecycle profiles.
- `role-guide.md`: role responsibilities, authority boundaries, and copyable single-role ChatGPT prompts.
- `autonomous-development.md`: manual vs gh-aw execution, branch/PR boundaries, credentials and preflight entry points.
- `troubleshooting.md`: symptom-first diagnosis with links to authoritative deep references.

## Navigation design

Add a prominent `## Getting started` section near the beginning of `README.md`, before readers are required to understand Project Adapter, cross-repository transport, or runtime internals.

Every new guide should cross-link to the other user guides where the user is likely to need the next task. Deep implementation documents remain the source for detailed protocol/security behavior.

## Content ownership and duplication rules

The new guides own:

- ordered user procedures;
- copyable command examples;
- decision tables for common choices;
- role handoff guidance;
- first-Feature walkthroughs;
- symptom-oriented troubleshooting.

Existing documents continue to own:

- protocol/schema definitions;
- detailed transition/concurrency mechanics;
- security threat model;
- cross-repository transport internals;
- runtime adapter implementation details;
- release/stabilization evidence.

The user guides summarize those concepts only enough to perform the task, then link to the existing document.

## First Feature tutorial design

Use one stable example throughout:

- Feature: `F-DEMO-LOGIN-0001`
- Title: `Add user login`
- Branch: `feature/F-DEMO-LOGIN-0001`
- Bootstrap: `state/bootstrap/F-DEMO-LOGIN-0001.yaml`
- Manifest: `state/features/F-DEMO-LOGIN-0001.yaml`

The tutorial follows the exact `standard-feature` profile:

```text
requirement
  -> requirement-review / requirement-gate
  -> design
  -> design-review / design-gate
  -> plan
  -> implementation
  -> code-review / code-gate
  -> verification / verification-gate
  -> acceptance / release-gate
  -> DONE
```

At each major handoff the guide tells the user to re-run Plan/read the current Manifest rather than assuming the revision or next action.

## Command source of truth

Issue Comment examples are copied only from the regex-supported forms in `templates/github/ai-sdlc-command.yml`:

```text
/ai-sdlc bootstrap target_ref=<branch> bootstrap=state/bootstrap/<file>.yaml manifest=state/features/<file>.yaml
/ai-sdlc plan target_ref=<branch> manifest=state/features/<file>.yaml
/ai-sdlc persist target_ref=<branch> manifest=state/features/<file>.yaml event=state/events/<feature-id>/<file>.yaml
/ai-sdlc dispatch-gh-aw target_ref=<branch> manifest=state/features/<file>.yaml
```

The guides must not invent additional command flags.

## Installation design

The setup guide uses the actual target workflow template names:

- `.github/workflows/ai-sdlc-installation-check.yml`
- `.github/workflows/ai-sdlc-bootstrap.yml`
- `.github/workflows/ai-sdlc-plan.yml`
- `.github/workflows/ai-sdlc-persist.yml`
- `.github/workflows/ai-sdlc-command.yml` (optional command bridge; required for the documented Issue Comment experience and public-target control transport)

Target repositories keep `.ai-sdlc/project.yaml`, `AGENTS.md`, durable `state/`, installed caller workflows, and source. They do not copy trusted control-plane scripts/schemas/roles/dispatch/runtime implementation.

## Public/private transport design

Explain two execution paths without merging their trust boundaries:

- Private target: installed lifecycle caller can consume the pinned private AI-SDLC Action when repository access policy permits.
- Public target while control repository is private: lifecycle commands route through the public-safe command bridge to the trusted control repository and the Runtime GitHub App.

The user guide links to `docs/public-target-lifecycle-transport.md` for detailed mechanics.

## Authority boundary design

Use the same rule across every guide:

> Workers may produce code, artifacts, evidence, and Feature Events. They do not become lifecycle authority merely by completing work.

Developer/worker documentation explicitly forbids direct authoritative Manifest/Event ledger mutation, Gate PASS/waiver, merge, and release. Review, QA, and acceptance stay independent stages.

## Validation design

Before PR readiness:

1. compare all workflow names and command forms against current templates;
2. compare all lifecycle stages against current profiles;
3. validate secret names and preflight names against current autonomous/cross-repository documents and workflows;
4. inspect Markdown links and repository-relative paths;
5. run repository validation/CI on the PR;
6. independently review the final docs for authority-boundary regressions or invented capabilities.
