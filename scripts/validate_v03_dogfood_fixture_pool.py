#!/usr/bin/env python3
"""Deterministic validation for the closed v0.3 release dogfood fixture pool."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import subprocess
import tempfile

import provision_v03_dogfood_fixture_pool as provisioner
import v03_dogfood_fixture_pool as pool

REPOSITORY = "dream-xin/ai-sdlc"
MAIN_REF = "refs/heads/main"


def require(value, message):
    if not value:
        raise AssertionError(message)


def run(args, cwd):
    proc = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise AssertionError(f"command failed: {args}: {proc.stderr or proc.stdout}")
    return proc.stdout.strip()


def git(cwd, *args):
    return run(["git", *args], cwd)


def file_digest(root: Path, paths: list[str]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.encode())
        h.update(b"\0")
        h.update((root / path).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def expect_error(fn, contains):
    try:
        fn()
    except (RuntimeError, provisioner.DogfoodFixturePoolProvisionError) as exc:
        require(contains in str(exc), f"wrong failure: {exc}")
    else:
        raise AssertionError(f"expected failure containing {contains!r}")


def validate_closed_inventory_and_no_execution_surface():
    pool.validate_inventory()
    inventory = pool.inventory_document()
    require(len(inventory["slots"]) == 3, "dogfood pool is not exactly three slots")
    require(
        [row["scenario"] for row in inventory["slots"]] == list(pool.EXPECTED_SCENARIOS),
        "dogfood scenario order drifted",
    )
    require(inventory["release_eligible"] is False, "fixture inventory overclaimed release eligibility")

    main_source = inspect.getsource(provisioner.main)
    for forbidden in ("--slot", "--feature", "--ref", "--scenario", "--count"):
        require(forbidden not in main_source, f"provisioner exposes forbidden selector {forbidden}")
    source = inspect.getsource(provisioner)
    for forbidden in ("--force", "--force-with-lease", "operation.start", "dispatch_gateway", "OpenAI"):
        require(forbidden not in source, f"fixture provisioner contains forbidden execution surface {forbidden}")
    require(
        '"push", "origin", f"HEAD:refs/heads/{slot.target_ref}"' in source,
        "provisioner lost exact ordinary push path",
    )


def validate_slot_files_and_selector_readiness():
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-files-") as directory:
        root = Path(directory)
        first, second = pool.SLOTS[:2]
        pool.materialize_bootstrap(first, repo_dir=root)
        before = file_digest(root, [first.manifest_path, first.implementation_path])
        expect_error(lambda: pool.verify_bootstrap_files(second, repo_dir=root), "not exact bootstrap file set")
        pool.materialize_bootstrap(second, repo_dir=root)
        require(before == file_digest(root, [first.manifest_path, first.implementation_path]), "one slot mutated another")

        pool.materialize_activation(first, repo_dir=root, repository=REPOSITORY)
        active_paths = [first.manifest_path, first.implementation_path, first.event_path]
        digest_before = file_digest(root, active_paths)
        one = pool.verify_active_files(first, repo_dir=root, repository=REPOSITORY)
        two = pool.verify_active_files(first, repo_dir=root, repository=REPOSITORY)
        require(one == two, "active dogfood verification is not deterministic")
        require(digest_before == file_digest(root, active_paths), "active verification mutated files")
        require(one["current_stage"] == "implementation", "active dogfood slot is not implementation stage")
        require(one["stage_status"] == "WORKING", "active dogfood slot is not WORKING")
        require(one["release_eligible"] is False, "active dogfood fixture became release evidence")


def validate_inventory_mutations_fail_closed():
    original = pool.SLOTS
    try:
        pool.SLOTS = original[:-1]
        expect_error(pool.validate_inventory, "frozen three")
        pool.SLOTS = original + (original[0],)
        expect_error(pool.validate_inventory, "frozen three")

        duplicate_feature = list(original)
        duplicate_feature[1] = pool.SlotSpec(
            duplicate_feature[1].scenario,
            duplicate_feature[0].feature_id,
            duplicate_feature[1].target_ref,
            duplicate_feature[1].created_at,
            duplicate_feature[1].activated_at,
        )
        pool.SLOTS = tuple(duplicate_feature)
        expect_error(pool.validate_inventory, "duplicate Feature")

        duplicate_ref = list(original)
        duplicate_ref[1] = pool.SlotSpec(
            duplicate_ref[1].scenario,
            duplicate_ref[1].feature_id,
            duplicate_ref[0].target_ref,
            duplicate_ref[1].created_at,
            duplicate_ref[1].activated_at,
        )
        pool.SLOTS = tuple(duplicate_ref)
        expect_error(pool.validate_inventory, "duplicate target refs")
    finally:
        pool.SLOTS = original


def init_remote_fixture_repo(root: Path):
    remote = root / "remote.git"
    seed = root / "seed"
    run(["git", "init", "--bare", str(remote)], root)
    run(["git", "clone", str(remote), str(seed)], root)
    git(seed, "config", "user.name", "Dogfood Fixture Test")
    git(seed, "config", "user.email", "dogfood-fixture@example.invalid")
    (seed / "README.md").write_text("dogfood fixture pool validation\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-m", "test: seed trusted main")
    git(seed, "branch", "-M", "main")
    git(seed, "push", "-u", "origin", "main")
    return remote, seed, git(seed, "rev-parse", "HEAD")


def clone_controller(root: Path, remote: Path, main_sha: str) -> Path:
    controller = root / "controller"
    run(["git", "clone", str(remote), str(controller)], root)
    git(controller, "checkout", "main")
    require(git(controller, "rev-parse", "HEAD") == main_sha, "controller did not checkout exact main")
    return controller


def install_api_fakes():
    original_default = provisioner._repo_default_branch
    original_pr = provisioner._recover_or_create_pr
    state = {"next": 900, "calls": []}

    def fake_default(**kwargs):
        return "main"

    def fake_pr(slot, *, head_sha, **kwargs):
        state["calls"].append((slot.scenario, head_sha))
        state["next"] += 1
        return state["next"], "created-or-recovered-test"

    provisioner._repo_default_branch = fake_default
    provisioner._recover_or_create_pr = fake_pr
    return original_default, original_pr, state


def restore_api_fakes(original_default, original_pr):
    provisioner._repo_default_branch = original_default
    provisioner._recover_or_create_pr = original_pr


def _provision(controller: Path, main_sha: str, *, repository=REPOSITORY, github_ref=MAIN_REF):
    return provisioner.provision_pool(
        repo_dir=controller,
        repository=repository,
        github_ref=github_ref,
        github_sha=main_sha,
        token="test-token",
        api="https://api.example.invalid",
    )


def _assert_slot(row, main_sha: str):
    require(row["repository"] == REPOSITORY, "slot repository drifted")
    require(row["installation_commit_sha"] == main_sha, "slot installation head drifted")
    require(row["candidate_head_sha"] == row["active_sha"], "candidate head not active slot head")
    require(row["manifest_revision"] == 1, "slot revision drifted")
    require(row["current_stage"] == "implementation", "slot current stage drifted")
    require(row["stage_status"] == "WORKING", "slot status drifted")
    require(row["worker_launch_count"] == 0, "provisioning claimed Worker launch")
    require(row["operator_store_semantic_mutation_count"] == 0, "provisioning claimed Store mutation")
    require(row["model_call_count"] == 0, "provisioning claimed model call")
    require(row["release_eligible"] is False, "slot overclaimed release eligibility")


def validate_fresh_then_inert_rerun():
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-roundtrip-") as directory:
        root = Path(directory)
        remote, _, main_sha = init_remote_fixture_repo(root)
        controller = clone_controller(root, remote, main_sha)
        evidence = root / "evidence"
        old_root, old_receipt = provisioner.EVIDENCE_ROOT, provisioner.POOL_RECEIPT
        old_default, old_pr, api_state = install_api_fakes()
        try:
            provisioner.EVIDENCE_ROOT = evidence
            provisioner.POOL_RECEIPT = evidence / "pool.json"
            first = _provision(controller, main_sha)
            require(first["slot_count"] == 3, "fresh dogfood pool did not cover three slots")
            require(all(row["branch_action"] == "created-bootstrap-and-active" for row in first["slots"]), "fresh dogfood pool did not create exact two-commit slots")
            for row in first["slots"]:
                _assert_slot(row, main_sha)
            heads_before = {slot.target_ref: provisioner._remote_head(controller, slot.target_ref) for slot in pool.SLOTS}
            histories = {row["scenario"]: row["history_digest"] for row in first["slots"]}

            second = _provision(controller, main_sha)
            heads_after = {slot.target_ref: provisioner._remote_head(controller, slot.target_ref) for slot in pool.SLOTS}
            require(heads_before == heads_after, "inert rerun mutated dogfood refs")
            require(all(row["branch_action"] == "verified-active" for row in second["slots"]), "inert rerun was not read-only")
            require(histories == {row["scenario"]: row["history_digest"] for row in second["slots"]}, "history proof changed on inert rerun")
            require(len(api_state["calls"]) == 6, "each full run must bind exactly one PR per fixed slot")
        finally:
            provisioner.EVIDENCE_ROOT = old_root
            provisioner.POOL_RECEIPT = old_receipt
            restore_api_fakes(old_default, old_pr)


def validate_consumed_slot_is_never_recycled():
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-consumed-") as directory:
        root = Path(directory)
        remote, _, main_sha = init_remote_fixture_repo(root)
        controller = clone_controller(root, remote, main_sha)
        old_root, old_receipt = provisioner.EVIDENCE_ROOT, provisioner.POOL_RECEIPT
        old_default, old_pr, _ = install_api_fakes()
        try:
            provisioner.EVIDENCE_ROOT = root / "evidence"
            provisioner.POOL_RECEIPT = provisioner.EVIDENCE_ROOT / "pool.json"
            _provision(controller, main_sha)
            slot = pool.SLOTS[0]
            remote_head = provisioner._remote_head(controller, slot.target_ref)
            with tempfile.TemporaryDirectory(prefix="v03-dogfood-consume-") as temp:
                wt = Path(temp) / "worktree"
                provisioner._worktree_for_existing(controller, remote_sha=remote_head, target=wt, slot=slot)
                try:
                    (wt / "runtime-consumed.txt").write_text("real runtime would own later history\n", encoding="utf-8")
                    git(wt, "add", "runtime-consumed.txt")
                    git(wt, "commit", "-m", "test: consume dogfood slot")
                    provisioner._push_non_force(wt, slot)
                finally:
                    run(["git", "worktree", "remove", "--force", str(wt)], controller)
            expect_error(lambda: _provision(controller, main_sha), "base + 2 commits")
        finally:
            provisioner.EVIDENCE_ROOT = old_root
            provisioner.POOL_RECEIPT = old_receipt
            restore_api_fakes(old_default, old_pr)


def validate_scope_fail_closed():
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-scope-") as directory:
        root = Path(directory)
        remote, _, main_sha = init_remote_fixture_repo(root)
        controller = clone_controller(root, remote, main_sha)
        expect_error(lambda: _provision(controller, main_sha, github_ref="refs/heads/not-main"), "must run from refs/heads/main")
        expect_error(lambda: _provision(controller, main_sha, repository="other/repo"), "repository scope mismatch")


def main():
    validate_closed_inventory_and_no_execution_surface()
    validate_slot_files_and_selector_readiness()
    validate_inventory_mutations_fail_closed()
    validate_fresh_then_inert_rerun()
    validate_consumed_slot_is_never_recycled()
    validate_scope_fail_closed()
    print("PASS: three dogfood slots are isolated, Developer-ready, resumable only before use, inert on exact rerun and never recycled after consumption")


if __name__ == "__main__":
    main()
