#!/usr/bin/env python3
"""Build bounded review/verification Feature Events from trusted PR evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import yaml


def fail(message: str) -> None:
    raise SystemExit(message)


def load_manifest(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail("manifest must be a YAML object")
    return data


def stage_status(manifest: dict, stage_id: str) -> str | None:
    for stage in manifest.get("workflow", {}).get("stages", []):
        if stage.get("id") == stage_id:
            return stage.get("status")
    return None


def build_review_event(manifest: dict, *, pr_number: int, pr_url: str, review_url: str, ci_url: str, occurred_at: str) -> dict:
    feature_id = manifest["feature"]["id"]
    revision = manifest.get("revision", 0)
    review_status = stage_status(manifest, "review")
    implementation_status = stage_status(manifest, "implementation")
    verification_status = stage_status(manifest, "verification")
    if implementation_status != "DONE":
        fail("implementation stage must be DONE before automatic review completion")
    if review_status not in {"TODO", "READY", "WORKING"}:
        fail(f"review stage must be TODO, READY, or WORKING; found {review_status!r}")
    if verification_status not in {"TODO", "READY"}:
        fail(f"verification stage must not already be active; found {verification_status!r}")

    review_evidence = f"EVID-PR{pr_number}-REVIEW"
    ci_evidence = f"EVID-PR{pr_number}-CI"
    changes: list[dict] = [
        {"kind": "evidence", "record": {"id": review_evidence, "type": "code-review", "status": "pass", "uri": review_url}},
        {"kind": "evidence", "record": {"id": ci_evidence, "type": "ci-run", "status": "pass", "uri": ci_url}},
    ]
    if review_status != "WORKING":
        changes.append({"kind": "stage", "id": "review", "status": "WORKING"})
    changes.extend(
        [
            {"kind": "stage", "id": "review", "status": "DONE"},
            {"kind": "gate", "id": "code-gate", "status": "PASS", "evidence": [review_evidence, ci_evidence]},
            {"kind": "stage", "id": "verification", "status": "WORKING"},
        ]
    )
    return {
        "version": "0.1.0",
        "id": f"EVT-{feature_id}-PR{pr_number}-REVIEW-DONE",
        "feature_id": feature_id,
        "expected_revision": revision,
        "occurred_at": occurred_at,
        "changes": changes,
    }


def build_verification_event(manifest: dict, *, pr_number: int, pr_url: str, ci_url: str, occurred_at: str) -> dict:
    feature_id = manifest["feature"]["id"]
    revision = manifest.get("revision", 0)
    if stage_status(manifest, "review") != "DONE":
        fail("review stage must be DONE before automatic verification completion")
    if stage_status(manifest, "verification") != "WORKING":
        fail("verification stage must be WORKING before automatic verification completion")

    ci_evidence = f"EVID-PR{pr_number}-CI"
    verification_evidence = f"EVID-PR{pr_number}-VERIFICATION"
    return {
        "version": "0.1.0",
        "id": f"EVT-{feature_id}-PR{pr_number}-VERIFICATION-DONE",
        "feature_id": feature_id,
        "expected_revision": revision,
        "occurred_at": occurred_at,
        "changes": [
            {"kind": "evidence", "record": {"id": verification_evidence, "type": "verification", "status": "pass", "uri": ci_url}},
            {"kind": "stage", "id": "verification", "status": "DONE"},
            {"kind": "gate", "id": "verification-gate", "status": "PASS", "evidence": [verification_evidence, ci_evidence]},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--phase", choices=("review", "verification"), required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--pr-url", required=True)
    parser.add_argument("--review-url", default="")
    parser.add_argument("--ci-url", required=True)
    parser.add_argument("--occurred-at")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.pr_number <= 0:
        fail("pr-number must be positive")
    occurred_at = args.occurred_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = load_manifest(args.manifest)
    if args.phase == "review":
        if not args.review_url:
            fail("review phase requires --review-url")
        event = build_review_event(
            manifest,
            pr_number=args.pr_number,
            pr_url=args.pr_url,
            review_url=args.review_url,
            ci_url=args.ci_url,
            occurred_at=occurred_at,
        )
    else:
        event = build_verification_event(
            manifest,
            pr_number=args.pr_number,
            pr_url=args.pr_url,
            ci_url=args.ci_url,
            occurred_at=occurred_at,
        )
    args.output.write_text(yaml.safe_dump(event, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main()
