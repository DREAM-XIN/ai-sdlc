#!/usr/bin/env python3
"""Resolve immutable implementation candidates for autonomous Gate roles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

PR_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)$")
COMMIT_RE = re.compile(r"^/([^/]+)/([^/]+)/commit/([0-9a-f]{40})$")
CANDIDATE_PREFIX = "implementation-candidate-"
HEAD_PREFIX = "implementation-head-"


class CandidateError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    artifact_id: str
    head_artifact_id: str
    repository: str
    pr_number: int
    pr_url: str
    head_sha: str
    head_url: str
    status: str


def _github_parts(uri: str, pattern: re.Pattern[str], label: str):
    parsed = urlparse(uri)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        raise CandidateError(f"{label} must be a canonical https://github.com URL")
    match = pattern.match(parsed.path)
    if not match:
        raise CandidateError(f"invalid {label} URL: {uri}")
    return match.groups()


def build_candidate_artifacts(repository: str, pr_number: int, head_sha: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise CandidateError("repository must be owner/repo")
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise CandidateError("pr_number must be a positive integer")
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise CandidateError("head_sha must be a lowercase 40-character git SHA")
    suffix = head_sha[:12]
    return [
        {
            "id": f"{CANDIDATE_PREFIX}{suffix}",
            "type": "implementation",
            "uri": f"https://github.com/{repository}/pull/{pr_number}",
            "status": "draft",
        },
        {
            "id": f"{HEAD_PREFIX}{suffix}",
            "type": "implementation-head",
            "uri": f"https://github.com/{repository}/commit/{head_sha}",
            "status": "draft",
        },
    ]


def resolve_current_candidate(manifest: dict, *, status: str) -> Candidate:
    if status not in {"draft", "approved"}:
        raise CandidateError("candidate status must be draft or approved")
    artifacts = [item for item in manifest.get("artifacts", []) if isinstance(item, dict)]
    candidates = [
        item for item in artifacts
        if item.get("type") == "implementation"
        and item.get("status") == status
        and str(item.get("id", "")).startswith(CANDIDATE_PREFIX)
    ]
    if len(candidates) != 1:
        raise CandidateError(
            f"expected exactly one current {status} implementation candidate, found {len(candidates)}"
        )

    candidate = candidates[0]
    owner, repo, pr_number = _github_parts(candidate["uri"], PR_RE, "candidate PR")
    suffix = candidate["id"][len(CANDIDATE_PREFIX):]
    head_id = f"{HEAD_PREFIX}{suffix}"
    heads = [
        item for item in artifacts
        if item.get("type") == "implementation-head"
        and item.get("status") == status
        and item.get("id") == head_id
    ]
    if len(heads) != 1:
        raise CandidateError(
            f"candidate must have exactly one matching {status} implementation-head artifact"
        )
    head = heads[0]
    h_owner, h_repo, head_sha = _github_parts(head["uri"], COMMIT_RE, "candidate head")
    if (owner, repo) != (h_owner, h_repo):
        raise CandidateError("candidate PR and head repository identities differ")
    if not head_sha.startswith(suffix):
        raise CandidateError("candidate artifact id is not bound to its head SHA")
    return Candidate(
        artifact_id=candidate["id"],
        head_artifact_id=head_id,
        repository=f"{owner}/{repo}",
        pr_number=int(pr_number),
        pr_url=candidate["uri"],
        head_sha=head_sha,
        head_url=head["uri"],
        status=status,
    )


def candidate_artifact_changes(manifest: dict, repository: str, pr_number: int, head_sha: str):
    new_artifacts = build_candidate_artifacts(repository, pr_number, head_sha)
    changes = []
    for item in manifest.get("artifacts", []):
        if (
            isinstance(item, dict)
            and item.get("status") == "draft"
            and (
                str(item.get("id", "")).startswith(CANDIDATE_PREFIX)
                or str(item.get("id", "")).startswith(HEAD_PREFIX)
            )
        ):
            changes.append({"kind": "artifact", "id": item["id"], "status": "superseded"})
    changes.extend({"kind": "artifact-record", "record": item} for item in new_artifacts)
    return changes
