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

from typing import Any

from operator_store_model import external_dispatch_key, semantic_effect_key, semantic_effect_material
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import select_vertical_action
from v03_real_runtime_full_preflight import FullRuntimePreflightError, evaluate_release_fixture


class RealRuntimeSmokeFixtureError(ValueError):
    pass


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
        fixture = evaluate_release_fixture(
            feature_id=feature_id,
            target_ref=target_ref,
            candidate_pr_number=candidate_pr_number,
            pull_request=pull_request,
            manifest=manifest,
        )
    except (FullRuntimePreflightError, ValueError, KeyError, TypeError) as exc:
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
