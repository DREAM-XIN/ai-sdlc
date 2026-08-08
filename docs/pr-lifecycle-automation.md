# Trusted PR lifecycle automation

AI-SDLC can close the review and verification stages for a bounded gh-aw work PR from trusted GitHub evidence without manually authoring lifecycle Event YAML.

## Eligible PR boundary

The privileged workflow is intentionally narrow. It only reacts to an `APPROVED` `pull_request_review` when all of the following are true:

- the PR head repository is the current repository (forks/cross-repository heads are rejected);
- the head ref starts with `gh-aw/`;
- the PR base is not the repository default branch;
- the head ref uniquely maps to exactly one Feature Manifest on the base branch;
- the PR head SHA has a completed successful `validate` check with an HTTPS evidence URL.

The workflow implementation and Python helpers are loaded from the repository default branch. PR-controlled code is never executed in the privileged write job.

## Lifecycle

For an eligible approved work PR:

1. Resolve the Feature Manifest from the non-default Feature base branch.
2. Build a review Event with durable PR review and CI evidence.
3. Apply the Event through `ingest_feature_event.py`.
4. Persist the Event and event-derived Manifest to the Feature base branch after `verify_git_write_precondition.py` succeeds.
5. Build a verification Event from the updated Manifest and the same successful deterministic CI evidence.
6. Apply and persist the verification Event through the same trusted event-ingest path.

The review Event can move `review` from `TODO` or `READY` to `WORKING` and then `DONE` in one event, passes `code-gate`, and starts `verification`. The verification Event completes `verification` and passes `verification-gate`.

The collector never invents a Manifest state independently of the Feature Event application logic.

## Why the collector persists atomically

GitHub intentionally suppresses workflow chaining for pushes made with the default `GITHUB_TOKEN`. Therefore a privileged lifecycle workflow cannot safely push an Event and depend on the separate push-triggered persistence workflow to run afterward without introducing a PAT or other recursion bypass.

AI-SDLC does not add that bypass. Instead, the lifecycle collector follows the same trusted pattern as gh-aw result ingestion: generate the Event, run the normal event ingest/application code, validate the derived Manifest, enforce optimistic git-write preconditions, then commit the Event and derived Manifest together.

Human- or connector-authored Feature Event pushes continue to use the automatic persistence flow described in `automatic-feature-event-persistence.md`.

## Failure and recovery

The workflow fails closed for fork PRs, default-branch targets, non-gh-aw heads, ambiguous Feature mappings, missing approvals, or missing/non-green required CI evidence. No lifecycle state is written in those cases.

Manual Feature Event creation remains the recovery path. Operators can author the same review/verification Events explicitly and use normal automatic Event persistence or the manual `workflow_dispatch` persistence fallback.
