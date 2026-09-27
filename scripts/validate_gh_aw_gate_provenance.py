#!/usr/bin/env python3
from copy import deepcopy

from gh_aw_gate_provenance import GateProvenanceError, dispatch_key, validate_run

CONTROL = "DREAM-XIN/ai-sdlc"
TARGET = "dream-xin/ai-sdlc"
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
        target_repository=TARGET,
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
    key = dispatch_key(
        target_repository=TARGET,
        feature_id=FEATURE,
        task_id=TASK,
        revision=REVISION,
        stage=STAGE,
        role=ROLE,
        head_sha=HEAD,
    )
    run = {
        "id": RUN_ID,
        "repository": {"full_name": CONTROL},
        "event": "workflow_dispatch",
        "head_branch": BRANCH,
        "path": PATH,
        "display_title": f"AI-SDLC gh-aw {key}",
    }
    worker = validate_run(
        run,
        source_run_id=RUN_ID,
        source_workflow_ref=REF,
        control_repository=CONTROL,
        target_repository=TARGET,
        default_branch=BRANCH,
        role=ROLE,
        stage=STAGE,
        feature_id=FEATURE,
        task_id=TASK,
        expected_revision=REVISION,
        candidate_head_sha=HEAD,
    )
    require(worker.worker_workflow == WORKFLOW, "valid provenance resolved wrong role worker")

    local_reviewer_workflow = "ai-sdlc-gh-aw-reviewer-copilot-v03-local.lock.yml"
    local_reviewer_path = f".github/workflows/{local_reviewer_workflow}"
    local_reviewer_ref = f"{CONTROL}/{local_reviewer_path}@refs/heads/{BRANCH}"
    local_reviewer_run = deepcopy(run)
    local_reviewer_run["path"] = local_reviewer_path
    local_reviewer_worker = validate_run(
        local_reviewer_run,
        source_run_id=RUN_ID,
        source_workflow_ref=local_reviewer_ref,
        control_repository=CONTROL,
        target_repository=TARGET,
        default_branch=BRANCH,
        role=ROLE,
        stage=STAGE,
        feature_id=FEATURE,
        task_id=TASK,
        expected_revision=REVISION,
        candidate_head_sha=HEAD,
    )
    require(
        local_reviewer_worker.worker_workflow == "ai-sdlc-gh-aw-reviewer-copilot.lock.yml"
        and local_reviewer_worker.profile == "copilot",
        "reviewed local Reviewer alias did not resolve to canonical Copilot worker identity",
    )
    expect_invalid(
        local_reviewer_run,
        source_workflow_ref=f"{CONTROL}/.github/workflows/ai-sdlc-gh-aw-reviewer-copilot.lock.yml@refs/heads/{BRANCH}",
    )

    local_qa_workflow = "ai-sdlc-gh-aw-qa-gemini-v03-local.lock.yml"
    local_qa_path = f".github/workflows/{local_qa_workflow}"
    local_qa_ref = f"{CONTROL}/{local_qa_path}@refs/heads/{BRANCH}"
    local_qa_run = deepcopy(run)
    local_qa_run["path"] = local_qa_path
    local_qa_key = dispatch_key(
        target_repository=TARGET,
        feature_id=FEATURE,
        task_id=TASK,
        revision=REVISION,
        stage="verification",
        role="qa",
        head_sha=HEAD,
    )
    local_qa_run["display_title"] = f"AI-SDLC gh-aw {local_qa_key}"
    local_qa_worker = validate_run(
        local_qa_run,
        source_run_id=RUN_ID,
        source_workflow_ref=local_qa_ref,
        control_repository=CONTROL,
        target_repository=TARGET,
        default_branch=BRANCH,
        role="qa",
        stage="verification",
        feature_id=FEATURE,
        task_id=TASK,
        expected_revision=REVISION,
        candidate_head_sha=HEAD,
    )
    require(
        local_qa_worker.worker_workflow == "ai-sdlc-gh-aw-qa-gemini.lock.yml"
        and local_qa_worker.profile == "gemini",
        "reviewed local QA alias did not resolve to canonical Gemini worker identity",
    )

    expect_invalid(run, source_run_id=RUN_ID + 1)
    expect_invalid(run, task_id="F-GATE-OTHER-TASK")
    expect_invalid(run, target_repository="other/repo")
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
    wrong_title["display_title"] = "AI-SDLC gh-aw dispatch-" + "0" * 40
    expect_invalid(wrong_title)

    print("gh-aw Gate provenance validation passed")


if __name__ == "__main__":
    main()
