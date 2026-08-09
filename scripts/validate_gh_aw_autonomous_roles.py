#!/usr/bin/env python3
"""Deterministic regression suite for bounded autonomous Reviewer/QA control-plane semantics."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from gh_aw_candidate import CandidateError, build_candidate_artifacts, resolve_current_candidate
from gh_aw_gate_result import GateResultError, translate
from gh_aw_role_workers import RoleWorkerError, load_role_workers, resolve_role_worker
from runtime_router import select_runtime

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "dispatch" / "gh-aw-developer.yaml"
REPO = "DREAM-XIN/example"
REF = "feature/F-EXAMPLE-0001"
SHA = "a" * 40
PR = 7


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def load_policy():
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def base_manifest(stage: str, status: str, candidate_status: str):
    artifacts = build_candidate_artifacts(REPO, PR, SHA)
    for item in artifacts:
        item["status"] = candidate_status
    stages = [
        {"id": "implementation", "status": "DONE"},
        {"id": "code-review", "status": "DONE" if stage == "verification" else status, "gate": "code-gate"},
        {"id": "verification", "status": status if stage == "verification" else "TODO", "gate": "verification-gate"},
        {"id": "acceptance", "status": "TODO", "gate": "release-gate"},
    ]
    if stage == "verification":
        artifacts.append({
            "id": f"reviewed-candidate-head-{SHA[:12]}",
            "type": "reviewed-candidate-head",
            "uri": f"https://github.com/{REPO}/commit/{SHA}",
            "status": "approved",
        })
    return {
        "revision": 10,
        "feature": {"id": "F-EXAMPLE-0001"},
        "workflow": {"stages": stages},
        "artifacts": artifacts,
    }


def reviewer_result(verdict="PASS"):
    result = {
        "version": "0.1.0",
        "contract": "ai-sdlc-gh-aw-reviewer-result-v0.1",
        "id": "GHAW-REVIEW-1",
        "feature_id": "F-EXAMPLE-0001",
        "task_id": "F-EXAMPLE-0001-code-review",
        "stage": "code-review",
        "role": "reviewer",
        "expected_revision": 10,
        "target_repository": REPO,
        "target_ref": REF,
        "candidate_pr_number": PR,
        "candidate_head_sha": SHA,
        "verdict": verdict,
        "findings": [],
        "evidence": [{"id": "evidence-review-1", "type": "review", "status": "pass", "uri": "docs/review.md"}],
        "occurred_at": "2026-08-09T13:00:00Z",
    }
    if verdict == "REWORK":
        result["reason"] = "A major issue requires remediation."
        result["findings"] = [{"code": "CR-MAJOR-1", "severity": "MAJOR", "message": "Fix the bounded defect."}]
        result["evidence"][0]["status"] = "fail"
    if verdict == "BLOCKED":
        result["reason"] = "Review cannot establish a safe verdict."
        result["evidence"][0]["status"] = "warning"
    return result


def qa_result(verdict="PASS"):
    result = {
        "version": "0.1.0",
        "contract": "ai-sdlc-gh-aw-qa-result-v0.1",
        "id": "GHAW-QA-1",
        "feature_id": "F-EXAMPLE-0001",
        "task_id": "F-EXAMPLE-0001-verification",
        "stage": "verification",
        "role": "qa",
        "expected_revision": 10,
        "target_repository": REPO,
        "target_ref": REF,
        "candidate_pr_number": PR,
        "candidate_head_sha": SHA,
        "verdict": verdict,
        "checks": [{"name": "protocol", "status": "pass"}],
        "coverage": [{"criterion": "AC-1", "status": "pass", "evidence": "protocol"}],
        "evidence": [{"id": "evidence-qa-1", "type": "verification", "status": "pass", "uri": "docs/verification.md"}],
        "occurred_at": "2026-08-09T13:00:00Z",
    }
    if verdict in {"FAIL", "BLOCKED"}:
        result["reason"] = "Verification did not establish PASS."
        result["checks"][0]["status"] = "fail" if verdict == "FAIL" else "blocked"
        result["coverage"][0]["status"] = "fail" if verdict == "FAIL" else "blocked"
        result["evidence"][0]["status"] = "fail" if verdict == "FAIL" else "warning"
    return result


def validate_routes():
    policy = load_policy()
    cases = [
        ("developer", "implementation", "gh-aw", "autonomous"),
        ("reviewer", "code-review", "gh-aw", "autonomous"),
        ("qa", "verification", "gh-aw", "autonomous"),
        ("reviewer", "requirement-review", "chatgpt-web", "manual"),
        ("reviewer", "design-review", "chatgpt-web", "manual"),
        ("product", "acceptance", "chatgpt-web", "manual"),
        ("architect", "design", "chatgpt-web", "manual"),
    ]
    for role, stage, runtime_id, mode in cases:
        result = select_runtime({"role": role, "stage": stage}, "high", policy)
        assert_true(result["outcome"] == "ROUTED", f"route failed for {role}/{stage}: {result}")
        assert_true(result["runtime"] == {"id": runtime_id, "mode": mode}, f"unexpected runtime for {role}/{stage}: {result}")


def validate_role_registry():
    workers = load_role_workers()
    assert_true(len(workers) == 4, "expected exactly four Gate-role worker variants")
    assert_true(resolve_role_worker("reviewer", "code-review", "claude").worker_workflow.endswith("reviewer-claude.lock.yml"), "reviewer claude worker mismatch")
    assert_true(resolve_role_worker("qa", "verification", "gemini").worker_workflow.endswith("qa-gemini.lock.yml"), "qa gemini worker mismatch")
    try:
        resolve_role_worker("reviewer", "design-review", "claude")
        raise AssertionError("design-review unexpectedly resolved to autonomous role worker")
    except RoleWorkerError:
        pass


def validate_candidate_contract():
    manifest = base_manifest("code-review", "WORKING", "draft")
    candidate = resolve_current_candidate(manifest, status="draft")
    assert_true(candidate.head_sha == SHA and candidate.pr_number == PR, "candidate resolution mismatch")
    moved = deepcopy(manifest)
    moved["artifacts"].append(deepcopy(moved["artifacts"][0]))
    try:
        resolve_current_candidate(moved, status="draft")
        raise AssertionError("ambiguous candidate unexpectedly resolved")
    except CandidateError:
        pass


def validate_reviewer_translation():
    manifest = base_manifest("code-review", "WORKING", "draft")
    event = translate(reviewer_result(), manifest, repository=REPO, target_ref=REF, current_pr_head_sha=SHA)
    kinds = [(c["kind"], c.get("id"), c.get("status")) for c in event["changes"]]
    assert_true(("gate", "code-gate", "PASS") in kinds, "Reviewer PASS did not pass code-gate")
    assert_true(("stage", "verification", "READY") in kinds, "Reviewer PASS did not ready verification")
    assert_true(any(c["kind"] == "artifact" and c["id"].startswith("implementation-candidate-") and c["status"] == "approved" for c in event["changes"]), "Reviewer PASS did not approve candidate")

    rework = translate(reviewer_result("REWORK"), manifest, repository=REPO, target_ref=REF, current_pr_head_sha=SHA)
    assert_true(any(c["kind"] == "task-record" and c["record"]["source_stage"] == "code-review" for c in rework["changes"]), "Reviewer REWORK did not create bounded remediation")
    assert_true(not any(c["kind"] == "gate" and c.get("status") == "PASS" for c in rework["changes"]), "Reviewer REWORK unexpectedly passed a Gate")

    stale = reviewer_result()
    stale["candidate_head_sha"] = "b" * 40
    try:
        translate(stale, manifest, repository=REPO, target_ref=REF, current_pr_head_sha=SHA)
        raise AssertionError("stale Reviewer candidate unexpectedly accepted")
    except GateResultError:
        pass


def validate_qa_translation():
    manifest = base_manifest("verification", "WORKING", "approved")
    event = translate(qa_result(), manifest, repository=REPO, target_ref=REF, current_pr_head_sha=SHA)
    kinds = [(c["kind"], c.get("id"), c.get("status")) for c in event["changes"]]
    assert_true(("gate", "verification-gate", "PASS") in kinds, "QA PASS did not pass verification-gate")
    assert_true(("stage", "acceptance", "READY") in kinds, "QA PASS did not ready acceptance")
    assert_true(not any(c.get("id") == "release-gate" for c in event["changes"]), "QA result attempted release-gate authority")

    failed = translate(qa_result("FAIL"), manifest, repository=REPO, target_ref=REF, current_pr_head_sha=SHA)
    assert_true(any(c["kind"] == "gate" and c.get("id") == "verification-gate" and c.get("status") == "FAIL" for c in failed["changes"]), "QA FAIL did not remain fail-closed")
    assert_true(not any(c["kind"] == "stage" and c.get("id") == "acceptance" and c.get("status") == "READY" for c in failed["changes"]), "QA FAIL advanced acceptance")

    extra = qa_result()
    extra["release_gate"] = "PASS"
    try:
        translate(extra, manifest, repository=REPO, target_ref=REF, current_pr_head_sha=SHA)
        raise AssertionError("QA additional authority field unexpectedly accepted")
    except GateResultError:
        pass


def main():
    validate_routes()
    validate_role_registry()
    validate_candidate_contract()
    validate_reviewer_translation()
    validate_qa_translation()
    print("gh-aw autonomous Reviewer/QA validation passed")


if __name__ == "__main__":
    main()
