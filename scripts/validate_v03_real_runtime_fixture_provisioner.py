#!/usr/bin/env python3
"""Deterministic validation for the fixed v0.3 real-runtime fixture provisioner."""
from __future__ import annotations

import copy
import hashlib
import subprocess
import tempfile
from pathlib import Path

import yaml

from apply_feature_event import apply_event
from ingest_feature_event import ingest
from operator_vertical import (
    FeatureSnapshot,
    TrustedDispatchContext,
    VERTICAL_PROFILE,
    translate_result,
)
from operator_vertical_controller import select_vertical_action
from operator_vertical_recovery import DurableRoleIndependencePolicy
from provision_v03_real_runtime_fixture import (
    EVENT_ID,
    EVENT_PATH,
    FEATURE_ID,
    IMPLEMENTATION_PATH,
    MANIFEST_PATH,
    TARGET_REF,
    activate_manifest,
    build_bootstrap_manifest,
    materialize_activation,
    materialize_bootstrap,
)
from resolve_feature_event_push import resolve_push
from validate_feature_manifest import validate_manifest
from verify_git_write_precondition import verify_write_precondition

REPOSITORY = "dream-xin/ai-sdlc"
NOW = "2026-08-14T10:00:00Z"


def require(value, message):
    if not value:
        raise AssertionError(message)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def fixture_feature(manifest: dict, *, candidate: str) -> FeatureSnapshot:
    return FeatureSnapshot.from_manifest(
        repository=REPOSITORY,
        target_ref=TARGET_REF,
        manifest=manifest,
        candidate_pr_number=321,
        candidate_head_sha=candidate,
    )


def reviewer_context(feature: FeatureSnapshot, *, task_id: str, dispatch_id: str, worker: str) -> TrustedDispatchContext:
    return TrustedDispatchContext(
        operation_id="op-v03-fixture-review",
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key="b" * 64,
        external_dispatch_key="dispatch-v03-fixture-review",
        dispatch_id=dispatch_id,
        runtime_receipt_identity="123456789",
        target_repository=REPOSITORY,
        target_ref=TARGET_REF,
        feature_id=FEATURE_ID,
        expected_revision=feature.revision,
        feature_stage=feature.current_stage,
        task_id=task_id,
        role="reviewer",
        candidate_pr_number=feature.candidate_pr_number,
        candidate_head_sha=feature.candidate_head_sha,
        worker_identity=worker,
        collector_identity="trusted-fixture-collector",
    )


def review_receipt(context: TrustedDispatchContext, *, label: str, data: bytes) -> dict:
    return {
        "output_id": f"out-{label}",
        "label": label,
        "kind": "evidence",
        "media_type": "text/markdown",
        "trusted_uri": f"docs/features/{FEATURE_ID}/worker-runs/{context.dispatch_id}/{label}.md",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "operation_id": context.operation_id,
        "operation_generation": context.operation_generation,
        "operation_profile": context.operation_profile,
        "semantic_effect_key": context.semantic_effect_key,
        "external_dispatch_key": context.external_dispatch_key,
        "dispatch_id": context.dispatch_id,
        "worker_role": context.role,
        "worker_identity": context.worker_identity,
        "target_repository": REPOSITORY,
        "feature_id": FEATURE_ID,
        "expected_revision": context.expected_revision,
        "candidate_head_sha": context.candidate_head_sha,
        "collector_identity": context.collector_identity,
        "collected_at": NOW,
    }


def empty_durable_independence() -> DurableRoleIndependencePolicy:
    return DurableRoleIndependencePolicy(
        candidate_contributor_identities=(),
        reviewer_identities=(),
        developer_identity=None,
        remediation_developer_identity=None,
    )


