#!/usr/bin/env python3
"""Deterministic orchestration planner and trusted resume boundary for the v0.3 vertical loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from operator_store_model import digest_json, rebuild_projection
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE, VerticalInvariantError


@dataclass(frozen=True)
class VerticalAction:
    kind: str
    step: str
    role: str | None = None
    task_id: str | None = None
    task_identity: str | None = None
    candidate_head_sha: str | None = None
    feature_event: dict[str, Any] | None = None


class FeatureTruthGateway(Protocol):
    def read_feature(self, *, operation_id: str) -> tuple[FeatureSnapshot, dict[str, Any]]: ...


class VerticalExecutor(Protocol):
    def advance_action(self, *, operation_id: str, action: VerticalAction) -> dict[str, Any]: ...


def _start_event(feature: FeatureSnapshot, changes: list[dict[str, Any]], purpose: str, occurred_at: str) -> dict[str, Any]:
    event_id = "EVT-" + feature.feature_id + "-VERTICAL-" + purpose + "-" + digest_json(
        {"revision": feature.revision, "changes": changes}
    )[:12].upper()
    return {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": feature.feature_id,
        "expected_revision": feature.revision,
        "occurred_at": occurred_at,
        "changes": changes,
    }


def _remediation_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in manifest.get("tasks", []) if row.get("kind") == "remediation" and row.get("source_stage") == "code-review"]


def select_vertical_action(*, feature: FeatureSnapshot, manifest: dict[str, Any], occurred_at: str) -> VerticalAction:
    """Choose exactly one next trusted action from current authoritative Feature truth."""
    if feature.current_stage == "implementation":
        if feature.stages.get("implementation") != "WORKING":
            raise VerticalInvariantError("BLOCKED", "vertical loop requires implementation WORKING")
        task_identity = f"vertical:implementation:{feature.revision}"
        return VerticalAction(
            kind="dispatch",
            step="IMPLEMENTATION_WORK",
            role="developer",
            task_id=task_identity,
            task_identity=task_identity,
            candidate_head_sha=feature.candidate_head_sha,
        )

    if feature.current_stage == "code-review":
        stage_status = feature.stages.get("code-review")
        remediations = _remediation_rows(manifest)
        active = [row for row in remediations if row.get("status") != "DONE"]
        if active:
            if len(active) != 1:
                raise VerticalInvariantError("BLOCKED", "multiple active code remediation tasks are unsupported")
            task = active[0]
            status = task.get("status")
            if status in {"TODO", "READY"}:
                event = _start_event(
                    feature,
                    [{"kind": "task", "id": task["id"], "status": "WORKING"}],
                    "CODE-REMEDIATION-START",
                    occurred_at,
                )
                return VerticalAction(kind="persist", step="CODE_REMEDIATION", task_id=task["id"], feature_event=event)
            if status == "WORKING":
                if not feature.candidate_head_sha:
                    raise VerticalInvariantError("BLOCKED", "code remediation requires trusted candidate head")
                identity = f"vertical:code-remediation:{task['id']}:{feature.candidate_head_sha}"
                return VerticalAction(kind="dispatch", step="CODE_REMEDIATION", role="developer", task_id=task["id"], task_identity=identity, candidate_head_sha=feature.candidate_head_sha)
            raise VerticalInvariantError("BLOCKED", "unsupported remediation task status")

        if stage_status == "READY":
            event = _start_event(
                feature,
                [{"kind": "stage", "id": "code-review", "status": "WORKING"}],
                "CODE-REVIEW-START",
                occurred_at,
            )
            return VerticalAction(kind="persist", step="CODE_REVIEW", feature_event=event)
        if stage_status != "WORKING":
            raise VerticalInvariantError("BLOCKED", "code review is not runnable")
        if not feature.candidate_head_sha:
            raise VerticalInvariantError("BLOCKED", "code review requires trusted candidate head")
        completed = [row for row in remediations if row.get("status") == "DONE"]
        if completed:
            latest = sorted(completed, key=lambda row: str(row["id"]))[-1]
            identity = f"vertical:code-rereview:{latest['id']}:{feature.candidate_head_sha}"
            return VerticalAction(kind="dispatch", step="CODE_REREVIEW", role="reviewer", task_id=latest["id"], task_identity=identity, candidate_head_sha=feature.candidate_head_sha)
        identity = f"vertical:code-review:{feature.candidate_head_sha}"
        return VerticalAction(kind="dispatch", step="CODE_REVIEW", role="reviewer", task_id=identity, task_identity=identity, candidate_head_sha=feature.candidate_head_sha)

    if feature.current_stage == "verification":
        status = feature.stages.get("verification")
        if status == "READY":
            event = _start_event(
                feature,
                [{"kind": "stage", "id": "verification", "status": "WORKING"}],
                "VERIFICATION-START",
                occurred_at,
            )
            return VerticalAction(kind="persist", step="VERIFICATION_QA", feature_event=event)
        if status != "WORKING":
            raise VerticalInvariantError("BLOCKED", "verification is not runnable")
        if not feature.candidate_head_sha:
            raise VerticalInvariantError("BLOCKED", "verification requires trusted candidate head")
        identity = f"vertical:verification:{feature.candidate_head_sha}"
        return VerticalAction(kind="dispatch", step="VERIFICATION_QA", role="qa", task_id=identity, task_identity=identity, candidate_head_sha=feature.candidate_head_sha)

    if feature.current_stage == "acceptance" and feature.stages.get("acceptance") == "READY":
        if feature.gates.get("verification-gate") != "PASS":
            raise VerticalInvariantError("BLOCKED", "acceptance READY without verification PASS")
        return VerticalAction(kind="done", step="VERIFICATION_QA")

    raise VerticalInvariantError("BLOCKED", f"unsupported vertical Feature state: {feature.current_stage}")


class VerticalLoopResumeBackend:
    """Canonical operation.resume backend over a trusted vertical executor.

    The executor owns Store CAS, Feature/Persist gateways, dispatch/collector gateways and
    callback reconciliation. This backend only authorizes the profile and delegates one
    deterministic advance transaction; canonical input cannot choose a profile.
    """

    def __init__(self, *, runtime, feature_gateway: FeatureTruthGateway, executor: VerticalExecutor):
        self.runtime = runtime
        self.feature_gateway = feature_gateway
        self.executor = executor

    def availability(self, capability, trusted_context):
        try:
            receipt = self.runtime.protected_receipt()
            receipt.validate_for(self.runtime.backend.repository, self.runtime.backend.state_ref)
        except Exception:
            return False, "POLICY_RESTRICTED"
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        operation_id = (request.get("context") or {}).get("operation_id")
        if not operation_id:
            raise VerticalInvariantError("INVALID_REQUEST", "context.operation_id is required")
        projection = rebuild_projection(self.runtime.backend.read_snapshot(), operation_id)
        if projection.get("operation_profile") != VERTICAL_PROFILE:
            raise VerticalInvariantError("CAPABILITY_UNAVAILABLE", "Operation is not bound to the supported vertical profile")
        expected = (request.get("context") or {}).get("expected_feature_revision")
        feature, manifest = self.feature_gateway.read_feature(operation_id=operation_id)
        if expected != feature.revision:
            raise VerticalInvariantError("STALE_REVISION", "trusted Feature revision no longer matches resume request")
        action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=self.runtime.clock())
        result = self.executor.advance_action(operation_id=operation_id, action=action)
        if not isinstance(result, dict) or result.get("operation_id") != operation_id:
            raise VerticalInvariantError("INTERNAL_FAILURE", "vertical executor returned invalid result")
        return result
