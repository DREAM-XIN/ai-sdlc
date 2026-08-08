# Public lifecycle runtime distribution

## Goal

A target repository should not need a PAT, control-dispatch secret, Runtime GitHub App client id, or Runtime GitHub App private key just to run the base AI-SDLC lifecycle (`plan`, `bootstrap`, `persist`).

The private `DREAM-XIN/ai-sdlc` repository remains the source-of-truth control repository. It publishes a minimal reviewed runtime distribution to a separate public repository, expected to be `DREAM-XIN/ai-sdlc-runtime`.

Target repositories consume the public runtime by immutable commit SHA and use only their repository-scoped `GITHUB_TOKEN` for their own checkout and Feature-branch persistence.

## Trust model

```text
Private DREAM-XIN/ai-sdlc
  source / tests / autonomous workers / policies
             |
             | reviewed central publish
             v
Public DREAM-XIN/ai-sdlc-runtime @ immutable SHA
  lifecycle Action + lifecycle Python closure + schema + default policy
             |
             | target GITHUB_TOKEN
             v
Target repository Feature branch
```

The public distribution is not an independent source repository. Every published tree records the private source commit in `SOURCE_AI_SDLC_COMMIT`, and `runtime-manifest.json` records the exported file set and SHA-256 digests.

## What is exported

`scripts/build_public_runtime.py` starts from the lifecycle Action's executable Python seeds and recursively follows repository-local Python imports. It also exports protocol schema data and the default dispatch policy required by lifecycle planning.

The bundle includes the lifecycle composite Action and its dependency file so a caller can use:

```yaml
uses: DREAM-XIN/ai-sdlc-runtime/.github/actions/control@<reviewed-runtime-commit-sha>
```

## What is intentionally not exported

The distribution must not contain:

- control-repository GitHub workflows;
- target Feature state or Event Inbox data;
- project documentation/templates;
- autonomous gh-aw workers and engine profiles;
- provider configuration;
- Runtime GitHub App credentials/configuration;
- target-to-control dispatch credentials.

`scripts/validate_public_runtime_distribution.py` enforces this boundary in CI before a runtime can be published.

## Credential model

### Target repositories

Base lifecycle operations require no custom repository secret. The target workflow grants its own `GITHUB_TOKEN` only the normal operation-specific permissions:

- `plan`: `contents: read`;
- `bootstrap`: `contents: write` when persistence is requested;
- `persist`: `contents: write` when persistence is requested.

### Private control repository

Only the central publisher needs a credential capable of writing the public runtime repository. Configure `AI_SDLC_RUNTIME_PUBLISH_TOKEN` once in `DREAM-XIN/ai-sdlc`, scoped only to `DREAM-XIN/ai-sdlc-runtime` contents write access.

This central publication credential is infrastructure configuration. It is not copied to target repositories.

## Autonomous execution remains separate

This distribution removes cross-repository credentials from the **base lifecycle**. It does not make autonomous Developer execution target-local.

Autonomous execution may still use the private control repository and a centrally managed Runtime GitHub App because it owns trusted worker policy, engine profiles, Safe Output, and collector behavior. The installation UX for autonomous execution should be treated independently from the base lifecycle distribution.

The important boundary is:

- ordinary lifecycle: zero target secrets;
- autonomous execution: central trusted service/App boundary only when that capability is enabled.

## Rollout

1. Merge the distribution builder, validator, and publisher into `ai-sdlc`.
2. Create `DREAM-XIN/ai-sdlc-runtime` as a Public repository with a protected `main` branch.
3. Configure `AI_SDLC_RUNTIME_PUBLISH_TOKEN` once in the private control repository.
4. Run **AI-SDLC Publish Public Runtime** and review the resulting public runtime commit.
5. Update lifecycle installation templates to pin `DREAM-XIN/ai-sdlc-runtime/.github/actions/control@<full SHA>`.
6. Update the public-target command bridge to dispatch installed target-local lifecycle workflows instead of the private cross-repository lifecycle workflow.
7. Remove `AI_SDLC_CONTROL_DISPATCH_TOKEN` from public targets once the new target-local transport is verified.

Do not switch installation templates before step 4 has produced a real reviewed public runtime commit.