def validate_real_reviewer_outcomes(manifest: dict, *, candidate: str, review_task_id: str) -> None:
    """Prove first real Reviewer PASS and REWORK both remain canonical."""
    feature = fixture_feature(manifest, candidate=candidate)

    pass_context = reviewer_context(
        feature,
        task_id=review_task_id,
        dispatch_id="fixture-review-pass",
        worker="reviewer-fixture-pass",
    )
    empty_durable_independence().verify(pass_context)
    pass_data = b"fixture review pass evidence"
    pass_receipt = review_receipt(pass_context, label="code-review", data=pass_data)
    pass_event = translate_result(
        context=pass_context,
        feature=feature,
        worker_payload={
            "verdict": "PASS",
            "summary": "fixture review passed",
            "findings": [],
            "outputs": [{"label": "code-review", "kind": "evidence"}],
        },
        receipts=[pass_receipt],
        independence_policy=empty_durable_independence(),
        occurred_at=NOW,
        content_loader=lambda uri: {pass_receipt["trusted_uri"]: pass_data}[uri],
    )
    applied_pass = apply_event(manifest, pass_event)
    require(applied_pass["outcome"] == "APPLIED", f"fixture Reviewer PASS Event is not canonical: {applied_pass}")
    passed = applied_pass["manifest"]
    pass_stages = {row["id"]: row["status"] for row in passed["workflow"]["stages"]}
    pass_gates = {row["id"]: row["status"] for row in passed["gates"]}
    require("implementation" not in pass_stages, "Reviewer PASS invented an implementation lifecycle stage")
    require(pass_stages["code-review"] == "DONE" and pass_stages["verification"] == "READY", "Reviewer PASS did not advance to Verification")
    require(pass_gates["code-gate"] == "PASS", "Reviewer PASS did not pass code-gate")
    require(passed["artifacts"][0]["status"] == "approved", "Reviewer PASS did not approve draft implementation artifact")

    rework_context = reviewer_context(
        feature,
        task_id=review_task_id,
        dispatch_id="fixture-review-rework",
        worker="reviewer-fixture-rework",
    )
    empty_durable_independence().verify(rework_context)
    rework_data = b"fixture review rework evidence"
    rework_receipt = review_receipt(rework_context, label="code-review-rework", data=rework_data)
    rework_event = translate_result(
        context=rework_context,
        feature=feature,
        worker_payload={
            "verdict": "REWORK",
            "summary": "fixture review requests remediation",
            "findings": [
                {"severity": "MAJOR", "code": "FI-REWORK", "summary": "exercise authentic remediation path"}
            ],
            "outputs": [{"label": "code-review-rework", "kind": "evidence"}],
        },
        receipts=[rework_receipt],
        independence_policy=empty_durable_independence(),
        occurred_at=NOW,
        content_loader=lambda uri: {rework_receipt["trusted_uri"]: rework_data}[uri],
    )
    applied_rework = apply_event(manifest, rework_event)
    require(applied_rework["outcome"] == "APPLIED", f"fixture Reviewer REWORK Event is not canonical: {applied_rework}")
    reworked = applied_rework["manifest"]
    require(validate_manifest(reworked) == [], "Reviewer REWORK produced invalid fixture Manifest")
    rework_stages = {row["id"]: row["status"] for row in reworked["workflow"]["stages"]}
    require("implementation" not in rework_stages, "REWORK invented an implementation lifecycle stage")
    require(rework_stages["code-review"] == "WORKING", "REWORK moved the frozen code-review lifecycle locus")
    remediation = [row for row in reworked["tasks"] if row.get("kind") == "remediation"]
    require(len(remediation) == 1, "Reviewer REWORK did not create one remediation task")
    require(
        remediation[0]["stage"] == "implementation"
        and remediation[0]["source_stage"] == "code-review"
        and remediation[0]["status"] == "TODO",
        "Reviewer REWORK remediation identity drifted",
    )
    no_draft = copy.deepcopy(reworked)
    no_draft["artifacts"] = []
    require(
        any("unknown stage implementation" in error for error in validate_manifest(no_draft)),
        "Code-Review-first remediation was accepted without its draft implementation artifact",
    )

    rework_feature = fixture_feature(reworked, candidate=candidate)
    start_remediation = select_vertical_action(feature=rework_feature, manifest=reworked, occurred_at=NOW)
    require(
        start_remediation.kind == "persist" and start_remediation.step == "CODE_REMEDIATION",
        "Reviewer REWORK cannot enter the standard remediation path",
    )
    remediation_started = apply_event(reworked, start_remediation.feature_event)
    require(remediation_started["outcome"] == "APPLIED", "remediation start Persist is not canonical")
    remediation_manifest = remediation_started["manifest"]
    remediation_feature = fixture_feature(remediation_manifest, candidate=candidate)
    remediation_dispatch = select_vertical_action(feature=remediation_feature, manifest=remediation_manifest, occurred_at=NOW)
    require(
        remediation_dispatch.kind == "dispatch"
        and remediation_dispatch.role == "developer"
        and remediation_dispatch.step == "CODE_REMEDIATION",
        "authentic REWORK did not converge to a bounded Developer remediation dispatch",
    )


