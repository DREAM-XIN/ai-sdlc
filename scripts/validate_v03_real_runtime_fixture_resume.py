#!/usr/bin/env python3
"""Adversarial validation for resumable v0.3 real-runtime fixture provisioning."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from provision_v03_real_runtime_fixture import (
    EVENT_PATH,
    IMPLEMENTATION_PATH,
    MANIFEST_PATH,
    TARGET_REF,
    materialize_activation,
    materialize_bootstrap,
    verify_active_files,
    verify_bootstrap_files,
)

REPOSITORY = "dream-xin/ai-sdlc"
BOOTSTRAP_PATHS = tuple(sorted((MANIFEST_PATH, IMPLEMENTATION_PATH)))
ACTIVATION_PATHS = tuple(sorted((MANIFEST_PATH, EVENT_PATH)))
ACTIVE_PATHS = tuple(sorted((MANIFEST_PATH, EVENT_PATH, IMPLEMENTATION_PATH)))


def require(value, message):
    if not value:
        raise AssertionError(message)


def git(root: Path, *args: str, ok: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr or f"git {' '.join(args)} failed")
    if not ok:
        return str(result.returncode)
    return result.stdout.strip()


def changed(root: Path, before: str, after: str) -> tuple[str, ...]:
    return tuple(sorted(line for line in git(root, "diff", "--name-only", before, after).splitlines() if line))


def one_parent(root: Path, sha: str) -> bool:
    return len(git(root, "rev-list", "--parents", "-n", "1", sha).split()) == 2


def inspect_existing(root: Path, *, current_main: str) -> tuple[str, str]:
    """Mirror the workflow's exact branch-shape/ancestry recovery contract."""
    head = git(root, "rev-parse", "HEAD")
    if (root / EVENT_PATH).is_file():
        verify_active_files(repo_dir=root, repository=REPOSITORY)
        bootstrap = git(root, "rev-parse", "HEAD~1")
        base = git(root, "rev-parse", "HEAD~2")
        require(one_parent(root, head) and one_parent(root, bootstrap), "active fixture commits must be linear")
        require(git(root, "merge-base", "--is-ancestor", base, current_main, ok=False) == "0", "active fixture base is not trusted-main ancestry")
        require(changed(root, base, bootstrap) == BOOTSTRAP_PATHS, "bootstrap commit path set drifted")
        require(changed(root, bootstrap, head) == ACTIVATION_PATHS, "activation commit path set drifted")
        require(changed(root, base, head) == ACTIVE_PATHS, "active fixture total path set drifted")
        return "active", base

    verify_bootstrap_files(repo_dir=root)
    base = git(root, "rev-parse", "HEAD~1")
    require(one_parent(root, head), "bootstrap fixture commit must be linear")
    require(git(root, "merge-base", "--is-ancestor", base, current_main, ok=False) == "0", "bootstrap fixture base is not trusted-main ancestry")
    require(changed(root, base, head) == BOOTSTRAP_PATHS, "bootstrap fixture path set drifted")
    return "bootstrap", base


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        base_dir = Path(td)
        remote = base_dir / "remote.git"
        work = base_dir / "work"
        git(base_dir, "init", "--bare", str(remote))
        git(base_dir, "init", str(work))
        git(work, "config", "user.name", "fixture-resume-validator")
        git(work, "config", "user.email", "fixture-resume@example.invalid")
        git(work, "remote", "add", "origin", str(remote))

        (work / "seed").write_text("trusted main\n", encoding="utf-8")
        git(work, "add", "seed")
        git(work, "commit", "-m", "trusted main base")
        git(work, "branch", "-M", "main")
        trusted_base = git(work, "rev-parse", "HEAD")
        git(work, "push", "origin", "HEAD:refs/heads/main")
        git(work, "checkout", "-b", TARGET_REF)

        materialize_bootstrap(repo_dir=work)
        git(work, "add", MANIFEST_PATH, IMPLEMENTATION_PATH)
        git(work, "commit", "-m", "fixture bootstrap")
        git(work, "push", "origin", f"HEAD:refs/heads/{TARGET_REF}")
        state, fixture_base = inspect_existing(work, current_main=trusted_base)
        require(state == "bootstrap" and fixture_base == trusted_base, "exact bootstrap is not resumable")

        materialize_activation(repo_dir=work, repository=REPOSITORY)
        git(work, "add", MANIFEST_PATH, EVENT_PATH)
        git(work, "commit", "-m", "fixture activation")
        active_head = git(work, "rev-parse", "HEAD")
        git(work, "push", "origin", f"HEAD:refs/heads/{TARGET_REF}")
        state, fixture_base = inspect_existing(work, current_main=trusted_base)
        require(state == "active" and fixture_base == trusted_base, "exact active fixture is not resumable")

        git(work, "checkout", "main")
        (work / "later-main").write_text("later reviewed main\n", encoding="utf-8")
        git(work, "add", "later-main")
        git(work, "commit", "-m", "later main")
        later_main = git(work, "rev-parse", "HEAD")
        git(work, "checkout", TARGET_REF)
        state, fixture_base = inspect_existing(work, current_main=later_main)
        require(state == "active" and fixture_base == trusted_base, "reviewed ancestor base did not survive main advance")

        (work / "unexpected.txt").write_text("not provisioner-owned\n", encoding="utf-8")
        git(work, "add", "unexpected.txt")
        git(work, "commit", "-m", "unexpected fixture mutation")
        try:
            inspect_existing(work, current_main=later_main)
            raise AssertionError("extra fixed-branch commit was accepted as resumable fixture")
        except (AssertionError, RuntimeError):
            pass
        git(work, "reset", "--hard", active_head)

        implementation = work / IMPLEMENTATION_PATH
        implementation.write_text(implementation.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
        git(work, "add", IMPLEMENTATION_PATH)
        git(work, "commit", "-m", "fixture content drift")
        try:
            inspect_existing(work, current_main=later_main)
            raise AssertionError("drifted fixed fixture content was accepted")
        except (AssertionError, RuntimeError):
            pass
        git(work, "reset", "--hard", active_head)

        git(work, "checkout", "--orphan", "untrusted-base")
        subprocess.run(["git", "-C", str(work), "rm", "-rf", "."], text=True, capture_output=True)
        subprocess.run(["git", "-C", str(work), "clean", "-fdx"], check=True, text=True, capture_output=True)
        (work / "untrusted").write_text("untrusted root\n", encoding="utf-8")
        git(work, "add", "untrusted")
        git(work, "commit", "-m", "untrusted root")
        materialize_bootstrap(repo_dir=work)
        git(work, "add", MANIFEST_PATH, IMPLEMENTATION_PATH)
        git(work, "commit", "-m", "lookalike bootstrap")
        try:
            inspect_existing(work, current_main=later_main)
            raise AssertionError("lookalike fixture on unrelated ancestry was accepted")
        except (AssertionError, RuntimeError):
            pass

    workflow = Path(".github/workflows/provision-v03-real-runtime-fixture.yml").read_text(encoding="utf-8")
    for needle in (
        "FIXTURE_STATE",
        "FIXTURE_BASE_SHA",
        "verify-bootstrap",
        "verify-active",
        "merge-base --is-ancestor",
        '"state": "all"',
        "candidate_pr_number",
        "provisioning_run_main_sha",
    ):
        require(needle in workflow, f"resumable fixture workflow is missing {needle}")
    require("git push --force" not in workflow and "--force" not in workflow, "resume workflow permits force push")
    require("git branch -D" not in workflow and "git push origin --delete" not in workflow, "resume workflow deletes incompatible fixture state")

    print("v0.3 real-runtime fixture resumability validation passed")
    print("- exact bootstrap state resumes activation")
    print("- exact active state resumes PR/receipt verification")
    print("- reviewed base may remain an ancestor after main advances")
    print("- extra commit/content drift/unrelated ancestry fail closed")
    print("- workflow reuses one open exact PR and never force-repairs/deletes state")


if __name__ == "__main__":
    main()
