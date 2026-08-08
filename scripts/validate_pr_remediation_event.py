#!/usr/bin/env python3
import tempfile
from pathlib import Path

import yaml

from apply_feature_event import apply_event
from pr_remediation_event import build_event

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def fixture_manifest():
    return {
        "protocol_version": "0.1.0",
        "revision": 2,
        "feature": {"id": "F-REVIEW-0001", "title": "Review remediation", "risk": "low", "issue": "#99"},
        "workflow": {
            "profile": "small-change",
            "status": "ACTIVE",
            "current_stage": "review",
            "stages": [
                {"id": "requirement", "status": "DONE"},
                {"id": "implementation", "status": "DONE"},
                {"id": "review", "status": "TODO", "gate": "code-gate"},
                {"id": "verification", "status": "TODO", "gate": "verification-gate"},
            ],
        },
        "tasks": [],
        "artifacts": [],
        "gates": [
            {"id": "code-gate", "status": "PENDING", "evidence": []},
            {"id": "verification-gate", "status": "PENDING", "evidence": []},
        ],
        "evidence": [],
        "applied_events": ["EVT-START", "EVT-IMPLEMENTATION"],
        "updated_at": "2026-08-08T08:00:00Z",
    }


def review_event(state="changes_requested", body="Read the linked Feature Issue before claiming context use.", reviewer="reviewer", author="github-actions[bot]"):
    return {
        "number": 10,
        "review": {
            "id": 12345,
            "state": state,
            "body": body,
            "html_url": "https://github.com/DREAM-XIN/ai-sdlc/pull/10#pullrequestreview-12345",
            "submitted_at": "2026-08-08T08:10:00Z",
            "user": {"login": reviewer},
        },
        "pull_request": {
            "number": 10,
            "html_url": "https://github.com/DREAM-XIN/ai-sdlc/pull/10",
            "user": {"login": author},
            "head": {
                "ref": "gh-aw/F-REVIEW-0001-123-v2-deadbeef",
                "sha": "1" * 40,
                "repo": {"full_name": "DREAM-XIN/ai-sdlc"},
            },
            "base": {"ref": "feature/F-REVIEW-0001"},
        },
    }


def main():
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp) / "workspace"
        manifest_path = workspace / "state" / "features" / "F-REVIEW-0001.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest = fixture_manifest()
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        generated, metadata = build_event(
            review_event(), workspace, ROOT, "DREAM-XIN/ai-sdlc", "main"
        )
        require(metadata["feature_id"] == "F-REVIEW-0001", f"feature mapping failed: {metadata}")
        require(metadata["target_stage"] == "implementation", f"wrong remediation target: {metadata}")
        require(metadata["source_stage"] == "review", f"wrong review source: {metadata}")
        require(generated["expected_revision"] == 2, "collector did not preserve Manifest revision")
        evidence, task_change = generated["changes"]
        require(evidence["kind"] == "evidence" and evidence["record"]["status"] == "warning", "review evidence missing")
        task = task_change["record"]
        require(task["kind"] == "remediation", "collector did not create remediation task")
        require(task["stage"] == "implementation" and task["role"] == "developer", f"trusted profile assignment lost: {task}")
        require(task["source_stage"] == "review", f"source review stage lost: {task}")
        require(task["target_pr"].endswith("/pull/10"), "target PR missing")
        require(task["issue"] == "#99", "Feature Issue was not propagated")
        require("linked Feature Issue" in task["feedback"], "review feedback missing")

        applied = apply_event(manifest, generated)
        require(applied["outcome"] == "APPLIED", f"generated remediation Event did not apply: {applied}")
        result = applied["manifest"]
        require(result["revision"] == 3, "remediation Event did not increment revision")
        require(next(s for s in result["workflow"]["stages"] if s["id"] == "implementation")["status"] == "DONE", "collector reopened implementation stage")
        require(next(s for s in result["workflow"]["stages"] if s["id"] == "review")["status"] == "TODO", "collector completed review stage")
        require(next(g for g in result["gates"] if g["id"] == "code-gate")["status"] == "PENDING", "collector changed code-gate")
        require(result["tasks"][0]["status"] == "TODO", "remediation task was not persisted")

        cases = [
            (review_event(state="approved"), "only a CHANGES_REQUESTED"),
            (review_event(body=""), "concrete feedback"),
            (review_event(reviewer="github-actions[bot]"), "independent non-author"),
        ]
        fork = review_event()
        fork["pull_request"]["head"]["repo"]["full_name"] = "attacker/fork"
        cases.append((fork, "fork/cross-repository"))
        default_target = review_event()
        default_target["pull_request"]["base"]["ref"] = "main"
        cases.append((default_target, "non-default"))

        for payload, marker in cases:
            try:
                build_event(payload, workspace, ROOT, "DREAM-XIN/ai-sdlc", "main")
            except SystemExit as exc:
                require(marker in str(exc), f"fail-closed diagnostic missing {marker!r}: {exc}")
            else:
                raise AssertionError(f"invalid review payload unexpectedly passed: {marker}")

    print("Trusted PR review remediation Event collector checks passed")


if __name__ == "__main__":
    main()