def main() -> None:
    bootstrap = build_bootstrap_manifest()
    require(validate_manifest(bootstrap) == [], "bootstrap Manifest failed canonical validation")
    require(bootstrap["revision"] == 0, "bootstrap must be revision 0")
    require(bootstrap["workflow"]["current_stage"] == "code-review", "bootstrap stage drifted")
    bootstrap_stages = {row["id"]: row["status"] for row in bootstrap["workflow"]["stages"]}
    require(
        bootstrap_stages == {
            "code-review": "READY",
            "verification": "TODO",
            "acceptance": "TODO",
        },
        "bootstrap fixture is not Code-Review-first",
    )
    require("implementation" not in bootstrap_stages, "bootstrap invented implementation stage")
    require(bootstrap["evidence"] == [] and bootstrap["artifacts"] == [], "bootstrap fabricated authority evidence")

    manifest, event = activate_manifest(bootstrap_manifest=bootstrap, repository=REPOSITORY)
    require(validate_manifest(manifest) == [], "active fixture Manifest failed canonical validation")
    require(manifest["revision"] == 1, "fixture must stop at revision 1")
    require(manifest["workflow"]["status"] == "ACTIVE", "fixture must remain ACTIVE")
    require(manifest["workflow"]["current_stage"] == "code-review", "fixture stage drifted")
    stages = {row["id"]: row["status"] for row in manifest["workflow"]["stages"]}
    require(stages == {"code-review": "WORKING", "verification": "TODO", "acceptance": "TODO"}, "fixture lifecycle expanded beyond frozen Issue #276")
    require(manifest["evidence"] == [], "fixture fabricated review evidence")
    require(manifest["tasks"] == [], "fixture fabricated remediation tasks")
    require(all(row["status"] == "PENDING" for row in manifest["gates"]), "fixture fabricated Gate verdict")
    require(manifest["applied_events"] == [EVENT_ID], "fixture must have one canonical activation Event")
    require(manifest["artifacts"] == [{
        "id": "implementation-v1",
        "type": "implementation",
        "uri": IMPLEMENTATION_PATH,
        "status": "draft",
    }], "fixture draft implementation artifact drifted")

    candidate = "a" * 40
    feature = fixture_feature(manifest, candidate=candidate)
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=NOW)
    require(action.kind == "dispatch" and action.role == "reviewer" and action.step == "CODE_REVIEW", "fixture did not select Reviewer")
    require(action.candidate_head_sha == candidate, "fixture dispatch lost trusted PR head binding")
    require(candidate not in yaml.safe_dump(manifest, sort_keys=False), "Manifest embeds future PR head")
    validate_real_reviewer_outcomes(manifest, candidate=candidate, review_task_id=action.task_id)

    replay = ingest(
        manifest,
        event,
        event_path=EVENT_PATH,
        repository=REPOSITORY,
        manifest_path=MANIFEST_PATH,
        target_ref=TARGET_REF,
        issue=276,
    )
    require(replay["outcome"] == "INVALID", "activation Event replay unexpectedly mutated fixture")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        remote = base / "remote.git"
        root = base / "work"
        git(base, "init", "--bare", str(remote))
        git(base, "init", str(root))
        git(root, "config", "user.name", "fixture-validator")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "remote", "add", "origin", str(remote))
        (root / "seed").write_text("seed\n", encoding="utf-8")
        git(root, "add", "seed")
        git(root, "commit", "-m", "seed")
        git(root, "branch", "-M", "main")
        git(root, "push", "origin", "HEAD:refs/heads/main")
        git(root, "checkout", "-b", TARGET_REF)

        bootplan = materialize_bootstrap(repo_dir=root)
        require(bootplan["current_stage"] == "code-review" and bootplan["stage_status"] == "READY", "bootstrap plan overstates lifecycle")
        require(bootplan["release_eligible"] is False, "bootstrap plan overclaims release eligibility")
        git(root, "add", MANIFEST_PATH, IMPLEMENTATION_PATH)
        git(root, "commit", "-m", "fixture bootstrap")
        git(root, "push", "origin", f"HEAD:refs/heads/{TARGET_REF}")
        before = git(root, "rev-parse", "HEAD")

        precondition = verify_write_precondition(root, TARGET_REF, "main")
        require(precondition["outcome"] == "READY", "shared Git write precondition rejected exact bootstrap head")

        plan = materialize_activation(repo_dir=root, repository=REPOSITORY)
        require(plan["release_eligible"] is False, "activation plan overclaims release eligibility")
        require(plan["current_stage"] == "code-review" and plan["stage_status"] == "WORKING", "activation plan drifted from Code-Review-first authority")
        require("implementation_stage_status" not in plan, "activation plan fabricates implementation stage authority")
        git(root, "add", MANIFEST_PATH, EVENT_PATH)
        git(root, "commit", "-m", "fixture activation")
        after = git(root, "rev-parse", "HEAD")
        mode, event_path, manifest_path, changed = resolve_push(root, before, after)
        require(mode == "noop", "second push is not an exact already-applied Event replay")
        require(event_path == "" and manifest_path == "", "noop unexpectedly selected persistence mutation")
        require(changed == [EVENT_PATH], "second push changed unexpected Feature Event paths")
        git(root, "push", "origin", f"HEAD:refs/heads/{TARGET_REF}")

        try:
            materialize_activation(repo_dir=root, repository=REPOSITORY)
            raise AssertionError("second activation unexpectedly replaced Event")
        except RuntimeError as exc:
            require("already exists" in str(exc), "second activation failed for wrong reason")

    workflow = Path(".github/workflows/provision-v03-real-runtime-fixture.yml").read_text(encoding="utf-8")
    require("workflow_dispatch:" in workflow and "inputs:" not in workflow, "fixture workflow exposes caller-selected authority")
    require("refs/heads/main" in workflow, "fixture workflow lacks trusted-main gate")
    require(FEATURE_ID in workflow and TARGET_REF in workflow, "fixture workflow is not fixed-scope")
    require("contents: write" in workflow and "pull-requests: write" in workflow, "fixture workflow lacks bounded creation authority")
    require("git push --force" not in workflow and "--force" not in workflow, "fixture workflow permits force push")
    require("implementation_stage_status" not in workflow, "durable fixture receipt fabricates implementation lifecycle authority")
    require('"state": "all"' in workflow, "fixture PR recovery does not inspect historical fixed-branch PRs")
    require("provisioning_run_main_sha" in workflow, "fixture receipt lacks current trusted-main recovery provenance")

    print("v0.3 real-runtime fixture provisioner validation passed")
    print("- frozen fixture profile starts at code-review; no implementation lifecycle completion is fabricated")
    print("- first real Reviewer PASS and REWORK are both canonical")
    print("- REWORK uses narrow artifact-backed remediation while code-review remains WORKING")
    print("- bootstrap/activation are replay-safe ordinary Git commits with no candidate SHA in Manifest")
    print("- provisioning workflow remains trusted-main-only, fixed-scope and release_eligible=false")


if __name__ == "__main__":
    main()
