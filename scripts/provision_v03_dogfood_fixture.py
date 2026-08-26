#!/usr/bin/env python3
"""Trusted-main provisioner for one fixed v0.3 real-dogfood fixture slot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v03_dogfood_fixture_pool import (
    DogfoodSlot,
    materialize_activation,
    materialize_bootstrap,
    require_slot,
    verify_active_files,
)
from v03_dogfood_fixture_pr_authority import recover_or_create_dogfood_pr

BOOTSTRAP_PREFIX = "test(v0.3): bootstrap dogfood fixture "
ACTIVATION_PREFIX = "test(v0.3): activate dogfood fixture "


class DogfoodFixtureProvisionError(RuntimeError):
    pass


def _run(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and result.returncode != 0:
        raise DogfoodFixtureProvisionError(result.stderr.strip() or result.stdout.strip() or "trusted fixture command failed")
    return result


def _git(cwd: Path, *args: str) -> str:
    return _run(["git", *args], cwd=cwd).stdout.strip()


def _sha(value: str, label: str) -> str:
    value = str(value or "").strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise DogfoodFixtureProvisionError(f"{label} is not an exact Git SHA")
    return value


def _changed(cwd: Path, base: str, head: str) -> tuple[str, ...]:
    return tuple(sorted(filter(None, _git(cwd, "diff", "--name-only", base, head).splitlines())))


def _parents(cwd: Path, sha: str) -> list[str]:
    row = _git(cwd, "rev-list", "--parents", "-n", "1", sha).split()
    if not row or row[0] != sha:
        raise DogfoodFixtureProvisionError("cannot reconstruct dogfood fixture parents")
    return row[1:]


def _remote_head(cwd: Path, ref: str) -> str | None:
    raw = _git(cwd, "ls-remote", "--heads", "origin", f"refs/heads/{ref}")
    if not raw:
        return None
    rows = [line.split() for line in raw.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != f"refs/heads/{ref}":
        raise DogfoodFixtureProvisionError("dogfood fixture remote ref is ambiguous")
    return _sha(rows[0][0], "dogfood fixture remote head")


def _configure(cwd: Path) -> None:
    _git(cwd, "config", "user.name", "AI-SDLC Trusted Dogfood Provisioner")
    _git(cwd, "config", "user.email", "trusted-dogfood@ai-sdlc.invalid")


def _commit(cwd: Path, *, paths: tuple[str, ...], message: str) -> str:
    _run(["git", "add", "--", *paths], cwd=cwd)
    staged = tuple(sorted(filter(None, _git(cwd, "diff", "--cached", "--name-only").splitlines())))
    if staged != tuple(sorted(paths)):
        raise DogfoodFixtureProvisionError("dogfood fixture staged path set drifted")
    _git(cwd, "commit", "-m", message)
    return _sha(_git(cwd, "rev-parse", "HEAD"), "dogfood fixture commit")


def _api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if not token or not repository:
        raise DogfoodFixtureProvisionError("dogfood fixture GitHub authority is missing")
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = Request(
        f"{api}/repos/{repository}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ai-sdlc-v03-dogfood-fixture",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            if response.status not in {200, 201}:
                raise DogfoodFixtureProvisionError(f"GitHub API returned HTTP {response.status}")
            return json.loads(raw.decode()) if raw else {}
    except HTTPError as exc:
        raise DogfoodFixtureProvisionError(f"GitHub API returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DogfoodFixtureProvisionError("GitHub API transport failed") from exc


def _inspect_existing(slot: DogfoodSlot, *, worktree: Path, repository: str, current_main: str) -> tuple[str, str, str]:
    active = _sha(_git(worktree, "rev-parse", "HEAD"), "dogfood active head")
    active_parents = _parents(worktree, active)
    if len(active_parents) != 1:
        raise DogfoodFixtureProvisionError("dogfood active fixture must have one parent")
    bootstrap = active_parents[0]
    bootstrap_parents = _parents(worktree, bootstrap)
    if len(bootstrap_parents) != 1:
        raise DogfoodFixtureProvisionError("dogfood bootstrap fixture must have one parent")
    base = bootstrap_parents[0]
    if _run(["git", "merge-base", "--is-ancestor", base, current_main], cwd=worktree, check=False).returncode != 0:
        raise DogfoodFixtureProvisionError("dogfood fixture base is not trusted-main ancestry")
    if _git(worktree, "show", "-s", "--format=%s", bootstrap) != BOOTSTRAP_PREFIX + slot.scenario:
        raise DogfoodFixtureProvisionError("dogfood bootstrap commit is not provisioner-owned")
    if _git(worktree, "show", "-s", "--format=%s", active) != ACTIVATION_PREFIX + slot.scenario:
        raise DogfoodFixtureProvisionError("dogfood activation commit is not provisioner-owned")
    if _changed(worktree, base, bootstrap) != tuple(sorted((slot.manifest_path, slot.task_path))):
        raise DogfoodFixtureProvisionError("dogfood bootstrap changed unexpected paths")
    if _changed(worktree, bootstrap, active) != tuple(sorted((slot.manifest_path, slot.event_path))):
        raise DogfoodFixtureProvisionError("dogfood activation changed unexpected paths")
    if _git(worktree, "rev-list", "--count", f"{base}..{active}") != "2":
        raise DogfoodFixtureProvisionError("dogfood fixture history is not exactly base + 2 commits")
    verify_active_files(slot, repo_dir=worktree, repository=repository, candidate_head=active)
    return base, bootstrap, active


def provision(*, slot: DogfoodSlot, repo_dir: Path) -> dict[str, Any]:
    repository = os.environ.get("GITHUB_REPOSITORY", "").lower()
    if repository != "dream-xin/ai-sdlc":
        raise DogfoodFixtureProvisionError("dogfood fixture is bound to DREAM-XIN/ai-sdlc")
    current_main = _sha(_git(repo_dir, "rev-parse", "HEAD"), "trusted-main checkout")
    if current_main != _sha(os.environ.get("GITHUB_SHA", ""), "workflow trusted-main SHA"):
        raise DogfoodFixtureProvisionError("dogfood fixture checkout differs from exact workflow main")
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise DogfoodFixtureProvisionError("dogfood fixture provisioning requires trusted-main workflow_dispatch")

    remote = _remote_head(repo_dir, slot.target_ref)
    with tempfile.TemporaryDirectory() as temp:
        worktree = Path(temp) / "fixture"
        if remote is None:
            _run(["git", "worktree", "add", "--detach", str(worktree), current_main], cwd=repo_dir)
            _configure(worktree)
            _git(worktree, "checkout", "-b", slot.target_ref)
            bootstrap_paths = materialize_bootstrap(slot, repo_dir=worktree)
            bootstrap = _commit(worktree, paths=bootstrap_paths, message=BOOTSTRAP_PREFIX + slot.scenario)
            activation_paths = materialize_activation(slot, repo_dir=worktree, repository=repository)
            active = _commit(worktree, paths=activation_paths, message=ACTIVATION_PREFIX + slot.scenario)
            verify_active_files(slot, repo_dir=worktree, repository=repository, candidate_head=active)
            _git(worktree, "push", "origin", f"HEAD:refs/heads/{slot.target_ref}")
            base = current_main
        else:
            _git(repo_dir, "fetch", "origin", f"+refs/heads/{slot.target_ref}:refs/remotes/origin/{slot.target_ref}")
            fetched = _sha(_git(repo_dir, "rev-parse", f"refs/remotes/origin/{slot.target_ref}"), "fetched dogfood fixture")
            if fetched != remote:
                raise DogfoodFixtureProvisionError("dogfood fixture ref moved during fetch")
            _run(["git", "worktree", "add", "--detach", str(worktree), remote], cwd=repo_dir)
            _configure(worktree)
            base, bootstrap, active = _inspect_existing(slot, worktree=worktree, repository=repository, current_main=current_main)

    pr = recover_or_create_dogfood_pr(
        slot=slot,
        call=_api,
        repository=repository,
        head_sha=active,
    )
    return {
        "schema_version": "ai-sdlc.v03-dogfood-fixture-receipt/v1",
        "scenario": slot.scenario,
        "feature_id": slot.feature_id,
        "target_ref": slot.target_ref,
        "trusted_main_sha": current_main,
        "base_sha": base,
        "bootstrap_sha": bootstrap,
        "active_sha": active,
        "candidate_pr_number": pr["number"],
        "candidate_head_sha": active,
        "candidate_pr_url": pr.get("html_url"),
        "ordinary_push_only": True,
        "worker_launch_count": 0,
        "operator_store_semantic_mutation_count": 0,
        "release_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = provision(slot=require_slot(args.scenario), repo_dir=Path("."))
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
