#!/usr/bin/env python3
"""Zero-effect preflight for v0.3 full real-runtime fault injection.

This module decides only whether a trusted-main production stack and an explicit
release fixture are safe to hand to a later full-runtime runner. It never creates
an Operation, mutates the Store, dispatches a Worker, or persists a Feature Event.
"""
from __future__ import annotations

import re
from typing import Any

from v03_effect_safety_release_evidence import REQUIRED_PRODUCTION_PREREQUISITES
from v03_real_runtime_prerequisites import missing_prerequisites

SCHEMA_VERSION = "ai-sdlc.v0.3-full-runtime-preflight/v1"
RUNNABLE_STAGE_STATUSES = {
    "implementation": frozenset({"WORKING"}),
    "code-review": frozenset({"READY", "WORKING"}),
    "verification": frozenset({"READY", "WORKING"}),
}
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class FullRuntimePreflightError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FullRuntimePreflightError(message)


def evaluate_release_fixture(
    *,
    feature_id: str,
    target_ref: str,
    candidate_pr_number: int,
    pull_request: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate an explicit existing Feature/PR as a no-mutation full-runtime fixture."""
    _require(isinstance(feature_id, str) and bool(feature_id), "fixture Feature id is required")
    _require(isinstance(target_ref, str) and bool(target_ref), "fixture target ref is required")
    _require(target_ref != "main" and not target_ref.startswith("refs/"), "fixture target ref must be a non-default branch name")
    _require(isinstance(candidate_pr_number, int) and candidate_pr_number > 0, "fixture candidate PR number is invalid")
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
    _require(isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0, "fixture Manifest revision is invalid")
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


def build_full_runtime_preflight(
    *,
    prerequisites: dict[str, bool],
    fixture: dict[str, Any] | None = None,
    fixture_error: str | None = None,
) -> dict[str, Any]:
    """Return READY/BLOCKED preflight evidence; never release-eligible by itself."""
    _require(set(prerequisites) == set(REQUIRED_PRODUCTION_PREREQUISITES), "preflight prerequisite key set is not exact")
    missing = missing_prerequisites(prerequisites)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "release_eligible": False,
        "prerequisites": dict(prerequisites),
        "missing_prerequisites": missing,
        "fixture": fixture,
        "observations": {
            "store_mutation_attempted": False,
            "external_dispatch_attempted": False,
            "feature_persist_attempted": False,
        },
        "remaining_release_proof": [],
    }

    if missing:
        record["remaining_release_proof"] = [
            "all trusted-main production prerequisites must be present before full-runtime execution",
            f"missing prerequisites: {', '.join(missing)}",
        ]
        return record

    if fixture_error:
        record["remaining_release_proof"] = [
            "configure an existing exact runnable Feature/PR fixture without mutating its Manifest",
            fixture_error,
        ]
        return record

    if fixture is None:
        record["remaining_release_proof"] = [
            "configure an existing exact runnable Feature/PR fixture without mutating its Manifest"
        ]
        return record

    required_fixture_fields = {
        "feature_id",
        "target_ref",
        "feature_revision",
        "current_stage",
        "stage_status",
        "candidate_pr_number",
        "candidate_head_sha",
    }
    _require(set(fixture) == required_fixture_fields, "preflight fixture field set is not exact")
    record["status"] = "READY"
    record["remaining_release_proof"] = [
        "full-runtime runner must create/advance the durable Operation through trusted production composition",
        "READY preflight is not release PASS and authorizes no external effect by itself",
    ]
    return record
