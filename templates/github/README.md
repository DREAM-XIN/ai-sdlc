# GitHub caller workflow templates

These files are templates for installing AI-SDLC transport into a target repository.

The Plan, Bootstrap and Persist templates reference the private AI-SDLC control repository and are not ready for production until their AI-SDLC self-reference is replaced.

Those templates contain:

```text
@REPLACE_WITH_AI_SDLC_FULL_SHA # ai-sdlc-install-placeholder
```

Replace `REPLACE_WITH_AI_SDLC_FULL_SHA` with a reviewed 40-character commit SHA from `DREAM-XIN/ai-sdlc` before committing the workflow to a target repository. Do not replace it with `main`, a release branch, or a moving tag.

The optional `ai-sdlc-command.yml` template accepts exact trusted Issue Comment commands from an OWNER, MEMBER or COLLABORATOR. Bootstrap, Plan and Persist continue to dispatch the already-installed local caller workflows. Autonomous Developer execution uses the same Issue surface but hands off to the trusted control repository runtime gateway.

Supported commands are:

```text
/ai-sdlc bootstrap target_ref=<branch> bootstrap=state/bootstrap/<file>.yaml manifest=state/features/<file>.yaml
/ai-sdlc plan target_ref=<branch> manifest=state/features/<file>.yaml
/ai-sdlc persist target_ref=<branch> manifest=state/features/<file>.yaml event=state/events/<feature-id>/<file>.yaml
/ai-sdlc dispatch-gh-aw target_ref=<branch> manifest=state/features/<file>.yaml
```

The autonomous command deliberately has no repository, policy, provider, model, engine-profile, credential, or compiled-worker selector. Target identity comes from `GITHUB_REPOSITORY`; trusted policy and worker/profile resolution stay in `DREAM-XIN/ai-sdlc`.

The command bridge keeps `actions: write`, `contents: read`, and `issues: write`; it must not receive target `contents: write`. Bootstrap/Persist/gh-aw commands reject the target default branch and all command paths reject parent traversal.

Cross-repository autonomous dispatch additionally requires `AI_SDLC_CONTROL_DISPATCH_TOKEN`. Scope it only to dispatch/read Actions in `DREAM-XIN/ai-sdlc`; it must not grant target-repository source write access. The trusted control runtime uses a separately managed GitHub App to mint short-lived exact-target repository tokens. See `docs/cross-repository-autonomous-execution.md`.

The third-party GitHub Actions already present in these templates are pinned to reviewed immutable SHAs. Update those pins only through normal dependency review and CI.
