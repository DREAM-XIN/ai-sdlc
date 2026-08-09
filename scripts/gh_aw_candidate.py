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
REVIEWED_HEAD_PREFIX = "reviewed-candidate-head-"
VERIFIED_HEAD_PREFIX = "verified-candidate-head-"


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


def _candidate_pr(item: dict):
    if item.get("type") != "implementation":
        return None
    try:
        return _github_parts(item["uri"], PR_RE, "candidate PR")
    except (KeyError, CandidateError):
        return None


def resolve_current_candidate(manifest: dict, *, status: str) -> Candidate:
    """Resolve one current candidate without hard-coding an implementation artifact id.

    Autonomous candidates use the deterministic implementation-candidate/head id pair.
    A manual candidate is also eligible when its implementation artifact is explicitly
    bound to a canonical PR URL and exactly one same-status implementation-head artifact
    in the same repository supplies the immutable SHA. Documentation-only implementation
    artifacts are intentionally not inferred as PR candidates.
    """
    if status not in {"draft", "approved"}:
        raise CandidateError("candidate status must be draft or approved")
    artifacts = [item for item in manifest.get("artifacts", []) if isinstance(item, dict)]
    candidates = [
        item for item in artifacts
        if item.get("status") == status and _candidate_pr(item) is not None
    ]
    if len(candidates) != 1:
        raise CandidateError(
            f"expected exactly one current {status} PR-bound implementation candidate, found {len(candidates)}"
        )

    candidate = candidates[0]
    owner, repo, pr_number = _github_parts(candidate["uri"], PR_RE, "candidate PR")
    candidate_id = str(candidate.get("id", ""))
    heads = []
    if candidate_id.startswith(CANDIDATE_PREFIX):
        suffix = candidate_id[len(CANDIDATE_PREFIX):]
        head_id = f"{HEAD_PREFIX}{suffix}"
        heads = [
            item for item in artifacts
            if item.get("type") == "implementation-head"
            and item.get("status") == status
            and item.get("id") == head_id
        ]
    else:
        # Manual candidate compatibility is explicit, not inferred from a file artifact:
        # the implementation artifact must already point at the candidate PR, and exactly
        # one current head artifact in that same repository supplies immutable identity.
        for item in artifacts:
            if item.get("type") != "implementation-head" or item.get("status") != status:
                continue
            try:
                h_owner, h_repo, _ = _github_parts(item["uri"], COMMIT_RE, "candidate head")
            except (KeyError, CandidateError):
                continue
            if (h_owner, h_repo) == (owner, repo):
                heads.append(item)
        head_id = heads[0]["id"] if len(heads) == 1 else ""

    if len(heads) != 1:
        raise CandidateError(
            f"candidate must have exactly one matching {status} implementation-head artifact"
        )
    head = heads[0]
    h_owner, h_repo, head_sha = _github_parts(head["uri"], COMMIT_RE, "candidate head")
    if (owner, repo) != (h_owner, h_repo):
        raise CandidateError("candidate PR and head repository identities differ")
    if candidate_id.startswith(CANDIDATE_PREFIX):
        suffix = candidate_id[len(CANDIDATE_PREFIX):]
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


def _is_candidate_history_artifact(item: dict) -> bool:
    artifact_id = str(item.get("id", ""))
    artifact_type = item.get("type")
    return (
        (artifact_type == "implementation" and artifact_id.startswith("implementation-"))
        or artifact_type == "implementation-head"
        or artifact_type == "reviewed-candidate-head"
        or artifact_type == "verified-candidate-head"
    )


def supersede_other_current_candidate_artifacts(manifest: dict, *, keep_ids: set[str]):
    """Preserve history while making the newly approved candidate tuple uniquely current."""
    changes = []
    for item in manifest.get("artifacts", []):
        if not isinstance(item, dict) or item.get("id") in keep_ids:
            continue
        if item.get("status") not in {"draft", "approved"}:
            continue
        if _is_candidate_history_artifact(item):
            changes.append({"kind": "artifact", "id": item["id"], "status": "superseded"})
    return changes


def candidate_artifact_changes(manifest: dict, repository: str, pr_number: int, head_sha: str):
    new_artifacts = build_candidate_artifacts(repository, pr_number, head_sha)
    changes = []
    for item in manifest.get("artifacts", []):
        if (
            isinstance(item, dict)
            and item.get("status") == "draft"
            and _is_candidate_history_artifact(item)
        ):
            changes.append({"kind": "artifact", "id": item["id"], "status": "superseded"})
    changes.extend({"kind": "artifact-record", "record": item} for item in new_artifacts)
    return changes
