# Verification — F-AISDLC-DOCS-0001

## Verdict

PASS

## QA scope

Independently verified the user-facing documentation change on PR #194 after Code Review completed and `verification` became the current stage.

## Repository CI evidence

Verification-start commit:

```text
8f6c5e8e0ea2b9f5225c0b5ba8365ae79d226214
```

GitHub Actions results on that commit:

- `Validate AI-SDLC protocol` run `31302288547`: success.
- `Required PR Gate` run `31302288556`: success.

The checks were re-run after Code Review lifecycle persistence; this avoids treating an older implementation-head result as final QA evidence.

## Targeted documentation checks

### Navigation and links

Verified that README links to all six new user guides and that their repository-relative deep references resolve to existing paths, including:

- `docs/project-adapter.md`
- `docs/feature-bootstrap-event-inbox.md`
- `docs/optimistic-concurrency.md`
- `docs/cross-repository-installation.md`
- `docs/cross-repository-installation-preflight.md`
- `docs/public-target-lifecycle-transport.md`
- `docs/cross-repository-autonomous-execution.md`
- `docs/security-model.md`
- `docs/integrations/gh-aw.md`
- `runtimes/gh-aw/README.md`

### Command syntax

Checked the documented Issue Comment forms against the v0.2.0 baseline `templates/github/ai-sdlc-command.yml` regexes:

```text
/ai-sdlc bootstrap target_ref=<branch> bootstrap=state/bootstrap/<file>.yaml manifest=state/features/<file>.yaml
/ai-sdlc plan target_ref=<branch> manifest=state/features/<file>.yaml
/ai-sdlc persist target_ref=<branch> manifest=state/features/<file>.yaml event=state/events/<feature-id>/<file>.yaml
/ai-sdlc dispatch-gh-aw target_ref=<branch> manifest=state/features/<file>.yaml
```

No unsupported provider/model/policy/worker selector is documented.

### Lifecycle profiles

Checked against the v0.2.0 profile YAML:

- `standard-feature`: `requirement -> requirement-review -> design -> design-review -> plan -> implementation -> code-review -> verification -> acceptance`.
- `small-change`: `requirement -> implementation -> review -> verification`.

### Installation and runtime names

Checked exact workflow/template/credential names used by the guides, including:

- `ai-sdlc-installation-check.yml`
- `ai-sdlc-bootstrap.yml`
- `ai-sdlc-plan.yml`
- `ai-sdlc-persist.yml`
- `ai-sdlc-command.yml`
- `AI_SDLC_CONTROL_DISPATCH_TOKEN`
- `AI_SDLC_RUNTIME_APP_CLIENT_ID`
- `AI_SDLC_RUNTIME_APP_PRIVATE_KEY`
- `AI-SDLC gh-aw Cross-Repo Runtime Preflight`
- `AI-SDLC gh-aw Runtime Preflight`

### Public/private behavior

Verified the public-target explanation against the v0.2.0 baseline public transport contract: public callers cannot download the private control Action, while Bootstrap/Plan/Persist can route through the public-safe command bridge to the trusted control repository and exact-target Runtime GitHub App. The guides explicitly call out that the Action-based Installation Check caller itself is not made executable for a public target by that lifecycle bridge.

### Authority boundaries

Verified that the user guides do not grant a Developer authority to directly rewrite the Feature Manifest, PASS/waive Gates, self-approve Code Review, skip Verification, merge, or release. Manual ChatGPT Web and autonomous gh-aw are described as execution modes under the same durable lifecycle authority.

## QA conclusion

The documentation is internally navigable, aligned with the v0.2.0 baseline behavior, and backed by successful repository PR validation. No blocking verification defect was found. `verification-gate` may PASS; Acceptance remains separately required.