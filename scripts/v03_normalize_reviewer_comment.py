#!/usr/bin/env python3
"""Trusted normalization for the fixed v0.3 local Reviewer Safe Output comment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path

_GATE_START = "<!-- AI-SDLC-GATE-RESULT\n"
_GATE_END = "\nAI-SDLC-GATE-RESULT -->"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VERDICT_RE = re.compile(
    r"""(?mi)^\s*["']?verdict["']?\s*:\s*["']?(PASS|REWORK|BLOCKED)["']?\s*,?\s*$"""
)


class ReviewerCommentNormalizationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewerCommentNormalizationError(message)


def normalize_reviewer_comment(
    raw_body: str,
    *,
    feature_id: str,
    task_id: str,
    expected_revision: int,
    target_repository: str,
    target_ref: str,
    candidate_pr_number: int,
    candidate_head_sha: str,
    comment_url: str,
    occurred_at: str | None = None,
) -> str:
    """Build the closed Gate envelope from trusted identities plus one bounded verdict."""

    _require(isinstance(raw_body, str), "raw comment body must be text")
    _require(bool(feature_id), "feature id is required")
    _require(bool(task_id), "task id is required")
    _require(isinstance(expected_revision, int) and expected_revision >= 0, "expected revision is invalid")
    _require(bool(_REPOSITORY_RE.fullmatch(target_repository)), "target repository is invalid")
    _require(bool(target_ref), "target ref is required")
    _require(isinstance(candidate_pr_number, int) and candidate_pr_number > 0, "candidate PR number is invalid")
    _require(bool(_SHA_RE.fullmatch(candidate_head_sha)), "candidate head SHA is invalid")
    _require(comment_url.startswith("https://github.com/"), "comment URL must be GitHub HTTPS")

    matches = _VERDICT_RE.findall(raw_body)
    if len(matches) == 1:
        verdict = matches[0].upper()
        parse_reason = None
    else:
        verdict = "BLOCKED"
        parse_reason = "Reviewer Safe Output did not contain exactly one standalone verdict field."

    evidence_status = "pass" if verdict == "PASS" else "fail" if verdict == "REWORK" else "warning"
    timestamp = occurred_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "version": "0.1.0",
        "contract": "ai-sdlc-gh-aw-reviewer-result-v0.1",
        "id": f"vertical:code-review:{candidate_head_sha}",
        "feature_id": feature_id,
        "task_id": task_id,
        "stage": "code-review",
        "role": "reviewer",
        "expected_revision": expected_revision,
        "target_repository": target_repository.lower(),
        "target_ref": target_ref,
        "candidate_pr_number": candidate_pr_number,
        "candidate_head_sha": candidate_head_sha,
        "verdict": verdict,
        "findings": [],
        "evidence": [
            {
                "id": f"review-comment-{candidate_pr_number}",
                "type": "review",
                "status": evidence_status,
                "uri": comment_url,
            }
        ],
        "occurred_at": timestamp,
    }
    if verdict != "PASS":
        payload["reason"] = parse_reason or f"Reviewer recommended {verdict}; inspect the original Safe Output comment."

    machine = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    summary = f"Trusted v0.3 Reviewer transport normalized the non-authoritative recommendation: {verdict}."
    return _GATE_START + machine + _GATE_END + "\n" + summary + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-comment", required=True)
    parser.add_argument("--feature-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--target-repository", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--candidate-pr-number", required=True, type=int)
    parser.add_argument("--candidate-head-sha", required=True)
    parser.add_argument("--comment-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    raw = Path(args.raw_comment).read_text(encoding="utf-8")
    normalized = normalize_reviewer_comment(
        raw,
        feature_id=args.feature_id,
        task_id=args.task_id,
        expected_revision=args.expected_revision,
        target_repository=args.target_repository,
        target_ref=args.target_ref,
        candidate_pr_number=args.candidate_pr_number,
        candidate_head_sha=args.candidate_head_sha,
        comment_url=args.comment_url,
    )
    Path(args.output).write_text(normalized, encoding="utf-8")


if __name__ == "__main__":
    main()
