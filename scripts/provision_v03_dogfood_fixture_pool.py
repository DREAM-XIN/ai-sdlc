#!/usr/bin/env python3
"""Trusted-main-only provisioner for the closed v0.3 release dogfood fixture pool."""
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

from v03_dogfood_fixture_pool import (
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

EVIDENCE_ROOT = Path("evidence/v03-dogfood-fixture-pool")
POOL_RECEIPT = EVIDENCE_ROOT / "pool.json"
EXPECTED_REF = "refs/heads/main"
BOOTSTRAP_PREFIX = "test(v0.3): bootstrap dogfood fixture "
ACTIVATION_PREFIX = "test(v0.3): activate dogfood fixture "


class DogfoodFixturePoolProvisionError(RuntimeError):
    pass


def _run(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise DogfoodFixturePoolProvisionError(
            f"command failed ({' '.join(args[:3])}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def _git(repo_dir: Path, *args: str) -> str:
    return _run(["git", *args], cwd=repo_dir).stdout.strip()


def _exact_sha(value: str, label: str) -> str:
    value = value.strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise DogfoodFixturePoolProvisionError(f"{label} is not an exact Git SHA")
    return value


def _remote_head(repo_dir: Path, ref: str) -> str | None:
    raw = _run(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{ref}"],
        cwd=repo_dir,
    ).stdout.strip()
    if not raw:
        return None
    rows = [line.split() for line in raw.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != f"refs/heads/{ref}":
        raise DogfoodFixturePoolProvisionError(f"ambiguous remote ref for dogfood slot: {ref}")
    return _exact_sha(rows[0][0], f"remote head {ref}")


def _parents(repo_dir: Path, sha: str) -> list[str]:
    parts = _git(repo_dir, "rev-list", "--parents", "-n", "1", sha).split()
    if not parts or parts[0] != sha:
        raise DogfoodFixturePoolProvisionError("cannot reconstruct dogfood fixture commit parents")
    return parts[1:]


def _changed_paths(repo_dir: Path, base: str, head: str) -> tuple[str, ...]:
    return tuple(sorted(filter(None, _git(repo_dir, "diff", "--name-only", base, head).splitlines())))


def _commit_subject(repo_dir: Path, sha: str) -> str:
    return _git(repo_dir, "show", "-s", "--format=%s", sha)


def _require_ancestor(repo_dir: Path, ancestor: str, descendant: str, label: str) -> None:
    proc = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_dir,
        check=False,
    )
    if proc.returncode != 0:
        raise DogfoodFixturePoolProvisionError(f"{label} is not trusted-main ancestry")


def _configure_identity(worktree: Path) -> None:
    _git(worktree, "config", "user.name", "AI-SDLC Trusted Dogfood Fixture Provisioner")
    _git(worktree, "config", "user.email", "trusted-dogfood-fixture@ai-sdlc.invalid")


def _commit_exact(worktree: Path, *, paths: tuple[str, ...], message: str) -> str:
    _run(["git", "add", "--", *paths], cwd=worktree)
    staged = tuple(sorted(filter(None, _git(worktree, "diff", "--cached", "--name-only").splitlines())))
    if staged != tuple(sorted(paths)):
        raise DogfoodFixturePoolProvisionError(
            f"dogfood fixture commit path set drifted: expected={sorted(paths)} actual={list(staged)}"
        )
    _git(worktree, "commit", "-m", message)
    return _exact_sha(_git(worktree, "rev-parse", "HEAD"), "new dogfood fixture commit")


def _push_non_force(worktree: Path, slot: SlotSpec) -> None:
    # One ordinary push path only. There is deliberately no force/reset/delete path.
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


def _worktree_for_absent(
    repo_dir: Path,
    *,
    current_main: str,
    target: Path,
    slot: SlotSpec,
) -> None:
    _run(["git", "worktree", "add", "--detach", str(target), current_main], cwd=repo_dir)
    _configure_identity(target)
    _git(target, "checkout", "-b", slot.target_ref)


def _worktree_for_existing(
    repo_dir: Path,
    *,
    remote_sha: str,
    target: Path,
    slot: SlotSpec,
) -> None:
    _git(
        repo_dir,
        "fetch",
        "origin",
        f"+refs/heads/{slot.target_ref}:refs/remotes/origin/{slot.target_ref}",
    )
    fetched = _exact_sha(
        _git(repo_dir, "rev-parse", f"refs/remotes/origin/{slot.target_ref}"),
        "fetched dogfood slot head",
    )
    if fetched != remote_sha:
        raise DogfoodFixturePoolProvisionError(f"dogfood slot ref moved during exact fetch: {slot.scenario}")
    _run(["git", "worktree", "add", "--detach", str(target), remote_sha], cwd=repo_dir)
    _configure_identity(target)


def _inspect_existing_bootstrap(
    slot: SlotSpec,
    *,
    worktree: Path,
    current_main: str,
) -> tuple[str, str]:
    bootstrap_sha = _exact_sha(_git(worktree, "rev-parse", "HEAD"), "bootstrap dogfood slot head")
    parents = _parents(worktree, bootstrap_sha)
    if len(parents) != 1:
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap must have one parent: {slot.scenario}")
    base_sha = parents[0]
    _require_ancestor(worktree, base_sha, current_main, f"dogfood slot base {slot.scenario}")
    if _commit_subject(worktree, bootstrap_sha) != BOOTSTRAP_PREFIX + slot.scenario:
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap is not provisioner-owned: {slot.scenario}")
    if _changed_paths(worktree, base_sha, bootstrap_sha) != tuple(
        sorted((slot.manifest_path, slot.implementation_path))
    ):
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap changed unexpected paths: {slot.scenario}")
    if _git(worktree, "rev-list", "--count", f"{base_sha}..{bootstrap_sha}") != "1":
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap history is not base + 1: {slot.scenario}")
    verify_bootstrap_files(slot, repo_dir=worktree)
    return base_sha, bootstrap_sha


def _inspect_existing_active(
    slot: SlotSpec,
    *,
    worktree: Path,
    repository: str,
    current_main: str,
) -> tuple[str, str, str]:
    active_sha = _exact_sha(_git(worktree, "rev-parse", "HEAD"), "active dogfood slot head")
    active_parents = _parents(worktree, active_sha)
    if len(active_parents) != 1:
        raise DogfoodFixturePoolProvisionError(f"active dogfood slot must have one parent: {slot.scenario}")
    bootstrap_sha = active_parents[0]
    bootstrap_parents = _parents(worktree, bootstrap_sha)
    if len(bootstrap_parents) != 1:
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap must have one parent: {slot.scenario}")
    base_sha = bootstrap_parents[0]
    _require_ancestor(worktree, base_sha, current_main, f"dogfood slot base {slot.scenario}")
    if _commit_subject(worktree, bootstrap_sha) != BOOTSTRAP_PREFIX + slot.scenario:
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap is not provisioner-owned: {slot.scenario}")
    if _commit_subject(worktree, active_sha) != ACTIVATION_PREFIX + slot.scenario:
        raise DogfoodFixturePoolProvisionError(f"dogfood activation is not provisioner-owned: {slot.scenario}")
    if _changed_paths(worktree, base_sha, bootstrap_sha) != tuple(
        sorted((slot.manifest_path, slot.implementation_path))
    ):
        raise DogfoodFixturePoolProvisionError(f"dogfood bootstrap changed unexpected paths: {slot.scenario}")
    if _changed_paths(worktree, bootstrap_sha, active_sha) != tuple(
        sorted((slot.event_path, slot.manifest_path))
    ):
        raise DogfoodFixturePoolProvisionError(f"dogfood activation changed unexpected paths: {slot.scenario}")
    if _git(worktree, "rev-list", "--count", f"{base_sha}..{active_sha}") != "2":
        raise DogfoodFixturePoolProvisionError(
            f"dogfood active history is not exactly base + 2 commits (slot may already be consumed): {slot.scenario}"
        )
    verify_active_files(slot, repo_dir=worktree, repository=repository)
    return base_sha, bootstrap_sha, active_sha


def _api_request(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-sdlc-v03-dogfood-fixture-pool",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
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
        raise DogfoodFixturePoolProvisionError("GitHub API transport failed") from exc


def _repo_default_branch(*, api: str, repository: str, token: str) -> str:
    status, payload = _api_request("GET", f"{api}/repos/{repository}", token)
    if status != 200 or not isinstance(payload, dict) or not payload.get("default_branch"):
        raise DogfoodFixturePoolProvisionError("cannot resolve repository default branch")
    branch = str(payload["default_branch"])
    if branch != "main":
        raise DogfoodFixturePoolProvisionError("v0.3 dogfood fixture pool requires default branch main")
    return branch


def _exact_slot_pr_binding(
    row: Any,
    slot: SlotSpec,
    *,
    repository: str,
    head_sha: str,
    default_branch: str,
) -> tuple[int, str]:
    if not isinstance(row, dict):
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR truth malformed: {slot.scenario}")
    head, base = row.get("head"), row.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR head/base missing: {slot.scenario}")
    head_repo, base_repo = head.get("repo"), base.get("repo")
    if not isinstance(head_repo, dict) or not isinstance(base_repo, dict):
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR repository authority missing: {slot.scenario}")
    expected = repository.lower()
    if str(head_repo.get("full_name") or "").lower() != expected or str(base_repo.get("full_name") or "").lower() != expected:
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR repository drifted: {slot.scenario}")
    if head.get("ref") != slot.target_ref or base.get("ref") != default_branch:
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR ref/base drifted: {slot.scenario}")
    candidate_head = _exact_sha(str(head.get("sha") or ""), f"dogfood slot PR head {slot.scenario}")
    if candidate_head != head_sha:
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR head drifted: {slot.scenario}")
    number = row.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR number malformed: {slot.scenario}")
    if row.get("state") != "open" or row.get("draft") is not False:
        raise DogfoodFixturePoolProvisionError(f"dogfood slot PR is not open non-draft: {slot.scenario}")
    return number, str(row.get("html_url") or "")


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
    query = urlencode(
        {
            "state": "all",
            "head": f"{owner}:{slot.target_ref}",
            "base": default_branch,
            "per_page": 100,
        }
    )
    status, payload = _api_request("GET", f"{api}/repos/{repository}/pulls?{query}", token)
    if status != 200 or not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise DogfoodFixturePoolProvisionError(f"cannot inspect dogfood slot PR history: {slot.scenario}")
    if len(payload) > 1:
        raise DogfoodFixturePoolProvisionError(f"ambiguous historical PRs for dogfood slot: {slot.scenario}")
    if payload:
        return _exact_slot_pr_binding(
            payload[0],
            slot,
            repository=repository,
            head_sha=head_sha,
            default_branch=default_branch,
        )
    status, row = _api_request(
        "POST",
        f"{api}/repos/{repository}/pulls",
        token,
        {
            "title": f"test(v0.3): dogfood fixture — {slot.scenario}",
            "head": slot.target_ref,
            "base": default_branch,
            "body": (
                "Release-only Issue #239 dogfood fixture provisioned by trusted-main prerequisite #345. "
                "This candidate is runtime input only and is not product or release evidence."
            ),
            "draft": False,
        },
    )
    if status != 201:
        raise DogfoodFixturePoolProvisionError(f"cannot create dogfood slot PR: {slot.scenario}")
    return _exact_slot_pr_binding(
        row,
        slot,
        repository=repository,
        head_sha=head_sha,
        default_branch=default_branch,
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prepare_checkout(*, repo_dir: Path, current_main: str) -> None:
    _git(repo_dir, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    fetched = _exact_sha(_git(repo_dir, "rev-parse", "refs/remotes/origin/main"), "fetched main")
    if fetched != current_main:
        raise DogfoodFixturePoolProvisionError("trusted main moved during dogfood fixture provisioning")


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
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-slot-") as temp:
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
            except DogfoodFixturePoolProvisionError as active_exc:
                try:
                    base_sha, bootstrap_sha = _inspect_existing_bootstrap(
                        slot,
                        worktree=worktree,
                        current_main=current_main,
                    )
                except DogfoodFixturePoolProvisionError:
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
        confirmed = _remote_head(repo_dir, slot.target_ref)
        if confirmed != active_sha:
            raise DogfoodFixturePoolProvisionError(f"dogfood slot moved before receipt sealing: {slot.scenario}")
        pr_number, pr_url = _recover_or_create_pr(
            slot,
            api=api,
            repository=repository,
            token=token,
            head_sha=active_sha,
            default_branch=default_branch,
        )
        return {
            "schema_version": "ai-sdlc.v03-dogfood-fixture-slot-receipt/v1",
            "pool_profile": POOL_PROFILE,
            "repository": repository.lower(),
            "installation_commit_sha": current_main,
            "scenario": slot.scenario,
            "feature_id": slot.feature_id,
            "target_ref": slot.target_ref,
            "branch_action": branch_action,
            "base_sha": base_sha,
            "bootstrap_sha": bootstrap_sha,
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
            "model_call_count": 0,
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
        raise DogfoodFixturePoolProvisionError("dogfood fixture pool repository scope mismatch")
    if github_ref != EXPECTED_REF:
        raise DogfoodFixturePoolProvisionError("dogfood fixture pool must run from refs/heads/main")
    current_main = _exact_sha(github_sha, "trusted main")
    if not token:
        raise DogfoodFixturePoolProvisionError("GitHub token is required")
    default_branch = _repo_default_branch(api=api, repository=repository, token=token)
    _prepare_checkout(repo_dir=repo_dir, current_main=current_main)

    inventory = inventory_document()
    _write_json(EVIDENCE_ROOT / "inventory.json", inventory)
    receipts: list[dict[str, Any]] = []
    for slot in SLOTS:
        row = _provision_one(
            slot,
            repo_dir=repo_dir,
            repository=repository,
            current_main=current_main,
            api=api,
            token=token,
            default_branch=default_branch,
        )
        receipts.append(row)
        _write_json(EVIDENCE_ROOT / f"{slot.scenario}.json", row)

    slot_receipt_digest = hashlib.sha256(
        json.dumps(receipts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    pool_receipt = {
        "schema_version": "ai-sdlc.v03-dogfood-fixture-pool-receipt/v1",
        "pool_profile": POOL_PROFILE,
        "repository": repository.lower(),
        "installation_commit_sha": current_main,
        "inventory_digest": inventory["inventory_digest"],
        "slot_count": len(receipts),
        "slot_receipt_digest": slot_receipt_digest,
        "slots": receipts,
        "worker_launch_count": 0,
        "operator_store_semantic_mutation_count": 0,
        "model_call_count": 0,
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
