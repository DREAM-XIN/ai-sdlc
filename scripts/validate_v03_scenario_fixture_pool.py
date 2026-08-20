#!/usr/bin/env python3
"""Deterministic validation for the closed #310 v0.3 scenario fixture pool."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import subprocess
import tempfile

import v03_scenario_fixture_pool as pool
import provision_v03_scenario_fixture_pool as provisioner

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
    except (RuntimeError, provisioner.FixturePoolProvisionError) as exc:
        require(contains in str(exc), f"wrong failure: {exc}")
    else:
        raise AssertionError(f"expected failure containing {contains!r}")


def validate_closed_inventory_and_no_selector_surface():
    pool.validate_inventory()
    inventory = pool.inventory_document()
    require(inventory["slot_count"] == 9, "pool is not exactly nine slots")
    require([row["scenario"] for row in inventory["slots"]] == list(pool.EXPECTED_SCENARIOS), "pool scenario order drifted")
    require(len({row["feature_id"] for row in inventory["slots"]}) == 9, "Feature ids are not unique")
    require(len({row["target_ref"] for row in inventory["slots"]}) == 9, "refs are not unique")
    require(inventory["release_eligible"] is False, "fixture inventory overclaimed release eligibility")

    main_source = inspect.getsource(provisioner.main)
    for forbidden in ("--slot", "--feature", "--ref", "--scenario", "--count"):
        require(forbidden not in main_source, f"provisioner exposes forbidden selector {forbidden}")
    source = inspect.getsource(provisioner)
    require("--force-with-lease" not in source, "provisioner contains force-with-lease authority")
    require('"push", "origin", f"HEAD:refs/heads/{slot.target_ref}"' in source, "provisioner lost exact non-force push path")
    require("operation.start" not in source, "fixture provisioner contains Worker launch entrypoint")
    require("dispatch_gateway" not in source, "fixture provisioner contains Worker dispatch gateway")


def validate_slot_canonical_files_and_isolation():
    with tempfile.TemporaryDirectory(prefix="v03-pool-files-") as directory:
        root = Path(directory)
        slot_a, slot_b = pool.SLOTS[:2]
        pool.materialize_bootstrap(slot_a, repo_dir=root)
        before = file_digest(root, [slot_a.manifest_path, slot_a.implementation_path])
        expect_error(lambda: pool.verify_bootstrap_files(slot_b, repo_dir=root), "not exact bootstrap file set")
        pool.materialize_bootstrap(slot_b, repo_dir=root)
        after = file_digest(root, [slot_a.manifest_path, slot_a.implementation_path])
        require(before == after, "materializing one slot mutated another slot")
        pool.materialize_activation(slot_a, repo_dir=root, repository=REPOSITORY)
        active_paths = [slot_a.manifest_path, slot_a.implementation_path, slot_a.event_path]
        digest_before = file_digest(root, active_paths)
        first = pool.verify_active_files(slot_a, repo_dir=root, repository=REPOSITORY)
        second = pool.verify_active_files(slot_a, repo_dir=root, repository=REPOSITORY)
        require(first == second, "exact active verification is not deterministic")
        require(digest_before == file_digest(root, active_paths), "exact active rerun mutated lifecycle files")
        require(first["release_eligible"] is False, "active fixture overclaimed release evidence")


def validate_inventory_mutations_fail_closed():
    original = pool.SLOTS
    try:
        pool.SLOTS = original[:-1]
        expect_error(pool.validate_inventory, "exactly nine")
        pool.SLOTS = original + (original[0],)
        expect_error(pool.validate_inventory, "exactly nine")

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
        expect_error(pool.validate_inventory, "duplicate refs")

        swapped = list(original)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        pool.SLOTS = tuple(swapped)
        expect_error(pool.validate_inventory, "differs from Issue #310 closed set")
    finally:
        pool.SLOTS = original


def init_remote_fixture_repo(root: Path):
    remote = root / "remote.git"
    seed = root / "seed"
    run(["git", "init", "--bare", str(remote)], root)
    run(["git", "clone", str(remote), str(seed)], root)
    git(seed, "config", "user.name", "Fixture Test")
    git(seed, "config", "user.email", "fixture-test@example.invalid")
    (seed / "README.md").write_text("fixture pool validation\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-m", "test: seed trusted main")
    git(seed, "branch", "-M", "main")
    git(seed, "push", "-u", "origin", "main")
    main_sha = git(seed, "rev-parse", "HEAD")
    return remote, seed, main_sha


def clone_controller(root: Path, remote: Path, main_sha: str) -> Path:
    controller = root / "controller"
    run(["git", "clone", str(remote), str(controller)], root)
    git(controller, "checkout", "main")
    require(git(controller, "rev-parse", "HEAD") == main_sha, "controller did not checkout exact main")
    return controller


def install_api_fakes():
    original_default = provisioner._repo_default_branch
    original_pr = provisioner._recover_or_create_pr
    state = {"next": 700, "calls": []}

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


def _provision(controller: Path, main_sha: str, *, repository: str = REPOSITORY, github_ref: str = MAIN_REF):
    return provisioner.provision_pool(
        repo_dir=controller,
        repository=repository,
        github_ref=github_ref,
        github_sha=main_sha,
        token="test-token",
        api="https://api.example.invalid",
    )


def _assert_complete_slot_receipt(row, *, main_sha: str):
    require(row["repository"] == REPOSITORY, "slot receipt repository drifted")
    require(row["installation_commit_sha"] == main_sha, "slot receipt trusted-main head drifted")
    require(row["slot_branch_head"] == row["active_sha"], "slot receipt branch head drifted")
    require(row["candidate_head_sha"] == row["active_sha"], "slot candidate head drifted")
    require(isinstance(row["candidate_pr_number"], int) and row["candidate_pr_number"] > 0, "slot candidate PR missing")
    require(row["manifest_revision"] == 1, "slot active revision drifted")
    require(row["workflow_status"] == "ACTIVE", "slot workflow status drifted")
    require(row["current_stage"] == "code-review", "slot current stage drifted")
    require(row["stage_status"] == "WORKING", "slot stage status drifted")
    require(len(row["history_digest"]) == 64, "slot history digest is not bounded sha256")
    require(row["worker_launch_count"] == 0, "slot receipt claimed Worker launch")
    require(row["operator_store_semantic_mutation_count"] == 0, "slot receipt claimed Store mutation")
    require(row["release_eligible"] is False, "slot receipt overclaimed release eligibility")


def validate_full_fresh_then_inert_rerun():
    with tempfile.TemporaryDirectory(prefix="v03-pool-roundtrip-") as directory:
        root = Path(directory)
        remote, _, main_sha = init_remote_fixture_repo(root)
        controller = clone_controller(root, remote, main_sha)
        evidence = root / "evidence"
        original_root, original_pool_receipt = provisioner.EVIDENCE_ROOT, provisioner.POOL_RECEIPT
        original_default, original_pr, api_state = install_api_fakes()
        try:
            provisioner.EVIDENCE_ROOT = evidence
            provisioner.POOL_RECEIPT = evidence / "pool.json"
            first = _provision(controller, main_sha)
            require(first["slot_count"] == 9, "fresh provisioning did not cover all slots")
            require(first["inventory_digest"] == pool.inventory_document()["inventory_digest"], "pool receipt inventory digest drifted")
            require(all(row["branch_action"] == "created-bootstrap-and-active" for row in first["slots"]), "fresh pool did not create exact bootstrap+active states")
            for row in first["slots"]:
                _assert_complete_slot_receipt(row, main_sha=main_sha)
            heads_before = {slot.target_ref: provisioner._remote_head(controller, slot.target_ref) for slot in pool.SLOTS}
            require(all(heads_before.values()), "fresh provisioning missed a slot branch")
            first_history = {row["scenario"]: row["history_digest"] for row in first["slots"]}

            second = _provision(controller, main_sha)
            heads_after = {slot.target_ref: provisioner._remote_head(controller, slot.target_ref) for slot in pool.SLOTS}
            require(heads_before == heads_after, "exact active pool rerun mutated remote branch heads")
            require(all(row["branch_action"] == "verified-active" for row in second["slots"]), "active rerun did not remain read-only")
            require(first["inventory_digest"] == second["inventory_digest"], "pool inventory digest changed across inert rerun")
            require(first_history == {row["scenario"]: row["history_digest"] for row in second["slots"]}, "slot history proof changed across inert rerun")
            for row in second["slots"]:
                _assert_complete_slot_receipt(row, main_sha=main_sha)
            require(len(api_state["calls"]) == 18, "each full run must verify/create exactly one PR per fixed slot")
        finally:
            provisioner.EVIDENCE_ROOT = original_root
            provisioner.POOL_RECEIPT = original_pool_receipt
            restore_api_fakes(original_default, original_pr)


def provision_bootstrap_only(controller: Path, main_sha: str, slot: pool.SlotSpec):
    with tempfile.TemporaryDirectory(prefix="v03-bootstrap-only-") as directory:
        target = Path(directory) / "slot"
        provisioner._worktree_for_absent(controller, current_main=main_sha, target=target, slot=slot)
        try:
            pool.materialize_bootstrap(slot, repo_dir=target)
            sha = provisioner._commit_exact(
                target,
                paths=(slot.manifest_path, slot.implementation_path),
                message=provisioner.BOOTSTRAP_PREFIX + slot.scenario,
            )
            provisioner._push_non_force(target, slot)
            return sha
        finally:
            run(["git", "worktree", "remove", "--force", str(target)], controller)


def validate_partial_bootstrap_resume_and_bad_history_rejection():
    with tempfile.TemporaryDirectory(prefix="v03-pool-partial-") as directory:
        root = Path(directory)
        remote, _, main_sha = init_remote_fixture_repo(root)
        controller = clone_controller(root, remote, main_sha)
        bootstrap_sha = provision_bootstrap_only(controller, main_sha, pool.SLOTS[0])
        evidence = root / "evidence"
        original_root, original_pool_receipt = provisioner.EVIDENCE_ROOT, provisioner.POOL_RECEIPT
        original_default, original_pr, _ = install_api_fakes()
        try:
            provisioner.EVIDENCE_ROOT = evidence
            provisioner.POOL_RECEIPT = evidence / "pool.json"
            result = _provision(controller, main_sha)
            first = result["slots"][0]
            require(first["bootstrap_sha"] == bootstrap_sha, "partial resume rewrote canonical bootstrap commit")
            require(first["branch_action"] == "resumed-bootstrap-to-active", "partial bootstrap did not resume exactly once")
            _assert_complete_slot_receipt(first, main_sha=main_sha)
            require(all(row["branch_action"] == "created-bootstrap-and-active" for row in result["slots"][1:]), "partial run did not provision the remaining fixed slots")

            slot = pool.SLOTS[1]
            remote_head = provisioner._remote_head(controller, slot.target_ref)
            with tempfile.TemporaryDirectory(prefix="v03-bad-history-") as bad_dir:
                target = Path(bad_dir) / "slot"
                provisioner._worktree_for_existing(controller, remote_sha=remote_head, target=target, slot=slot)
                try:
                    (target / "unexpected.txt").write_text("forbidden history\n", encoding="utf-8")
                    git(target, "add", "unexpected.txt")
                    git(target, "commit", "-m", "forbidden extra fixture mutation")
                    provisioner._push_non_force(target, slot)
                finally:
                    run(["git", "worktree", "remove", "--force", str(target)], controller)

            expect_error(
                lambda: _provision(controller, main_sha),
                "trusted-main ancestry",
            )
        finally:
            provisioner.EVIDENCE_ROOT = original_root
            provisioner.POOL_RECEIPT = original_pool_receipt
            restore_api_fakes(original_default, original_pr)


def validate_non_main_and_repository_scope_fail_closed():
    with tempfile.TemporaryDirectory(prefix="v03-pool-scope-") as directory:
        root = Path(directory)
        remote, _, main_sha = init_remote_fixture_repo(root)
        controller = clone_controller(root, remote, main_sha)
        expect_error(
            lambda: _provision(controller, main_sha, github_ref="refs/heads/not-main"),
            "must run from refs/heads/main",
        )
        expect_error(
            lambda: _provision(controller, main_sha, repository="other/repo"),
            "repository scope mismatch",
        )


def main():
    validate_closed_inventory_and_no_selector_surface()
    validate_slot_canonical_files_and_isolation()
    validate_inventory_mutations_fail_closed()
    validate_full_fresh_then_inert_rerun()
    validate_partial_bootstrap_resume_and_bad_history_rejection()
    validate_non_main_and_repository_scope_fail_closed()
    print("PASS: bounded nine-slot fixture pool is closed, complete-receipt, resumable, inert on exact rerun and fail-closed on incompatible history")


if __name__ == "__main__":
    main()
