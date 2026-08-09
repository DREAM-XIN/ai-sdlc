#!/usr/bin/env python3
"""Validate manual candidate binding and multi-round candidate history semantics."""

from __future__ import annotations

from gh_aw_candidate import CandidateError, resolve_current_candidate
from gh_aw_gate_result import reviewer_event

REPO = "DREAM-XIN/example"
REF = "feature/F-EXAMPLE-0001"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def stage_manifest(artifacts):
    return {
        "revision": 11,
        "feature": {"id": "F-EXAMPLE-0001"},
        "workflow": {
            "stages": [
                {"id": "implementation", "status": "DONE"},
                {"id": "code-review", "status": "WORKING", "gate": "code-gate"},
                {"id": "verification", "status": "TODO", "gate": "verification-gate"},
                {"id": "acceptance", "status": "TODO", "gate": "release-gate"},
            ]
        },
        "artifacts": artifacts,
    }


def reviewer_result(pr_number: int, sha: str):
    return {
        "version": "0.1.0",
        "contract": "ai-sdlc-gh-aw-reviewer-result-v0.1",
        "id": "GHAW-REVIEW-HISTORY",
        "feature_id": "F-EXAMPLE-0001",
        "task_id": "F-EXAMPLE-0001-code-review",
        "stage": "code-review",
        "role": "reviewer",
        "expected_revision": 11,
        "target_repository": REPO,
        "target_ref": REF,
        "candidate_pr_number": pr_number,
        "candidate_head_sha": sha,
        "verdict": "PASS",
        "findings": [],
        "evidence": [{
            "id": "evidence-review-history",
            "type": "review",
            "status": "pass",
            "uri": "https://github.com/DREAM-XIN/example/pull/8#issuecomment-1",
        }],
        "occurred_at": "2026-08-09T14:00:00Z",
    }


def validate_manual_candidate():
    artifacts = [
        {
            "id": "implementation-v1",
            "type": "implementation",
            "uri": f"https://github.com/{REPO}/pull/7",
            "status": "draft",
        },
        {
            "id": "manual-implementation-head",
            "type": "implementation-head",
            "uri": f"https://github.com/{REPO}/commit/{OLD_SHA}",
            "status": "draft",
        },
    ]
    candidate = resolve_current_candidate(stage_manifest(artifacts), status="draft")
    require(candidate.artifact_id == "implementation-v1", "manual implementation artifact id was hard-coded away")
    require(candidate.head_artifact_id == "manual-implementation-head", "manual implementation head was not resolved")
    require(candidate.pr_number == 7 and candidate.head_sha == OLD_SHA, "manual PR/head binding drifted")

    documentation_only = stage_manifest([{
        "id": "implementation-v1",
        "type": "implementation",
        "uri": "docs/features/F-EXAMPLE-0001/implementation.md",
        "status": "draft",
    }])
    try:
        resolve_current_candidate(documentation_only, status="draft")
        raise AssertionError("documentation-only implementation was guessed as an autonomous PR candidate")
    except CandidateError:
        pass


def validate_multi_round_supersession():
    artifacts = [
        {
            "id": "implementation-candidate-aaaaaaaaaaaa",
            "type": "implementation",
            "uri": f"https://github.com/{REPO}/pull/7",
            "status": "approved",
        },
        {
            "id": "implementation-head-aaaaaaaaaaaa",
            "type": "implementation-head",
            "uri": f"https://github.com/{REPO}/commit/{OLD_SHA}",
            "status": "approved",
        },
        {
            "id": "reviewed-candidate-head-aaaaaaaaaaaa",
            "type": "reviewed-candidate-head",
            "uri": f"https://github.com/{REPO}/commit/{OLD_SHA}",
            "status": "approved",
        },
        {
            "id": "implementation-candidate-bbbbbbbbbbbb",
            "type": "implementation",
            "uri": f"https://github.com/{REPO}/pull/8",
            "status": "draft",
        },
        {
            "id": "implementation-head-bbbbbbbbbbbb",
            "type": "implementation-head",
            "uri": f"https://github.com/{REPO}/commit/{NEW_SHA}",
            "status": "draft",
        },
    ]
    manifest = stage_manifest(artifacts)
    event = reviewer_event(
        reviewer_result(8, NEW_SHA),
        manifest,
        repository=REPO,
        target_ref=REF,
        current_pr_head_sha=NEW_SHA,
    )
    superseded = {
        change["id"] for change in event["changes"]
        if change.get("kind") == "artifact" and change.get("status") == "superseded"
    }
    require("implementation-candidate-aaaaaaaaaaaa" in superseded, "old approved implementation candidate stayed current")
    require("implementation-head-aaaaaaaaaaaa" in superseded, "old approved implementation head stayed current")
    require("reviewed-candidate-head-aaaaaaaaaaaa" in superseded, "old reviewed head stayed current")
    require("implementation-candidate-bbbbbbbbbbbb" not in superseded, "new candidate was superseded before approval")


if __name__ == "__main__":
    validate_manual_candidate()
    validate_multi_round_supersession()
    print("gh-aw candidate history and manual compatibility validation passed")
