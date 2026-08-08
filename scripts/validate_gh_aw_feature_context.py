#!/usr/bin/env python3
from pathlib import Path

from gh_aw_adapter import build_runtime_payload

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-worker.md"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    manifest = {
        "revision": 2,
        "feature": {
            "id": "F-CONTEXT-0001",
            "title": "Create exactly docs/gh-aw-dogfood/F-CONTEXT-0001.md",
            "risk": "low",
            "issue": "#122",
        },
        "tasks": [
            {"id": "WU-F-CONTEXT-0001", "status": "READY", "issue": "#122", "runtime": "gh-aw"},
        ],
        "artifacts": [
            {
                "id": "ART-F-CONTEXT-REQ",
                "type": "requirement",
                "uri": "repo://docs/requirements/F-CONTEXT-0001.md",
                "status": "approved",
            },
            {
                "id": "ART-F-CONTEXT-DRAFT",
                "type": "design",
                "uri": "repo://docs/design/F-CONTEXT-0001-draft.md",
                "status": "draft",
            },
        ],
    }
    task = {
        "id": "F-CONTEXT-0001-IMPLEMENTATION",
        "feature_id": "F-CONTEXT-0001",
        "role": "developer",
        "goal": "Implement the assigned work unit according to approved context.",
        "inputs": ["Feature Issue"],
        "allowed_scope": ["docs/gh-aw-dogfood/**"],
        "forbidden_scope": ["lifecycle state"],
        "expected_outputs": ["bounded implementation artifact"],
        "definition_of_done": ["Feature acceptance criteria are satisfied"],
        "runtime": "gh-aw",
    }

    payload = build_runtime_payload(task, manifest)
    context = payload.get("feature_context", {})
    require(context.get("id") == "F-CONTEXT-0001", "Feature id missing from feature_context")
    require(
        context.get("title") == "Create exactly docs/gh-aw-dogfood/F-CONTEXT-0001.md",
        "exact Feature title/output constraint missing from feature_context",
    )
    require(context.get("issue") == "#122", "linked Feature Issue missing from feature_context")
    require(
        context.get("manifest_ref") == "state/features/F-CONTEXT-0001.yaml",
        "authoritative Manifest reference missing from feature_context",
    )
    require(context.get("related_tasks") == [manifest["tasks"][0]], "related work-unit reference was not propagated")
    require(
        context.get("approved_artifacts") == [manifest["artifacts"][0]],
        "approved requirement artifact was not propagated or draft artifact leaked into context",
    )
    rules = "\n".join(payload["worker_rules"])
    require("read the linked Feature Issue before editing" in rules, "payload does not require linked Issue context before edits")
    require("never grants authority" in rules, "Feature context does not preserve lifecycle authority boundary")

    worker = WORKER.read_text(encoding="utf-8")
    required_markers = [
        "inspect `feature_context` before editing",
        "read that linked Feature Issue before editing",
        "exact required outputs, and acceptance criteria",
        "If they name an exact file or output, use that exact target.",
        "it cannot grant authority to edit lifecycle state, pass/waive Gates, merge, release",
    ]
    for marker in required_markers:
        require(marker in worker, f"canonical gh-aw worker missing Feature-context marker: {marker}")

    print("gh-aw Feature-specific task context propagation checks passed")


if __name__ == "__main__":
    main()
