#!/usr/bin/env python3
"""Trusted-main-only provisioner for the closed #310 v0.3 scenario fixture pool."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from v03_scenario_fixture_pool import (
    POOL_PROFILE,
    SLOTS,
    SlotSpec,
    inventory_document,
    materialize_activation,
    materialize_bootstrap,
    validate_inventory,
    verify_active_files,
    verify_bootstrap_files,
)

EVIDENCE_ROOT = Path("evidence/v03-scenario-fixture-pool")
POOL_RECEIPT = EVIDENCE_ROOT / "pool.json"
EXPECTED_REF = "refs/heads/main"
BOOTSTRAP_PREFIX = "test(v0.3): bootstrap #310 fixture "
ACTIVATION_PREFIX = "test(v0.3): activate #310 fixture "


class FixturePoolProvisionError(RuntimeError):
    pass


def _run(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise FixturePoolProvisionError(
            f"command failed ({' '.join(args[:3])}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def _git(repo_dir: Path, *args: str) -> str:
    return _run(["git", *args], cwd=repo_dir).stdout.strip()


def _exact_sha(value: str, label: str) -> str:
    value = value.strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise FixturePoolProvisionError(f"{label} is not an exact Git SHA")
    return value


def _remote_head(repo_dir: Path, ref: str) -> str | None:
    result = _run(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{ref}"],
        cwd=repo_dir,
    ).stdout.strip()
    if not result:
        return None
    rows = [line.split() for line in result.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != f"refs/heads/{ref}":
        raise FixturePoolProvisionError(f"ambiguous remote ref for fixed slot: {ref}")
    return _exact_sha(rows[0][0], f"remote head {ref}")


def _parents(repo_dir: Path, sha: str) -> list[str]:
    parts = _git(repo_dir, "rev-list", "--parents", "-n", "1", sha).split()
    if not parts or parts[0] != sha:
        raise FixturePoolProvisionError("cannot reconstruct fixture commit parents")
    return parts[1:]


def _changed_paths(repo_dir: Path, base: str, head: str) -> tuple[str, ...]:
    return tuple(sorted(filter(None, _git(repo_dir, "diff", "--name-only", base, head).splitlines())))


def _commit_subject(repo_dir: Path, sha: str) -> str:
    return _git(repo_dir, "show", "-s", "--format=%s", sha)


def _require_ancestor(repo_dir: Path, ancestor: str, descendant: str, label: str) -> None:
    result = _run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo_dir, check=False)
    if result.returncode != 0:
        raise FixturePoolProvisionError(f"{label} is not trusted-main ancestry")


def _configure_identity(worktree: Path) -> None:
    _git(worktree, "config", "user.name", "AI-SDLC Trusted Fixture Provisioner")
    _git(worktree, "config", "user.email", "trusted-fixture-provisioner@ai-sdlc.invalid")


def _commit_exact(worktree: Path, *, paths: tuple[str, ...], message: str) -> str:
    _run(["git", "add", "--", *paths], cwd=worktree)
    staged = tuple(sorted(filter(None, _git(worktree, "diff", "--cached", "--name-only").splitlines())))
    if staged != tuple(sorted(paths)):
        raise FixturePoolProvisionError(
            f"fixture commit path set drifted: expected={sorted(paths)} actual={list(staged)}"
        )
    _git(worktree, "commit", "-m", message)
    return _exact_sha(_git(worktree, "rev-parse", "HEAD"), "new fixture commit")


def _push_non_force(worktree: Path, slot: SlotSpec) -> None:
    # Deliberately use one ordinary push path only; no history rewrite or deletion path exists here.
    _git(worktree, "push", "origin", f"HEAD:refs/heads/{slot.target_ref}")


def _history_digest(*, slot: SlotSpec, base_sha: str, bootstrap_sha: str, active_sha: str) -> str:
    value = {
        "pool_profile": POOL_PROFILE,
        "scenario": slot.scenario,
        "feature_id": slot.feature_id,
        "target_ref": slot.target_ref,
        "base_sha": base_sha,
        "bootstrap_sha": bootstrap_sha,
        "active_sha": active_sha,
        "bootstrap_paths": sorted((slot.manifest_path, slot.implementation_path)),
        "activation_paths": sorted((slot.manifest_path, slot.event_path)),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _inspect_existing_active(
    slot: SlotSpec,
    *,
    worktree: Path,
    repository: str,
    current_main: str,
) -> tuple[str, str, str]:
    active_sha = _exact_sha(_git(worktree, "rev-parse", "HEAD"), "active slot head")
    active_parents = _parents(worktree, active_sha)
    if len(active_parents) != 1:
        raise FixturePoolProvisionError(f"active slot must have one parent: {slot.scenario}")
    bootstrap_sha = active_parents[0]
    bootstrap_parents = _parents(worktree, bootstrap_sha)
    if len(bootstrap_parents) != 1:
        raise FixturePoolProvisionError(f"bootstrap slot must have one parent: {slot.scenario}")
    base_sha = bootstrap_parents[0]
    _require_ancestor(worktree, base_sha, current_main, f"slot base {slot.scenario}")
    if _commit_subject(worktree, bootstrap_sha) != BOOTSTRAP_PREFIX + slot.scenario:
        raise FixturePoolProvisionError(f"slot bootstrap commit is not provisioner-owned: {slot.scenario}")
    if _commit_subject(worktree, active_sha) != ACTIVATION_PREFIX + slot.scenario:
        raise FixturePoolProvisionError(f"slot activation commit is not provisioner-owned: {slot.scenario}")
    if _changed_paths(worktree, base_sha, bootstrap_sha) != tuple(sorted((slot.manifest_path, slot.implementation_path))):
        raise FixturePoolProvisionError(f"slot bootstrap changed unexpected paths: {slot.scenario}")
    if _changed_paths(worktree, bootstrap_sha, active_sha) != tuple(sorted((slot.event_path, slot.manifest_path))):
        raise FixturePoolProvisionError(f"slot activation changed unexpected paths: {slot.scenario}")
    if _git(worktree, "rev-list", "--count", f"{base_sha}..{active_sha}") != "2":
        raise FixturePoolProvisionError(f"active slot history is not exactly base + 2 commits: {slot.scenario}")
    verify_active_files(slot, repo_dir=worktree, repository=repository)
    return base_sha, bootstrap_sha, active_sha


def _inspect_existing_bootstrap(
    slot: SlotSpec,
    *,
    worktree: Path,
    current_main: str,
) -> tuple[str, str]:
    bootstrap_sha = _exact_sha(_git(worktree, "rev-parse", "HEAD"), "bootstrap slot head")
    parents = _parents(worktree, bootstrap_sha)
    if len(parents) != 1:
        raise FixturePoolProvisionError(f"bootstrap slot must have one parent: {slot.scenario}")
    base_sha = parents[0]
    _require_ancestor(worktree, base_sha, current_main, f"slot base {slot.scenario}")
    if _commit_subject(worktree, bootstrap_sha) != BOOTSTRAP_PREFIX + slot.scenario:
        raise FixturePoolProvisionError(f"slot bootstrap commit is not provisioner-owned: {slot.scenario}")
    if _changed_paths(worktree, base_sha, bootstrap_sha) != tuple(sorted((slot.manifest_path, slot.implementation_path))):
        raise FixturePoolProvisionError(f"slot bootstrap changed unexpected paths: {slot.scenario}")
    if _git(worktree, "rev-list", "--count", f"{base_sha}..{bootstrap_sha}") != "1":
        raise FixturePoolProvisionError(f"bootstrap slot history is not exactly base + 1 commit: {slot.scenario}")
    verify_bootstrap_files(slot, repo_dir=worktree)
    return base_sha, bootstrap_sha


def _worktree_for_existing(repo_dir: Path, *, remote_sha: str, target: Path, slot: SlotSpec) -> None:
    _git(repo_dir, "fetch", "origin", f"+refs/heads/{slot.target_ref}:refs/remotes/origin/{slot.target_ref}")
    fetched = _exact_sha(_git(repo_dir, "rev-parse", f"refs/remotes/origin/{slot.target_ref}"), "fetched slot head")
    if fetched != remote_sha:
        raise FixturePoolProvisionError(f"slot ref moved during exact fetch: {slot.scenario}")
    _run(["git", "worktree", "add", "--detach", str(target), remote_sha], cwd=repo_dir)
    _configure_identity(target)


def _worktree_for_absent(repo_dir: Path, *, current_main: str, target: Path, slot: SlotSpec) -> None:
    _run(["git", "worktree", "add", "--detach", str(target), current_main], cwd=repo_dir)
    _configure_identity(target)
    _git(target, "checkout", "-b", slot.target_ref)


def _api_request(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-sdlc-v03-scenario-fixture-pool",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode()) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload: Any = json.loads(raw.decode()) if raw else {}
        except Exception:
            payload = {}
        return exc.code, payload
    except (URLError, TimeoutError, OSError) as exc:
        raise FixturePoolProvisionError("GitHub API transport failed") from exc


def _repo_default_branch(*, api: str, repository: str, token: str) -> str:
    status, payload = _api_request("GET", f"{api}/repos/{repository}", token)
    if status != 200 or not isinstance(payload, dict) or not payload.get("default_branch"):
        raise FixturePoolProvisionError("cannot resolve repository default branch")
    branch = str(payload["default_branch"])
    if branch != "main":
        raise FixturePoolProvisionError("v0.3 scenario fixture pool requires default branch main")
    return branch


def _recover_or_create_pr(
    slot: SlotSpec,
    *,
    api: str,
    repository: str,
    token: str,
    head_sha: str,
    default_branch: str,
) -> tuple[int, str]:
    owner = repository.split("/", 1)[0]
    query = urlencode({"state": "all", "head": f"{owner}:{slot.target_ref}", "base": default_branch, "per_page": 100})
    status, payload = _api_request("GET", f"{api}/repos/{repository}/pulls?{query}", token)
    if status != 200 or not isinstance(payload, list):
        raise FixturePoolProvisionError(f"cannot inspect slot PR history: {slot.scenario}")
    matches = [
        row for row in payload
        if isinstance(row, dict)
        and (row.get("head") or {}).get("ref") == slot.target_ref
        and (row.get("base") or {}).get("ref") == default_branch
    ]
    if len(matches) > 1:
        raise FixturePoolProvisionError(f"ambiguous historical PRs for slot: {slot.scenario}")
    if matches:
        row = matches[0]
        if row.get("state") != "open" or row.get("draft") is True:
            raise FixturePoolProvisionError(f"existing slot PR is closed/draft and cannot be repaired: {slot.scenario}")
        if str((row.get("head") or {}).get("sha") or "") != head_sha:
            raise FixturePoolProvisionError(f"existing slot PR head drifted: {slot.scenario}")
        return int(row["number"]), str(row.get("html_url") or "")
    status, row = _api_request(
        "POST",
        f"{api}/repos/{repository}/pulls",
        token,
        {
            "title": f"test(v0.3): #221 fixture — {slot.scenario}",
            "head": slot.target_ref,
            "base": default_branch,
            "body": (
                "Release-only Issue #221 scenario fixture. This branch is provisioned only by the "
                "trusted-main scenario fixture pool and is not product/release evidence."
            ),
            "draft": False,
        },
    )
    if status != 201 or not isinstance(row, dict):
        raise FixturePoolProvisionError(f"cannot create slot PR: {slot.scenario}")
    if str((row.get("head") or {}).get("sha") or "") != head_sha or (row.get("base") or {}).get("ref") != default_branch:
        raise FixturePoolProvisionError(f"created slot PR binding drifted: {slot.scenario}")
    return int(row["number"]), str(row.get("html_url") or "")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prepare_checkout(*, repo_dir: Path, current_main: str) -> None:
    _git(repo_dir, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    fetched_main = _exact_sha(_git(repo_dir, "rev-parse", "refs/remotes/origin/main"), "fetched main")
    if fetched_main != current_main:
        raise FixturePoolProvisionError("trusted main moved during scenario fixture provisioning")


def _provision_one(
    slot: SlotSpec,
    *,
    repo_dir: Path,
    repository: str,
    current_main: str,
    api: str,
    token: str,
    default_branch: str,
) -> dict[str, Any]:
    remote_sha = _remote_head(repo_dir, slot.target_ref)
    with tempfile.TemporaryDirectory(prefix="v03-scenario-slot-") as temp:
        worktree = Path(temp) / "worktree"
        if remote_sha is None:
            _worktree_for_absent(repo_dir, current_main=current_main, target=worktree, slot=slot)
            materialize_bootstrap(slot, repo_dir=worktree)
            bootstrap_sha = _commit_exact(
                worktree,
                paths=(slot.manifest_path, slot.implementation_path),
                message=BOOTSTRAP_PREFIX + slot.scenario,
            )
            _push_non_force(worktree, slot)
            materialize_activation(slot, repo_dir=worktree, repository=repository)
            active_sha = _commit_exact(
                worktree,
                paths=(slot.manifest_path, slot.event_path),
                message=ACTIVATION_PREFIX + slot.scenario,
            )
            _push_non_force(worktree, slot)
            base_sha = current_main
            branch_action = "created-bootstrap-and-active"
        else:
            _worktree_for_existing(repo_dir, remote_sha=remote_sha, target=worktree, slot=slot)
            try:
                base_sha, bootstrap_sha, active_sha = _inspect_existing_active(
                    slot,
                    worktree=worktree,
                    repository=repository,
                    current_main=current_main,
                )
                branch_action = "verified-active"
            except FixturePoolProvisionError as active_exc:
                try:
                    base_sha, bootstrap_sha = _inspect_existing_bootstrap(
                        slot,
                        worktree=worktree,
                        current_main=current_main,
                    )
                except FixturePoolProvisionError:
                    raise active_exc
                materialize_activation(slot, repo_dir=worktree, repository=repository)
                active_sha = _commit_exact(
                    worktree,
                    paths=(slot.manifest_path, slot.event_path),
                    message=ACTIVATION_PREFIX + slot.scenario,
                )
                _push_non_force(worktree, slot)
                branch_action = "resumed-bootstrap-to-active"

        active_plan = verify_active_files(slot, repo_dir=worktree, repository=repository)
        confirmed_remote = _remote_head(repo_dir, slot.target_ref)
        if confirmed_remote != active_sha:
            raise FixturePoolProvisionError(f"slot ref moved before receipt sealing: {slot.scenario}")
        pr_number, pr_url = _recover_or_create_pr(
            slot,
            api=api,
            repository=repository,
            token=token,
            head_sha=active_sha,
            default_branch=default_branch,
        )
        return {
            "schema_version": "ai-sdlc.v03-scenario-fixture-slot-receipt/v1",
            "pool_profile": POOL_PROFILE,
            "repository": repository.lower(),
            "installation_commit_sha": current_main,
            "scenario": slot.scenario,
            "feature_id": slot.feature_id,
            "target_ref": slot.target_ref,
            "branch_action": branch_action,
            "base_sha": base_sha,
            "bootstrap_sha": bootstrap_sha,
            "slot_branch_head": active_sha,
            "active_sha": active_sha,
            "history_digest": _history_digest(
                slot=slot,
                base_sha=base_sha,
                bootstrap_sha=bootstrap_sha,
                active_sha=active_sha,
            ),
            "candidate_pr_number": pr_number,
            "candidate_pr_url": pr_url,
            "candidate_head_sha": active_sha,
            "manifest_revision": active_plan["manifest_revision"],
            "workflow_status": active_plan["workflow_status"],
            "current_stage": active_plan["current_stage"],
            "stage_status": active_plan["stage_status"],
            "worker_launch_count": 0,
            "operator_store_semantic_mutation_count": 0,
            "release_eligible": False,
        }


def provision_pool(
    *,
    repo_dir: Path,
    repository: str,
    github_ref: str,
    github_sha: str,
    token: str,
    api: str = "https://api.github.com",
) -> dict[str, Any]:
    validate_inventory()
    if repository.lower() != "dream-xin/ai-sdlc":
        raise FixturePoolProvisionError("scenario fixture pool repository scope mismatch")
    if github_ref != EXPECTED_REF:
        raise FixturePoolProvisionError("scenario fixture pool must run from refs/heads/main")
    current_main = _exact_sha(github_sha, "trusted main")
    if not token:
        raise FixturePoolProvisionError("GitHub token is required")
    default_branch = _repo_default_branch(api=api, repository=repository, token=token)
    _prepare_checkout(repo_dir=repo_dir, current_main=current_main)
    inventory = inventory_document()
    _write_json(EVIDENCE_ROOT / "inventory.json", inventory)
    receipts: list[dict[str, Any]] = []
    for slot in SLOTS:
        receipt = _provision_one(
            slot,
            repo_dir=repo_dir,
            repository=repository,
            current_main=current_main,
            api=api,
            token=token,
            default_branch=default_branch,
        )
        receipts.append(receipt)
        _write_json(EVIDENCE_ROOT / f"{slot.scenario}.json", receipt)
    pool_receipt = {
        "schema_version": "ai-sdlc.v03-scenario-fixture-pool-receipt/v1",
        "pool_profile": POOL_PROFILE,
        "repository": repository.lower(),
        "installation_commit_sha": current_main,
        "inventory_digest": inventory["inventory_digest"],
        "slot_count": len(receipts),
        "slots": receipts,
        "worker_launch_count": 0,
        "operator_store_semantic_mutation_count": 0,
        "release_eligible": False,
    }
    _write_json(POOL_RECEIPT, pool_receipt)
    return pool_receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=Path, default=Path("."))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--github-ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--github-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = parser.parse_args()
    result = provision_pool(
        repo_dir=args.repo_dir,
        repository=args.repository,
        github_ref=args.github_ref,
        github_sha=args.github_sha,
        token=args.token,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
