#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml

from pr_lifecycle_event import build_review_event, build_verification_event
from resolve_pr_lifecycle_context import resolve


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def base_manifest(feature_id="F-TEST-0001"):
    return {
        "protocol_version": "0.1.0",
        "revision": 4,
        "feature": {"id": feature_id, "title": "test", "risk": "low"},
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
        "applied_events": [],
        "updated_at": "2026-08-08T06:00:00Z",
    }


def event_payload(repository="DREAM-XIN/ai-sdlc", base_ref="feature/F-TEST-0001", head_ref="gh-aw/F-TEST-0001-123-v1"):
    return {
        "number": 12,
        "review": {"state": "approved", "html_url": "https://github.com/DREAM-XIN/ai-sdlc/pull/12#pullrequestreview-1"},
        "pull_request": {
            "number": 12,
            "html_url": "https://github.com/DREAM-XIN/ai-sdlc/pull/12",
            "head": {"ref": head_ref, "sha": "a" * 40, "repo": {"full_name": repository}},
            "base": {"ref": base_ref},
        },
    }


def checks(conclusion="success"):
    return {
        "check_runs": [
            {"name": "validate", "status": "completed", "conclusion": conclusion, "details_url": "https://github.com/DREAM-XIN/ai-sdlc/actions/runs/123"}
        ]
    }


def expect_failure(fn, needle):
    try:
        fn()
    except SystemExit as exc:
        require(needle in str(exc), f"expected {needle!r} in {exc!r}")
    else:
        raise AssertionError(f"expected failure containing {needle!r}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        feature_dir = workspace / "state" / "features"
        feature_dir.mkdir(parents=True)
        (feature_dir / "F-TEST-0001.yaml").write_text(yaml.safe_dump(base_manifest(), sort_keys=False), encoding="utf-8")

        context = resolve(event_payload(), checks(), workspace, "DREAM-XIN/ai-sdlc", "main", "validate")
        require(context["feature_id"] == "F-TEST-0001", "feature mapping failed")
        require(context["base_ref"] == "feature/F-TEST-0001", "base ref mapping failed")

        expect_failure(lambda: resolve(event_payload(repository="OTHER/repo"), checks(), workspace, "DREAM-XIN/ai-sdlc", "main", "validate"), "fork/cross-repository")
        expect_failure(lambda: resolve(event_payload(base_ref="main"), checks(), workspace, "DREAM-XIN/ai-sdlc", "main", "validate"), "non-default")
        expect_failure(lambda: resolve(event_payload(head_ref="feature/F-TEST-0001"), checks(), workspace, "DREAM-XIN/ai-sdlc", "main", "validate"), "gh-aw/")
        expect_failure(lambda: resolve(event_payload(), checks("failure"), workspace, "DREAM-XIN/ai-sdlc", "main", "validate"), "successful completed")

        (feature_dir / "F-TEST.yaml").write_text(yaml.safe_dump(base_manifest("F-TEST"), sort_keys=False), encoding="utf-8")
        expect_failure(lambda: resolve(event_payload(), checks(), workspace, "DREAM-XIN/ai-sdlc", "main", "validate"), "uniquely map")

    manifest = base_manifest()
    review = build_review_event(
        manifest,
        pr_number=12,
        pr_url="https://github.com/DREAM-XIN/ai-sdlc/pull/12",
        review_url="https://github.com/DREAM-XIN/ai-sdlc/pull/12#pullrequestreview-1",
        ci_url="https://github.com/DREAM-XIN/ai-sdlc/actions/runs/123",
        occurred_at="2026-08-08T06:00:00Z",
    )
    require(review["expected_revision"] == 4, "review event revision mismatch")
    require(any(c.get("id") == "code-gate" and c.get("status") == "PASS" for c in review["changes"]), "review event does not pass code-gate")
    require(review["changes"][-1] == {"kind": "stage", "id": "verification", "status": "WORKING"}, "review event does not start verification")

    verification_manifest = base_manifest()
    verification_manifest["revision"] = 5
    verification_manifest["workflow"]["stages"][2]["status"] = "DONE"
    verification_manifest["workflow"]["stages"][3]["status"] = "WORKING"
    verification = build_verification_event(
        verification_manifest,
        pr_number=12,
        pr_url="https://github.com/DREAM-XIN/ai-sdlc/pull/12",
        ci_url="https://github.com/DREAM-XIN/ai-sdlc/actions/runs/123",
        occurred_at="2026-08-08T06:01:00Z",
    )
    require(verification["expected_revision"] == 5, "verification event revision mismatch")
    require(any(c.get("id") == "verification-gate" and c.get("status") == "PASS" for c in verification["changes"]), "verification event does not pass verification-gate")

    print("Trusted PR lifecycle context and Feature Event boundary checks passed")


if __name__ == "__main__":
    main()
