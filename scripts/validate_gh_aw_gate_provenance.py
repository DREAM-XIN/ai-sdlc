#!/usr/bin/env python3
from copy import deepcopy

from gh_aw_gate_provenance import GateProvenanceError, dispatch_key, validate_run

CONTROL = "DREAM-XIN/ai-sdlc"
BRANCH = "main"
ROLE = "reviewer"
STAGE = "code-review"
FEATURE = "F-GATE"
TASK = "F-GATE-CODE-REVIEW"
REVISION = 42
HEAD = "a" * 40
WORKFLOW = "ai-sdlc-gh-aw-reviewer-claude.lock.yml"
PATH = f".github/workflows/{WORKFLOW}"
REF = f"{CONTROL}/{PATH}@refs/heads/{BRANCH}"
RUN_ID = 123456


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_invalid(run, **overrides):
    args = dict(
        source_run_id=RUN_ID,
        source_workflow_ref=REF,
        control_repository=CONTROL,
        default_branch=BRANCH,
        role=ROLE,
        stage=STAGE,
        feature_id=FEATURE,
        task_id=TASK,
        expected_revision=REVISION,
        candidate_head_sha=HEAD,
    )
    args.update(overrides)
    try:
        validate_run(run, **args)
    except GateProvenanceError:
        return
    raise AssertionError("invalid Gate provenance unexpectedly validated")


def main():
    key = dispatch_key(FEATURE, TASK, REVISION, HEAD)
    run = {
        "id": RUN_ID,
        "repository": {"full_name": CONTROL},
        "event": "workflow_dispatch",
        "head_branch": BRANCH,
        "path": PATH,
        "display_title": f"AI-SDLC gh-aw gate reviewer {key}",
    }
    worker = validate_run(
        run,
        source_run_id=RUN_ID,
        source_workflow_ref=REF,
        control_repository=CONTROL,
        default_branch=BRANCH,
        role=ROLE,
        stage=STAGE,
        feature_id=FEATURE,
        task_id=TASK,
        expected_revision=REVISION,
        candidate_head_sha=HEAD,
    )
    require(worker.worker_workflow == WORKFLOW, "valid provenance resolved wrong role worker")

    expect_invalid(run, source_run_id=RUN_ID + 1)
    expect_invalid(run, task_id="F-GATE-OTHER-TASK")
    expect_invalid(run, source_workflow_ref=f"{CONTROL}/.github/workflows/ai-sdlc-gh-aw-worker.lock.yml@refs/heads/{BRANCH}")

    wrong_workflow = deepcopy(run)
    wrong_workflow["path"] = ".github/workflows/ai-sdlc-gh-aw-worker.lock.yml"
    expect_invalid(wrong_workflow)

    wrong_repo = deepcopy(run)
    wrong_repo["repository"]["full_name"] = "attacker/repo"
    expect_invalid(wrong_repo)

    bot_comment_only = {}
    expect_invalid(bot_comment_only)

    wrong_title = deepcopy(run)
    wrong_title["display_title"] = "AI-SDLC gh-aw gate reviewer spoofed"
    expect_invalid(wrong_title)

    print("gh-aw Gate provenance validation passed")


if __name__ == "__main__":
    main()
