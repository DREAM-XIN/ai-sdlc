# Implementation Plan — F-AISDLC-DOCS-0001

## Work units

1. **Getting Started**
   - Create `docs/getting-started.md` as the primary first-time-user entry.
   - Explain control plane vs target repository, GitHub system of record, Manifest/Event/Persist/Gate concepts, and the end-to-end operating loop.
   - Link onward to setup, lifecycle, roles, autonomous development, and troubleshooting.

2. **New Project Setup**
   - Create `docs/new-project-setup.md`.
   - Use the current Project Adapter template and current five GitHub caller template file names.
   - Explain `AGENTS.md`, `context.rules`, `context.read`, commands, ownership, immutable full-SHA pins, Installation Check, and public/private target transport differences.

3. **Feature Lifecycle / First Feature**
   - Create `docs/feature-lifecycle-guide.md`.
   - Walk `F-DEMO-LOGIN-0001` from Issue/branch/bootstrap through all `standard-feature` stages to DONE.
   - Include only command forms implemented by `templates/github/ai-sdlc-command.yml`.
   - Explain `standard-feature` vs `small-change` using the actual profile stages.

4. **Role Guide**
   - Create `docs/role-guide.md`.
   - Define Product, Requirement Reviewer, Architect, Design Reviewer, Orchestrator, Developer, Code Reviewer, QA, and Acceptance Product Owner responsibilities and forbidden authority.
   - Provide short copyable ChatGPT prompt templates with one role per new window and mandatory live GitHub state re-read.

5. **Autonomous Development**
   - Create `docs/autonomous-development.md`.
   - Contrast Manual / ChatGPT Web Task Package execution with trusted gh-aw autonomous execution.
   - Document bounded worker branch and Draft PR behavior, forbidden lifecycle writes, private-target credentials, Runtime App, and preflights.

6. **Troubleshooting**
   - Create `docs/troubleshooting.md` covering the required installation, pin, revision, Persist, stage/Gate, dispatch, Runtime App/token/provider, Draft PR/state, and independence symptoms.
   - Prefer concise fixes plus links to existing deep documents.

7. **README Navigation**
   - Add a prominent Getting Started navigation section near the top of `README.md`.

## Validation matrix

- README links resolve to all six user guides.
- All guide-to-guide and guide-to-existing-doc links use existing paths.
- Workflow names exactly match current templates.
- Issue Comment syntax exactly matches current command bridge regexes.
- `standard-feature` and `small-change` stage sequences exactly match profile YAML.
- Secret names exactly match current implementation/docs: `AI_SDLC_CONTROL_DISPATCH_TOKEN`, `AI_SDLC_RUNTIME_APP_CLIENT_ID`, `AI_SDLC_RUNTIME_APP_PRIVATE_KEY`.
- Public target guidance matches `docs/public-target-lifecycle-transport.md`.
- Private target guidance matches `docs/cross-repository-installation.md`.
- Autonomous worker boundaries match `docs/cross-repository-autonomous-execution.md` and `docs/security-model.md`.
- Optimistic revision guidance matches `docs/optimistic-concurrency.md` and Event Inbox rules.
- Repository PR validation/CI is green before Verification Gate PASS.

## Review strategy

Code Review is a documentation correctness review, not self-attestation. It must specifically look for:

- invented commands, stages, secrets, or capabilities;
- moving-ref examples;
- confusing Feature branch with worker implementation branch;
- language implying a worker may directly change authoritative lifecycle state or PASS its own Gate;
- duplicated implementation documentation likely to drift.

Verification requires repository CI plus targeted link/path/command/profile checks. Acceptance evaluates whether a first-time user can follow the flow from README to a merge-ready first Feature.
