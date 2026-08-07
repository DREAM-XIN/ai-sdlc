#!/usr/bin/env python3
from copy import deepcopy

import yaml

from github_persistence import build_plan
from validate_feature_manifest import validate_manifest
from validate_feature_transition import event
from validate_orchestrator_examples import base_manifest


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    manifest = base_manifest()
    start_event = event(
        "F-0030",
        [{"kind": "stage", "id": "design", "status": "WORKING"}],
        expected_revision=0,
    )
    planned = build_plan(
        manifest,
        start_event,
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0030.yaml",
        target_ref="feature/example",
        issue=30,
    )
    require(planned["outcome"] == "PLANNED", f"valid persistence plan failed: {planned}")
    plan = planned["plan"]
    require(plan["target"]["ref"] == "feature/example", "target ref not preserved")
    require(plan["manifest"]["feature_id"] == "F-0030", "feature identity missing")
    require(plan["manifest"]["source_revision"] == 0, "source revision missing")
    require(plan["manifest"]["revision"] == 1, "result revision missing")
    require(len(plan["manifest"]["source_sha256"]) == 64, "source manifest digest missing")
    require(len(plan["manifest"]["sha256"]) == 64, "result manifest digest missing")
    require(plan["manifest"]["source_sha256"] != plan["manifest"]["sha256"], "source/result digests unexpectedly equal")
    require("revision=0->1" in plan["summary"]["message"], "revision transition missing from summary")
    require("status: WORKING" in plan["manifest"]["content"], "updated manifest content missing")
    require("revision: 1" in plan["manifest"]["content"], "updated manifest revision missing")
    materialized = yaml.safe_load(plan["manifest"]["content"])
    require(not validate_manifest(materialized), "materialized manifest failed semantic validation")
    kinds = {item["kind"] for item in plan["mutations"]}
    require(kinds == {"update-file", "check-run", "issue-comment"}, f"unexpected mutation set: {kinds}")
    update = next(item for item in plan["mutations"] if item["kind"] == "update-file")
    require(update["source_sha256"] == plan["manifest"]["source_sha256"], "update precondition digest mismatch")
    comment = next(item for item in plan["mutations"] if item["kind"] == "issue-comment")
    require("ai-sdlc-feature-status:F-0030" in comment["body"], "stable status marker missing")

    invalid_event = event(
        "F-OTHER",
        [{"kind": "stage", "id": "design", "status": "WORKING"}],
        expected_revision=0,
    )
    invalid = build_plan(
        manifest,
        invalid_event,
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0030.yaml",
        target_ref="feature/example",
    )
    require(invalid["outcome"] == "INVALID" and "plan" not in invalid, "invalid event produced a persistence plan")

    stale_event = event(
        "F-0030",
        [{"kind": "stage", "id": "design", "status": "WORKING"}],
        event_id="EVT-STALE",
        expected_revision=7,
    )
    stale = build_plan(
        manifest,
        stale_event,
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0030.yaml",
        target_ref="feature/example",
    )
    require(stale["outcome"] == "INVALID", "stale persistence event unexpectedly produced a plan")

    terminal = deepcopy(manifest)
    terminal["workflow"]["status"] = "CANCELLED"
    rejected = build_plan(
        terminal,
        start_event,
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0030.yaml",
        target_ref="feature/example",
    )
    require(rejected["outcome"] == "INVALID", "terminal feature unexpectedly produced a plan")

    traversal = build_plan(
        manifest,
        start_event,
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="../../.github/workflows/pwn.yml",
        target_ref="feature/example",
    )
    require(traversal["outcome"] == "INVALID", "path traversal unexpectedly produced a plan")

    wrong_root = build_plan(
        manifest,
        start_event,
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="docs/F-0030.yaml",
        target_ref="feature/example",
    )
    require(wrong_root["outcome"] == "INVALID", "write outside state/features unexpectedly produced a plan")

    print("GitHub persistence revision/precondition scenarios passed")


if __name__ == "__main__":
    main()
