# GitHub caller workflow templates

These files are templates, not ready-to-run production workflows until their AI-SDLC self-reference is replaced.

Every template contains:

```text
@REPLACE_WITH_AI_SDLC_FULL_SHA # ai-sdlc-install-placeholder
```

Replace `REPLACE_WITH_AI_SDLC_FULL_SHA` with a reviewed 40-character commit SHA from `DREAM-XIN/ai-sdlc` before committing the workflow to a target repository.

Do not replace it with `main`, a release branch, or a moving tag. A release tag may be useful for humans to discover a version, but the workflow should pin the commit SHA that the tag was reviewed to contain.

The third-party GitHub Actions already present in these templates are pinned to reviewed immutable SHAs. Update those pins only through normal dependency review and CI.
