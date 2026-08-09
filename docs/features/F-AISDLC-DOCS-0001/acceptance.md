# Acceptance — F-AISDLC-DOCS-0001

## Verdict

PASS

## Acceptance scope

Evaluated the delivered user documentation as the Acceptance Product Owner after independent Code Review and Verification passed.

## User journey acceptance

### 1. First repository entry

PASS — A first-time user opening `README.md` now sees a prominent `Getting started` section near the top with direct navigation to setup, first Feature, roles, autonomous development, and troubleshooting.

### 2. New project installation

PASS — `docs/new-project-setup.md` gives an ordered installation path covering `AGENTS.md`, `.ai-sdlc/project.yaml`, real caller workflow names, immutable full-SHA pins, credentials, installation validation, and the public/private transport distinction.

### 3. First complete Feature

PASS — `docs/feature-lifecycle-guide.md` walks `F-DEMO-LOGIN-0001` / `Add user login` from Issue and Feature branch through Bootstrap, Plan, Requirement, Requirement Review, Design, Design Review, Plan, Implementation, Code Review, Verification, Acceptance, and `DONE`, using the real v0.2.0 Issue Comment command forms.

### 4. Daily lifecycle operation

PASS — The guides explain the durable loop `Plan -> worker -> Artifact/Evidence/Event -> Persist -> re-read Manifest -> Plan again`, optimistic `expected_revision`, Gate behavior, and why the authoritative Manifest is not directly edited by ordinary workers.

### 5. Role separation

PASS — `docs/role-guide.md` defines Product, Requirement Reviewer, Architect, Design Reviewer, Orchestrator, Developer, Code Reviewer, QA, and Acceptance Product Owner boundaries and provides concise copyable prompts that require each new worker context to re-read current GitHub state.

### 6. Lifecycle profile choice

PASS — Users get a simple decision model while the documented stage sequences remain identical to the real profiles: nine-stage `standard-feature` and four-stage `small-change`.

### 7. Manual vs autonomous development

PASS — `docs/autonomous-development.md` distinguishes ChatGPT Web/manual execution from trusted gh-aw Developer execution, including bounded worker branches, Draft PR base, Worker Result/collector flow, private-target credentials, Runtime App, and separate preflights.

### 8. Troubleshooting

PASS — `docs/troubleshooting.md` covers the required dogfood failure classes: installation, identity, missing AGENTS/context, pinning, revision conflict, Persist, stage/Gate status, public transport, dispatch token, Runtime App, engine/provider preflight, Draft PR/state divergence, and independent Code Review/Verification.

### 9. Authority and security boundaries

PASS — The user-facing path consistently preserves GitHub as the durable system of record, immutable control-plane pins, trusted persistence, Evidence-backed Gates, independent review/QA/acceptance, and the separation between Feature branch and autonomous worker branch.

## Verification dependency

Acceptance relies on `evidence-verification-v1`, whose QA record includes successful repository PR validation and targeted command/profile/link/credential checks. Acceptance does not replace or waive Verification.

## Release Gate conclusion

The delivered documentation meets the approved requirement and final user outcome: a person unfamiliar with AI-SDLC can start from README, install a target repository, create and run a first Feature, choose the correct role/mode, diagnose common failures, and understand when the Feature reaches `DONE` without first reading the full internal architecture.

`release-gate` may PASS.