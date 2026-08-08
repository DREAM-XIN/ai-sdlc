#!/usr/bin/env python3
"""Convert trusted REQUEST_CHANGES review evidence into a remediation task Event."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def fail(message: str) -> None:
    raise SystemExit(message)


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail(f"{path} must contain a JSON object")
    return data


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail(f"{path} must contain a YAML object")
    return data


def resolve_manifest(workspace: Path, head_ref: str) -> tuple[str, Path, dict]:
    matches: list[tuple[str, Path, dict]] = []
    for path in sorted((workspace / "state" / "features").glob("*.yaml")):
        data = load_yaml(path)
        feature_id = str((data.get("feature") or {}).get("id", ""))
        if feature_id and feature_id in head_ref:
            matches.append((feature_id, path, data))
    if len(matches) != 1:
        fail(f"PR head ref must uniquely map to one Feature Manifest; found {len(matches)}")
    return matches[0]


def build_event(event: dict, workspace: Path, runtime_root: Path, repository: str, default_branch: str) -> tuple[dict, dict[str, str]]:
    review = event.get("review") or {}
    pr = event.get("pull_request") or {}
    if str(review.get("state", "")).lower() != "changes_requested":
        fail("only a CHANGES_REQUESTED pull_request_review is eligible")

    head = pr.get("head") or {}
    base = pr.get("base") or {}
    if (head.get("repo") or {}).get("full_name") != repository:
        fail("fork/cross-repository PRs are not eligible for privileged remediation persistence")
    head_ref = str(head.get("ref", ""))
    base_ref = str(base.get("ref", ""))
    if not head_ref.startswith("gh-aw/"):
        fail("eligible work PR head ref must start with gh-aw/")
    if not base_ref or base_ref == default_branch:
        fail("eligible work PR must target a non-default Feature base branch")

    reviewer = str((review.get("user") or {}).get("login") or "")
    author = str((pr.get("user") or {}).get("login") or "")
    if not reviewer or reviewer == author:
        fail("remediation review must come from an independent non-author reviewer")
    feedback = str(review.get("body") or "").strip()
    if not feedback:
        fail("CHANGES_REQUESTED review must contain concrete feedback")
    review_id = review.get("id")
    if not isinstance(review_id, int) or review_id <= 0:
        fail("review id is missing or invalid")
    review_url = str(review.get("html_url") or "")
    pr_url = str(pr.get("html_url") or "")
    pr_number = pr.get("number") or event.get("number")
    if not isinstance(pr_number, int) or pr_number <= 0:
        fail("pull request number is missing or invalid")
    if not review_url.startswith("https://") or not pr_url.startswith("https://"):
        fail("PR/review evidence URLs must be trusted https URLs")

    feature_id, manifest_path, manifest = resolve_manifest(workspace, head_ref)
    source_stage = str((manifest.get("workflow") or {}).get("current_stage") or "")
    stages = {item["id"]: item for item in (manifest.get("workflow") or {}).get("stages", [])}
    if source_stage not in stages or stages[source_stage].get("status") in {"DONE", "SKIPPED"}:
        fail("Feature is not waiting in an unfinished review stage")

    profile_id = str((manifest.get("workflow") or {}).get("profile") or "")
    profile_path = runtime_root / "profiles" / f"{profile_id}.yaml"
    if not profile_path.is_file():
        fail(f"trusted workflow profile not found: {profile_id}")
    profile = load_yaml(profile_path)
    profile_stages = {item["id"]: item for item in profile.get("stages", [])}
    source_profile = profile_stages.get(source_stage)
    if not source_profile:
        fail(f"current stage {source_stage} is missing from trusted profile {profile_id}")
    dependencies = list(source_profile.get("depends_on") or [])
    eligible_targets = [stage_id for stage_id in dependencies if stage_id in stages and stages[stage_id].get("status") == "DONE"]
    if len(eligible_targets) != 1:
        fail(f"review stage must have exactly one completed direct work dependency; found {len(eligible_targets)}")
    target_stage = eligible_targets[0]
    target_profile = profile_stages.get(target_stage) or {}
    role = str(target_profile.get("role") or "")
    if not role:
        fail(f"trusted profile does not define a role for remediation target stage {target_stage}")

    revision = manifest.get("revision", 0)
    if not isinstance(revision, int) or revision < 0:
        fail("Feature Manifest revision is invalid")
    task_id = f"{feature_id}-REMEDIATION-R{review_id}"
    event_id = f"EVT-{feature_id}-REMEDIATION-R{review_id}"
    task_record = {
        "id": task_id,
        "kind": "remediation",
        "stage": target_stage,
        "role": role,
        "source_stage": source_stage,
        "feedback": feedback,
        "target_pr": pr_url,
        "status": "TODO",
        "runtime": "gh-aw",
    }
    feature_issue = str((manifest.get("feature") or {}).get("issue") or "")
    if feature_issue:
        task_record["issue"] = feature_issue

    remediation_event = {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": feature_id,
        "expected_revision": revision,
        "occurred_at": str(review.get("submitted_at") or ""),
        "changes": [
            {"kind": "evidence", "record": {"id": f"EVID-PR-REVIEW-{review_id}", "type": "pr-review", "status": "warning", "uri": review_url}},
            {"kind": "task-record", "record": task_record},
        ],
    }
    if not remediation_event["occurred_at"]:
        fail("review submitted_at is required for durable remediation Event ordering")

    metadata = {
        "feature_id": feature_id,
        "manifest_path": manifest_path.relative_to(workspace).as_posix(),
        "base_ref": base_ref,
        "pr_number": str(pr_number),
        "pr_url": pr_url,
        "review_url": review_url,
        "task_id": task_id,
        "event_id": event_id,
        "target_stage": target_stage,
        "source_stage": source_stage,
    }
    return remediation_event, metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, default=Path("."))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    remediation_event, metadata = build_event(
        load_json(args.event), args.workspace, args.runtime_root, args.repository, args.default_branch
    )
    args.output.write_text(yaml.safe_dump(remediation_event, sort_keys=False), encoding="utf-8")
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            for key, value in metadata.items():
                handle.write(f"{key}={value}\n")
    else:
        print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
