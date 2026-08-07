#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-dispatch.yml"
RESULT = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-result.yml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    dispatch = DISPATCH.read_text(encoding="utf-8")
    result = RESULT.read_text(encoding="utf-8")

    for path, text in ((DISPATCH, dispatch), (RESULT, result)):
        require("Checkout trusted control plane" in text, f"{path.name}: trusted control checkout missing")
        require("ref: ${{ github.event.repository.default_branch }}" in text, f"{path.name}: trusted runtime is not default-branch based")
        require("path: runtime" in text and "path: workspace" in text, f"{path.name}: runtime/workspace split missing")
        require("pip install -r runtime/requirements-dev.txt" in text, f"{path.name}: dependencies not loaded from trusted runtime")
        require("python workspace/" not in text and "pip install -r workspace/" not in text, f"{path.name}: target workspace code/dependencies execute in control job")
        require("actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in text, f"{path.name}: checkout pin drifted")
        require("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text, f"{path.name}: setup-python pin drifted")
        require("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in text, f"{path.name}: upload-artifact pin drifted")
        require("pull_request_target:" not in text and "workflow_run:" not in text, f"{path.name}: privileged untrusted trigger introduced")

    require("permissions:\n      contents: read" in dispatch, "gh-aw plan job is not read-only")
    require("contents: write\n      actions: write" in dispatch, "gh-aw execute job lacks explicit contents/actions write envelope")
    require(dispatch.count("actions: write") == 1, "actions: write must exist only on the execute job")
    require("gh workflow run" in dispatch, "gh-aw execute path does not invoke workflow_dispatch")
    require("gh_aw_adapter.py start-event" in dispatch, "gh-aw execute path does not reserve stage before worker dispatch")
    require("ingest_feature_event.py" in dispatch, "gh-aw START reservation bypasses Event Inbox validation")
    require("verify_git_write_precondition.py" in dispatch, "gh-aw START reservation lacks remote-branch write precondition")
    require("git -C workspace push" in dispatch, "gh-aw reservation is not isolated to target workspace")
    require("gh-aw autonomous dispatch must target a non-default branch" in dispatch, "gh-aw dispatch lost default-branch denial")
    require(
        dispatch.index("git -C workspace push") < dispatch.index("gh', 'workflow', 'run"),
        "gh-aw worker may be dispatched before WORKING reservation is durably pushed",
    )
    require("AI-SDLC intentionally does not guess a rollback/block transition here" in dispatch, "dispatch failure semantics are no longer explicit/fail-closed")
    require("status: BLOCKED" not in dispatch, "dispatch workflow guesses a BLOCKED lifecycle transition after transport failure")

    require("permissions:\n      contents: write" in result, "gh-aw result intake lacks explicit write envelope")
    require("actions: write" not in result, "gh-aw result intake unnecessarily has Actions write permission")
    require("WORKER_RESULT_JSON: ${{ inputs.worker_result_json }}" in result, "worker result is not passed through a quoted environment boundary")
    require("gh_aw_adapter.py result-to-event" in result, "worker result bypasses runtime result contract")
    require("ingest_feature_event.py" in result, "worker result bypasses Event Inbox/revision validation")
    require("verify_git_write_precondition.py" in result, "worker result persistence lacks remote-branch precondition")
    require("git -C workspace add -- \"$MANIFEST_PATH\" \"$EVENT_PATH\"" in result, "worker result writes outside the canonical Manifest/Event set")
    require("git -C workspace push" in result, "worker result Git push is not workspace-isolated")
    require("gate" not in result.lower() or "gate" not in result, "result workflow should not contain direct Gate mutation logic")

    print("gh-aw runtime transport trusted-boundary and permission checks passed")


if __name__ == "__main__":
    main()
