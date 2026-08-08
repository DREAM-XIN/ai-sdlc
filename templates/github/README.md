# GitHub caller workflow templates

These files are templates for installing AI-SDLC transport into a target repository.

The Plan, Bootstrap and Persist templates reference the private AI-SDLC control repository and are not ready for production until their AI-SDLC self-reference is replaced.

Those templates contain:

```text
@REPLACE_WITH_AI_SDLC_FULL_SHA # ai-sdlc-install-placeholder
```

Replace `REPLACE_WITH_AI_SDLC_FULL_SHA` with a reviewed 40-character commit SHA from `DREAM-XIN/ai-sdlc` before committing the workflow to a target repository.

Do not replace it with `main`, a release branch, or a moving tag. A release tag may be useful for humans to discover a version, but the workflow should pin the commit SHA that the tag was reviewed to contain.

The optional `ai-sdlc-command.yml` template is different: it does not execute AI-SDLC control code itself. It accepts exact trusted Issue Comment commands from an OWNER, MEMBER or COLLABORATOR and dispatches the already-installed local `ai-sdlc-bootstrap.yml` or `ai-sdlc-plan.yml` workflow. This allows interactive surfaces such as ChatGPT Web to continue a Feature without requiring a human to open the Actions UI while preserving the existing caller permission boundaries.

Supported commands are:

```text
/ai-sdlc bootstrap target_ref=<branch> bootstrap=state/bootstrap/<file>.yaml manifest=state/features/<file>.yaml
/ai-sdlc plan target_ref=<branch> manifest=state/features/<file>.yaml
```

The command bridge has `actions: write`, `contents: read`, and `issues: write`. It must not receive `contents: write`, provider/model selectors, or provider credentials. Bootstrap commands are always dispatched with `allow_default_branch=false`.

The third-party GitHub Actions already present in these templates are pinned to reviewed immutable SHAs. Update those pins only through normal dependency review and CI.
