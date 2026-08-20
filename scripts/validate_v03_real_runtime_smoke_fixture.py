#!/usr/bin/env python3
"""Deterministic validation for selector-derived real-runtime smoke fixtures."""
from __future__ import annotations

from operator_store_model import external_dispatch_key, semantic_effect_key, semantic_effect_material
from v03_real_runtime_smoke_fixture import RealRuntimeSmokeFixtureError, prepare_real_runtime_smoke_fixture

REPOSITORY = "DREAM-XIN/ai-sdlc"
FEATURE = "F-SMOKE-FIXTURE-0001"
REF = "feature/F-SMOKE-FIXTURE-0001"
PR = 991
HEAD = "a" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_rejected(callable_, message):
    try:
        callable_()
    except RealRuntimeSmokeFixtureError:
        return
    raise AssertionError(message)


def pull_request(*, state="open", ref=REF, sha=HEAD):
    return {"number": PR, "state": state, "head": {"ref": ref, "sha": sha}}


def manifest(stage: str, status: str, *, workflow_status="ACTIVE"):
    rows = []
    for stage_id in (
        "requirement",
        "requirement-review",
        "design",
        "design-review",
        "plan",
        "implementation",
        "code-review",
        "verification",
        "acceptance",
    ):
        if stage_id == stage:
            stage_status = status
        elif stage_id in {"requirement", "requirement-review", "design", "design-review", "plan"}:
            stage_status = "DONE"
        elif stage_id == "implementation" and stage in {"code-review", "verification", "acceptance"}:
            stage_status = "DONE"
        elif stage_id == "code-review" and stage in {"verification", "acceptance"}:
            stage_status = "DONE"
        elif stage_id == "verification" and stage == "acceptance":
            stage_status = "DONE"
        else:
            stage_status = "TODO"
        rows.append({"id": stage_id, "status": stage_status})
    return {
        "revision": 11,
        "feature": {"id": FEATURE, "title": "smoke fixture"},
        "workflow": {
            "status": workflow_status,
            "current_stage": stage,
            "stages": rows,
        },
        "tasks": [],
    }


def prepared(stage: str, status: str):
    return prepare_real_runtime_smoke_fixture(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        candidate_pr_number=PR,
        pull_request=pull_request(),
        manifest=manifest(stage, status),
        occurred_at="2026-08-11T00:00:00Z",
    )


def validate_dispatch_ready(stage, status, role, task_identity):
    row = prepared(stage, status)
    require(row["current_stage"] == stage and row["stage_status"] == status, "fixture lost exact stage/status")
    require(row["candidate_head_sha"] == HEAD and row["feature_revision"] == 11, "fixture lost exact candidate/revision")
    require(row["role"] == role, f"{stage} selected wrong role")
    require(row["task_identity"] == task_identity, f"{stage} selected wrong exact task identity")
    material = semantic_effect_material(
        target_repository=REPOSITORY,
        feature_id=FEATURE,
        expected_revision=11,
        current_stage=stage,
        task_identity=task_identity,
        role=role,
        candidate_head_sha=HEAD,
    )
    expected_semantic = semantic_effect_key(**material)
    require(row["semantic_effect_key"] == expected_semantic, "smoke fixture semantic key differs from production identity function")
    require(row["external_dispatch_key"] == external_dispatch_key(expected_semantic), "smoke fixture external key differs from production identity function")


def main():
    validate_dispatch_ready(
        "implementation",
        "WORKING",
        "developer",
        "vertical:implementation:11",
    )
    validate_dispatch_ready(
        "code-review",
        "WORKING",
        "reviewer",
        f"vertical:code-review:{HEAD}",
    )
    validate_dispatch_ready(
        "verification",
        "WORKING",
        "qa",
        f"vertical:verification:{HEAD}",
    )

    # READY review/verification first require a bounded Feature Persist to move
    # the stage to WORKING. The transport smoke may not skip that authority step.
    expect_rejected(lambda: prepared("code-review", "READY"), "code-review READY illegally skipped stage-start Persist")
    expect_rejected(lambda: prepared("verification", "READY"), "verification READY illegally skipped stage-start Persist")

    expect_rejected(
        lambda: prepare_real_runtime_smoke_fixture(
            repository=REPOSITORY,
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=pull_request(state="closed"),
            manifest=manifest("code-review", "WORKING"),
            occurred_at="2026-08-11T00:00:00Z",
        ),
        "closed candidate PR became a live smoke target",
    )
    expect_rejected(
        lambda: prepare_real_runtime_smoke_fixture(
            repository=REPOSITORY,
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=pull_request(ref="feature/foreign"),
            manifest=manifest("code-review", "WORKING"),
            occurred_at="2026-08-11T00:00:00Z",
        ),
        "cross-ref candidate PR became a live smoke target",
    )
    expect_rejected(
        lambda: prepare_real_runtime_smoke_fixture(
            repository=REPOSITORY,
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=pull_request(),
            manifest=manifest("acceptance", "READY"),
            occurred_at="2026-08-11T00:00:00Z",
        ),
        "acceptance Feature became a live smoke target",
    )
    expect_rejected(
        lambda: prepare_real_runtime_smoke_fixture(
            repository=REPOSITORY,
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=pull_request(),
            manifest=manifest("code-review", "WORKING", workflow_status="DONE"),
            occurred_at="2026-08-11T00:00:00Z",
        ),
        "DONE workflow became a live smoke target",
    )

    print("v0.3 real-runtime smoke fixture validation passed")
    print("- implementation/code-review/verification WORKING derive developer/reviewer/qa through accepted selector")
    print("- semantic/external effect keys exactly match production identity functions")
    print("- review/verification READY cannot skip required stage-start Persist")
    print("- closed/cross-ref/acceptance/DONE fixtures fail closed before external dispatch")


if __name__ == "__main__":
    main()
