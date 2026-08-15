#!/usr/bin/env python3
"""Trusted Operation-bound gh-aw result collector for the v0.3 Vertical runtime.

This module is deliberately downstream of the protected Operator Store launch
facts. A caller may provide an Operation id and stable external dispatch key as
routing hints, but neither a Worker payload nor a claimed Actions run id is
accepted as authority. The exact run/receipt must be re-resolved by a trusted
result source and must match the durable launch receipt before the callback is
handed to TrustedVerticalCallbackCoordinator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
from typing import Any, Protocol

from operator_store_model import digest_json, normalize_repository, operation_events, reservation_path
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError, validate_worker_result
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from operator_vertical_store import vertical_projection


@dataclass(frozen=True)
class TrustedGhAwRun:
    """Trusted GitHub Actions observation resolved independently of Worker JSON."""

    run_id: int
    run_url: str
    receipt_identity: str
    control_repository: str
    workflow_file: str
    workflow_ref: str
    event: str
    status: str
    conclusion: str
    display_title: str
    external_dispatch_key: str
    role: str
    task_id: str
    worker_identity: str
    collector_identity: str
    candidate_pr_number: int | None = None
    candidate_head_sha: str | None = None


@dataclass(frozen=True)
class MaterializedGhAwOutput:
    """One trusted collector-owned materialized output location."""

    label: str
    kind: str
    media_type: str
    trusted_uri: str


@dataclass(frozen=True)
class TrustedGhAwResolvedResult:
    run: TrustedGhAwRun
    role_payload: dict[str, Any]
    outputs: tuple[MaterializedGhAwOutput, ...]


class TrustedGhAwResultSource(Protocol):
    """Production adapter that re-resolves Actions truth and materialized outputs."""

    def resolve(
        self,
        *,
        external_dispatch_key: str,
        expected_receipt_identity: str,
    ) -> TrustedGhAwResolvedResult: ...


def _task_binding_matches(task_identity: str, task_id: str) -> bool:
    # Keep the exact accepted Vertical callback task-binding semantics instead
    # of defining a broader gh-aw-specific interpretation.
    if task_identity.startswith(("vertical:code-remediation:", "vertical:code-rereview:")):
        return f":{task_id}:" in task_identity
    return task_identity == task_id


def _current_launch_binding(snapshot, *, operation_id: str, external_dispatch_key: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    projection = vertical_projection(snapshot, operation_id)
    if projection.get("operation_profile") != VERTICAL_PROFILE:
        raise VerticalInvariantError("CAPABILITY_UNAVAILABLE", "Operation is not a trusted Vertical profile")
    generation = int(projection["generation"])

    launches: list[dict[str, Any]] = []
    lookup_observations: list[dict[str, Any]] = []
    for event in operation_events(snapshot, operation_id):
        if int(event.get("operation_generation", -1)) != generation:
            continue
        payload = event.get("payload") or {}
        if event.get("event_type") == "dispatch.launch.authorized" and payload.get("external_dispatch_key") == external_dispatch_key:
            launches.append(payload)
        if event.get("event_type") == "dispatch.launch.lookup-recorded" and payload.get("external_dispatch_key") == external_dispatch_key:
            lookup_observations.append(payload)

    if len(launches) != 1:
        raise VerticalInvariantError("INVALID_REQUEST", "collector requires exactly one current-generation launch authorization")
    launch = launches[0]
    if not lookup_observations:
        raise VerticalInvariantError("BLOCKED", "collector requires a durable launch lookup observation")
    latest_lookup = lookup_observations[-1]
    if latest_lookup.get("lookup_state") != "LAUNCHED" or not latest_lookup.get("receipt_id"):
        raise VerticalInvariantError("BLOCKED", "latest trusted launch state is not an exact LAUNCHED receipt")
    receipt_ids = tuple(
        dict.fromkeys(
            str(row["receipt_id"])
            for row in lookup_observations
            if row.get("lookup_state") == "LAUNCHED" and row.get("receipt_id")
        )
    )
    if len(receipt_ids) != 1 or receipt_ids[0] != str(latest_lookup["receipt_id"]):
        raise VerticalInvariantError("BLOCKED", "conflicting durable runtime receipt identities")

    semantic_effect_key = str(launch.get("semantic_effect_key") or "")
    if len(semantic_effect_key) != 64:
        raise VerticalInvariantError("INTERNAL_FAILURE", "durable launch lacks semantic effect identity")
    reservation = snapshot.get(reservation_path(semantic_effect_key))
    if not isinstance(reservation, dict):
        raise VerticalInvariantError("INTERNAL_FAILURE", "durable semantic reservation is missing")

    expected = {
        "external_dispatch_key": external_dispatch_key,
        "feature_id": projection["feature_id"],
        "expected_revision": projection["expected_feature_revision"],
        "current_stage": launch.get("stage"),
        "role": launch.get("role"),
    }
    for field, value in expected.items():
        if reservation.get(field) != value:
            raise VerticalInvariantError("STALE_REVISION", f"durable launch/reservation mismatch: {field}")
    return projection, launch, str(latest_lookup["receipt_id"])


def _validate_run(
    run: TrustedGhAwRun,
    *,
    control_repository: str,
    workflows: GhAwVerticalWorkflowMap,
    launch: dict[str, Any],
    expected_receipt_identity: str,
    external_dispatch_key: str,
    reservation: dict[str, Any],
) -> None:
    if run.run_id <= 0 or not run.run_url:
        raise VerticalInvariantError("INVALID_REQUEST", "trusted gh-aw run observation is incomplete")
    if normalize_repository(run.control_repository) != normalize_repository(control_repository):
        raise VerticalInvariantError("POLICY_DENIED", "gh-aw run is outside trusted control repository")
    if run.receipt_identity != expected_receipt_identity:
        raise VerticalInvariantError("STALE_REVISION", "gh-aw run receipt does not match durable launch receipt")
    if run.external_dispatch_key != external_dispatch_key:
        raise VerticalInvariantError("STALE_REVISION", "gh-aw run dispatch identity mismatch")
    role = str(launch.get("role") or "")
    if run.role != role:
        raise VerticalInvariantError("STALE_REVISION", "gh-aw run role mismatch")
    if run.workflow_file != workflows.workflow_for(role):
        raise VerticalInvariantError("POLICY_DENIED", "gh-aw run workflow is not the trusted role workflow")
    if run.workflow_ref != workflows.default_branch:
        raise VerticalInvariantError("POLICY_DENIED", "gh-aw run did not execute from trusted default branch")
    if run.event != "workflow_dispatch" or run.status != "completed" or run.conclusion != "success":
        raise VerticalInvariantError("BLOCKED", "gh-aw role run is not a successful trusted dispatch")
    if run.display_title != f"AI-SDLC gh-aw {external_dispatch_key}":
        raise VerticalInvariantError("STALE_REVISION", "gh-aw run-name does not bind the stable dispatch identity")
    task_identity = str(reservation.get("task_identity") or "")
    if not run.task_id or not _task_binding_matches(task_identity, run.task_id):
        raise VerticalInvariantError("STALE_REVISION", "gh-aw run task identity mismatch")
    if not run.worker_identity or not run.collector_identity:
        raise VerticalInvariantError("BLOCKED", "trusted Worker/collector identity is missing")
    launch_head = launch.get("candidate_head_sha")
    if role in {"reviewer", "qa"}:
        if not isinstance(run.candidate_pr_number, int) or run.candidate_pr_number <= 0:
            raise VerticalInvariantError("STALE_REVISION", "Gate-role run lacks exact candidate PR")
        if run.candidate_head_sha != launch_head:
            raise VerticalInvariantError("STALE_REVISION", "Gate-role run candidate head mismatch")


def _build_receipts(
    *,
    coordinator,
    context: TrustedDispatchContext,
    outputs: tuple[MaterializedGhAwOutput, ...],
    declared_outputs: dict[str, str],
    collected_at: str,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for output in outputs:
        if output.label in seen:
            raise VerticalInvariantError("INVALID_REQUEST", "duplicate trusted gh-aw output label")
        if output.kind not in {"artifact", "evidence"} or not output.media_type:
            raise VerticalInvariantError("INVALID_REQUEST", "trusted gh-aw output descriptor is invalid")
        if declared_outputs.get(output.label) != output.kind:
            raise VerticalInvariantError("BLOCKED", "trusted gh-aw output is not declared by the normalized role result")
        prefix = f"docs/features/{context.feature_id}/worker-runs/{context.dispatch_id}/"
        uri_path = PurePosixPath(output.trusted_uri)
        if uri_path.is_absolute() or ".." in uri_path.parts or not output.trusted_uri.startswith(prefix):
            raise VerticalInvariantError("POLICY_DENIED", "trusted gh-aw output is outside the collector namespace")
        data = coordinator.content_loader(output.trusted_uri)
        if not isinstance(data, bytes):
            raise VerticalInvariantError("INTERNAL_FAILURE", "trusted collector content loader must return bytes")
        sha256 = hashlib.sha256(data).hexdigest()
        output_id = "gh-aw-output-" + digest_json(
            {
                "operation_id": context.operation_id,
                "generation": context.operation_generation,
                "dispatch_id": context.dispatch_id,
                "label": output.label,
                "trusted_uri": output.trusted_uri,
                "sha256": sha256,
            }
        )[:24]
        receipts.append(
            {
                "output_id": output_id,
                "label": output.label,
                "kind": output.kind,
                "media_type": output.media_type,
                "trusted_uri": output.trusted_uri,
                "sha256": sha256,
                "size_bytes": len(data),
                "operation_id": context.operation_id,
                "operation_generation": context.operation_generation,
                "operation_profile": context.operation_profile,
                "semantic_effect_key": context.semantic_effect_key,
                "external_dispatch_key": context.external_dispatch_key,
                "dispatch_id": context.dispatch_id,
                "worker_role": context.role,
                "worker_identity": context.worker_identity,
                "target_repository": normalize_repository(context.target_repository),
                "feature_id": context.feature_id,
                "expected_revision": context.expected_revision,
                "candidate_head_sha": context.candidate_head_sha,
                "collector_identity": context.collector_identity,
                "collected_at": collected_at,
            }
        )
        seen.add(output.label)
    return receipts


class GhAwVerticalResultCollector:
    """Adopt one exact gh-aw completion through the existing Vertical callback authority."""

    def __init__(
        self,
        *,
        callback_coordinator,
        result_source: TrustedGhAwResultSource,
        workflows: GhAwVerticalWorkflowMap,
        control_repository: str,
        clock,
    ):
        if not normalize_repository(control_repository) or not callable(clock):
            raise ValueError("trusted gh-aw collector configuration is incomplete")
        self.callback_coordinator = callback_coordinator
        self.result_source = result_source
        self.workflows = workflows
        self.control_repository = normalize_repository(control_repository)
        self.clock = clock

    def handle(self, *, operation_id: str, external_dispatch_key: str) -> dict[str, Any]:
        if not operation_id or not external_dispatch_key:
            raise VerticalInvariantError("INVALID_REQUEST", "Operation id and stable dispatch key are required")
        executor = self.callback_coordinator.executor
        snapshot = executor.runtime.backend.read_snapshot()
        projection, launch, receipt_identity = _current_launch_binding(
            snapshot,
            operation_id=operation_id,
            external_dispatch_key=external_dispatch_key,
        )
        semantic_effect_key = str(launch["semantic_effect_key"])
        reservation = snapshot.get(reservation_path(semantic_effect_key))
        assert isinstance(reservation, dict)

        resolved = self.result_source.resolve(
            external_dispatch_key=external_dispatch_key,
            expected_receipt_identity=receipt_identity,
        )
        _validate_run(
            resolved.run,
            control_repository=self.control_repository,
            workflows=self.workflows,
            launch=launch,
            expected_receipt_identity=receipt_identity,
            external_dispatch_key=external_dispatch_key,
            reservation=reservation,
        )

        role = str(launch["role"])
        worker_payload = validate_worker_result(role, resolved.role_payload)
        context = TrustedDispatchContext(
            operation_id=operation_id,
            operation_generation=int(projection["generation"]),
            operation_profile=VERTICAL_PROFILE,
            semantic_effect_key=semantic_effect_key,
            external_dispatch_key=external_dispatch_key,
            dispatch_id=str(launch["dispatch_id"]),
            runtime_receipt_identity=receipt_identity,
            target_repository=str(projection["target_repository"]),
            target_ref=executor.config.target_ref,
            feature_id=str(projection["feature_id"]),
            expected_revision=int(projection["expected_feature_revision"]),
            feature_stage=str(launch["stage"]),
            task_id=resolved.run.task_id,
            role=role,
            candidate_pr_number=resolved.run.candidate_pr_number,
            candidate_head_sha=launch.get("candidate_head_sha"),
            worker_identity=resolved.run.worker_identity,
            collector_identity=resolved.run.collector_identity,
        )
        collected_at = str(self.clock())
        declared_map = {str(row["label"]): str(row["kind"]) for row in worker_payload.get("outputs", [])}
        receipts = _build_receipts(
            coordinator=self.callback_coordinator,
            context=context,
            outputs=resolved.outputs,
            declared_outputs=declared_map,
            collected_at=collected_at,
        )
        declared = set(declared_map.items())
        materialized = {(row["label"], row["kind"]) for row in receipts}
        if declared != materialized:
            raise VerticalInvariantError("BLOCKED", "trusted gh-aw materialized outputs do not match role result")

        callback_id = "gh-aw-callback-" + digest_json(
            {
                "operation_id": operation_id,
                "generation": context.operation_generation,
                "external_dispatch_key": external_dispatch_key,
                "runtime_receipt_identity": receipt_identity,
                "run_id": resolved.run.run_id,
            }
        )[:24]
        return self.callback_coordinator.handle(
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
        )
