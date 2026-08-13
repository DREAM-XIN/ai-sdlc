#!/usr/bin/env python3
"""Exact-commit bindings for v0.3 Vertical policy state."""
from operator_store_model import normalize_repository


def exact_default_branch_ref(repository: str, commit_sha: str, path: str) -> str:
    repository = normalize_repository(repository)
    sha = commit_sha.lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("exact installation commit SHA is required")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError("invalid trusted policy path")
    return f"default-branch://{repository}@{sha}/{path}"
