#!/usr/bin/env python3
"""Pure trusted fixture preparation for the v0.3 transport-level real smoke.

This helper performs no GitHub I/O and no external effect. It validates an
explicit existing Feature/PR fixture, asks the accepted Vertical selector for
its exact next action, and derives the same semantic/external identity material
used by the production Store/executor. The transport-only smoke may run only
when that next action is already a dispatch; it never skips a required Persist
stage-start merely to reach a Worker launch.
"""
from __future__ import annotations

import re
from typing import Any

from operator_store_model import external_dispatch_key, semantic_effect_key, semantic_effect_material
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import select_vertical_action


RUNNABLE_STAGE_STATUSES = {
    "implementation": frozenset({"WORKING"}),
    "code-review": frozenset({"READY", "WORKING"}),
    "verification": frozenset({"READY", "WORKING"}),
}
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class RealRuntimeSmokeFixtureError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RealRuntimeSmokeFixtureError(message)


def _evaluate_release_fixture(
    *,
    feature_id: str,
    target_ref: str,
    candidate_pr_number: int,
    pull_request: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate an explicit existing Feature/PR without depending on support-module internals."""
    _require(isinstance(feature_id, str) and bool(feature_id), "fixture Feature id is required")
    _require(isinstance(target_ref, str) and bool(target_ref), "fixture target ref is required")
    _require(
        target_ref != "main" and not target_ref.startswith("refs/"),
        "fixture target ref must be a non-default branch name",
    )
    _require(
        isinstance(candidate_pr_number, int) and not isinstance(candidate_pr_number, bool) and candidate_pr_number > 0,
        "fixture candidate PR number is invalid",
    )
    _require(isinstance(pull_request, dict), "fixture pull request response is invalid")
    _require(isinstance(manifest, dict), "fixture Feature Manifest is invalid")

    pr_number = pull_request.get("number", candidate_pr_number)
    _require(pr_number == candidate_pr_number, "fixture PR identity changed")
    _require(pull_request.get("state") == "open", "fixture candidate PR is not open")
    head = pull_request.get("head") or {}
    _require(isinstance(head, dict), "fixture candidate PR lacks head identity")
    _require(head.get("ref") == target_ref, "fixture candidate PR ref differs from trusted target ref")
    candidate_head_sha = str(head.get("sha") or "")
    _require(bool(_SHA40.fullmatch(candidate_head_sha)), "fixture candidate PR lacks exact 40-char head SHA")

    feature = manifest.get("feature") or {}
    workflow = manifest.get("workflow") or {}
    _require(isinstance(feature, dict) and feature.get("id") == feature_id, "fixture Manifest Feature identity mismatch")
    revision = manifest.get("revision")
    _require(
        isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0,
        "fixture Manifest revision is invalid",
    )
    _require(isinstance(workflow, dict) and workflow.get("status") == "ACTIVE", "fixture Feature workflow is not ACTIVE")
    current_stage = str(workflow.get("current_stage") or "")
    _require(current_stage in RUNNABLE_STAGE_STATUSES, "fixture Feature stage is not supported by the Vertical runner")

    stages = workflow.get("stages")
    _require(isinstance(stages, list), "fixture Feature workflow stages are invalid")
    current_rows = [row for row in stages if isinstance(row, dict) and row.get("id") == current_stage]
    _require(len(current_rows) == 1, "fixture current stage must have exactly one stage row")
    stage_status = str(current_rows[0].get("status") or "")
    _require(
        stage_status in RUNNABLE_STAGE_STATUSES[current_stage],
        f"fixture {current_stage} stage status is not runnable by the Vertical selector",
    )

    return {
        "feature_id": feature_id,
        "target_ref": target_ref,
        "feature_revision": revision,
        "current_stage": current_stage,
        "stage_status": stage_status,
        "candidate_pr_number": candidate_pr_number,
        "candidate_head_sha": candidate_head_sha,
    }


def prepare_real_runtime_smoke_fixture(
    *,
    repository: str,
    feature_id: str,
    target_ref: str,
    candidate_pr_number: int,
    pull_request: dict[str, Any],
    manifest: dict[str, Any],
    occurred_at: str,
) -> dict[str, Any]:
    """Return exact selector-derived dispatch identity or fail closed."""
    try:
        fixture = _evaluate_release_fixture(
            feature_id=feature_id,
            target_ref=target_ref,
            candidate_pr_number=candidate_pr_number,
            pull_request=pull_request,
            manifest=manifest,
        )
    except (RealRuntimeSmokeFixtureError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, RealRuntimeSmokeFixtureError):
            raise
        raise RealRuntimeSmokeFixtureError(str(exc)) from exc

    candidate_head = str(fixture["candidate_head_sha"])
    feature = FeatureSnapshot.from_manifest(
        repository=repository,
        target_ref=target_ref,
        manifest=manifest,
        candidate_pr_number=candidate_pr_number,
        candidate_head_sha=candidate_head,
    )
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=occurred_at)
    if action.kind != "dispatch":
        raise RealRuntimeSmokeFixtureError(
            f"transport-level smoke requires an immediate dispatch action; accepted selector returned {action.kind}:{action.step}"
        )
    if not action.role or not action.task_id or not action.task_identity:
        raise RealRuntimeSmokeFixtureError("accepted dispatch action lacks exact role/task identity")
    if action.candidate_head_sha != candidate_head:
        raise RealRuntimeSmokeFixtureError("accepted dispatch action candidate binding differs from exact PR head")

    material = semantic_effect_material(
        target_repository=repository,
        feature_id=feature_id,
        expected_revision=feature.revision,
        current_stage=feature.current_stage,
        task_identity=str(action.task_identity),
        role=str(action.role),
        candidate_head_sha=action.candidate_head_sha,
    )
    semantic_key = semantic_effect_key(**material)
    return {
        **fixture,
        "role": str(action.role),
        "step": str(action.step),
        "task_id": str(action.task_id),
        "task_identity": str(action.task_identity),
        "semantic_effect_key": semantic_key,
        "external_dispatch_key": external_dispatch_key(semantic_key),
    }
