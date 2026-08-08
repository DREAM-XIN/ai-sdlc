#!/usr/bin/env python3
"""Resolve a trusted same-repository AI-SDLC PR to one Feature and green CI evidence."""

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


def resolve(event: dict, checks: dict, workspace: Path, repository: str, default_branch: str, required_check: str) -> dict[str, str]:
    review = event.get("review") or {}
    pr = event.get("pull_request") or {}
    if str(review.get("state", "")).lower() != "approved":
        fail("only an APPROVED pull_request_review is eligible")

    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    if head_repo != repository:
        fail("fork/cross-repository PRs are not eligible for privileged lifecycle persistence")

    head_ref = str(head.get("ref", ""))
    base_ref = str(base.get("ref", ""))
    if not head_ref.startswith("gh-aw/"):
        fail("eligible work PR head ref must start with gh-aw/")
    if not base_ref or base_ref == default_branch:
        fail("eligible work PR must target a non-default Feature base branch")

    manifests: list[tuple[str, Path]] = []
    for path in sorted((workspace / "state" / "features").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        feature_id = str((data.get("feature") or {}).get("id", ""))
        if feature_id and feature_id in head_ref:
            manifests.append((feature_id, path))
    if len(manifests) != 1:
        fail(f"PR head ref must uniquely map to one Feature Manifest; found {len(manifests)}")
    feature_id, manifest = manifests[0]

    check_runs = checks.get("check_runs") or []
    matching = [run for run in check_runs if run.get("name") == required_check]
    successful = [run for run in matching if run.get("status") == "completed" and run.get("conclusion") == "success"]
    if len(successful) != 1:
        fail(f"required check {required_check!r} must have exactly one successful completed run; found {len(successful)}")
    ci = successful[0]
    ci_url = str(ci.get("details_url") or ci.get("html_url") or "")
    if not ci_url.startswith("https://"):
        fail("successful required check is missing a trusted https evidence URL")

    pr_number = pr.get("number") or event.get("number")
    if not isinstance(pr_number, int) or pr_number <= 0:
        fail("pull request number is missing or invalid")
    pr_url = str(pr.get("html_url") or "")
    review_url = str(review.get("html_url") or "")
    head_sha = str(head.get("sha") or "")
    if not pr_url.startswith("https://") or not review_url.startswith("https://") or len(head_sha) != 40:
        fail("PR/review/head evidence metadata is incomplete")

    return {
        "feature_id": feature_id,
        "manifest_path": manifest.relative_to(workspace).as_posix(),
        "base_ref": base_ref,
        "head_sha": head_sha,
        "pr_number": str(pr_number),
        "pr_url": pr_url,
        "review_url": review_url,
        "ci_url": ci_url,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--required-check", default="validate")
    parser.add_argument("--github-output")
    args = parser.parse_args()

    result = resolve(
        load_json(args.event),
        load_json(args.checks),
        args.workspace,
        args.repository,
        args.default_branch,
        args.required_check,
    )
    lines = [f"{key}={value}" for key, value in result.items()]
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
