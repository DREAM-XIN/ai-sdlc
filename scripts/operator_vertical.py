#!/usr/bin/env python3
"""Trusted pure helpers for the v0.3 Implementation→Review→QA vertical loop."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import json
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker

from operator_store_model import canonical_json, digest_json, normalize_repository

ROOT = Path(__file__).resolve().parents[1]
VERTICAL_SCHEMA_ROOT = ROOT / "spec" / "operator" / "vertical"
VERTICAL_PROFILE = "vertical-implementation-review-qa/v1"
ROLE_SCHEMAS = {
    "developer": "developer-result.schema.json",
    "reviewer": "reviewer-result.schema.json",
    "qa": "qa-result.schema.json",
}


class VerticalInvariantError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FeatureSnapshot:
    repository: str
    feature_id: str
    target_ref: str
    revision: int
    manifest_digest: str
    current_stage: str
    stages: dict[str, str]
    gates: dict[str, str]
    remediation_tasks: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]
    candidate_pr_number: int | None = None
    candidate_head_sha: str | None = None

    @classmethod
    def from_manifest(
        cls,
        *,
        repository: str,
        target_ref: str,
        manifest: dict[str, Any],
        candidate_pr_number: int | None = None,
        candidate_head_sha: str | None = None,
    ) -> "FeatureSnapshot":
        workflow = manifest.get("workflow") or {}
        stages = {str(row["id"]): str(row["status"]) for row in workflow.get("stages", [])}
        gates = {str(row["id"]): str(row["status"]) for row in manifest.get("gates", [])}
        tasks = tuple(
            dict(row)
            for row in manifest.get("tasks", [])
            if row.get("kind") == "remediation" and row.get("status") != "DONE"
        )
        return cls(
            repository=normalize_repository(repository),
            feature_id=str((manifest.get("feature") or {})["id"]),
            target_ref=target_ref,
            revision=int(manifest.get("revision", 0)),
            manifest_digest=digest_json(manifest),
            current_stage=str(workflow["current_stage"]),
            stages=stages,
            gates=gates,
            remediation_tasks=tasks,
            artifacts=tuple(dict(row) for row in manifest.get("artifacts", [])),
            candidate_pr_number=candidate_pr_number,
            candidate_head_sha=candidate_head_sha,
        )


@dataclass(frozen=True)
class TrustedDispatchContext:
    operation_id: str
    operation_generation: int
    operation_profile: str
    semantic_effect_key: str
    external_dispatch_key: str
    dispatch_id: str
    runtime_receipt_identity: str
    target_repository: str
    target_ref: str
    feature_id: str
    expected_revision: int
    feature_stage: str
    task_id: str
    role: str
    candidate_pr_number: int | None
    candidate_head_sha: str | None
    worker_identity: str
    collector_identity: str


@dataclass(frozen=True)
class RoleIndependencePolicy:
    developer_identity: str | None = None
    reviewer_identity: str | None = None
    remediation_developer_identity: str | None = None

    def verify(self, context: TrustedDispatchContext) -> None:
        worker = context.worker_identity
        if not worker:
            raise VerticalInvariantError("BLOCKED", "trusted Worker identity is required")
        if context.role == "reviewer":
            forbidden = {self.developer_identity, self.remediation_developer_identity} - {None}
            if worker in forbidden:
                raise VerticalInvariantError("POLICY_DENIED", "reviewer identity is not independent")
        elif context.role == "qa":
            forbidden = {
                self.developer_identity,
                self.reviewer_identity,
                self.remediation_developer_identity,
            } - {None}
            if worker in forbidden:
                raise VerticalInvariantError("POLICY_DENIED", "QA identity is not independent")
        elif context.role != "developer":
            raise VerticalInvariantError("INVALID_REQUEST", "unsupported vertical role")


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((VERTICAL_SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _validate_schema(instance: Any, schema_name: str) -> None:
    errors = sorted(
        Draft202012Validator(
            _load_schema(schema_name), format_checker=FormatChecker()
        ).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise VerticalInvariantError("INVALID_REQUEST", f"{location}: {error.message}")


def validate_worker_result(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    schema = ROLE_SCHEMAS.get(role)
    if schema is None:
        raise VerticalInvariantError("INVALID_REQUEST", "unsupported vertical role")
    if not isinstance(payload, dict):
        raise VerticalInvariantError("INVALID_REQUEST", "Worker result must be an object")
    _validate_schema(payload, schema)
    labels = [row["label"] for row in payload.get("outputs", [])]
    if len(labels) != len(set(labels)):
        raise VerticalInvariantError("INVALID_REQUEST", "Worker output labels must be unique")
    return dict(payload)


def _trusted_output_prefix(context: TrustedDispatchContext) -> str:
    return f"docs/features/{context.feature_id}/worker-runs/{context.dispatch_id}/"


def validate_collected_outputs(
    *,
    context: TrustedDispatchContext,
    feature: FeatureSnapshot,
    worker_payload: dict[str, Any],
    receipts: list[dict[str, Any]],
    content_loader: Callable[[str], bytes] | None = None,
) -> tuple[dict[str, Any], ...]:
    declared = {row["label"]: row["kind"] for row in worker_payload.get("outputs", [])}
    seen_labels: set[str] = set()
    seen_output_ids: set[str] = set()
    prefix = _trusted_output_prefix(context)
    validated: list[dict[str, Any]] = []

    for receipt in receipts:
        _validate_schema(receipt, "collected-output-receipt.schema.json")
        label = str(receipt["label"])
        if label in seen_labels or receipt["output_id"] in seen_output_ids:
            raise VerticalInvariantError("INVALID_REQUEST", "conflicting collected output receipt")
        if declared.get(label) != receipt["kind"]:
            raise VerticalInvariantError("INVALID_REQUEST", "collector receipt lacks matching Worker logical output")
        bindings = {
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
        }
        for key, expected in bindings.items():
            actual = receipt.get(key)
            if key == "target_repository":
                actual = normalize_repository(str(actual))
            if actual != expected:
                raise VerticalInvariantError("STALE_REVISION", f"collector receipt binding mismatch: {key}")
        if feature.repository != normalize_repository(context.target_repository):
            raise VerticalInvariantError("STALE_REVISION", "Feature repository binding changed")
        if feature.feature_id != context.feature_id or feature.revision != context.expected_revision:
            raise VerticalInvariantError("STALE_REVISION", "Feature revision binding changed")
        if feature.current_stage != context.feature_stage:
            raise VerticalInvariantError("STALE_REVISION", "Feature stage binding changed")
        if feature.candidate_head_sha != context.candidate_head_sha:
            raise VerticalInvariantError("STALE_REVISION", "candidate head binding changed")

        uri = str(receipt["trusted_uri"])
        path = PurePosixPath(uri)
        if path.is_absolute() or ".." in path.parts or not uri.startswith(prefix):
            raise VerticalInvariantError("POLICY_DENIED", "collected output is outside trusted namespace")
        if content_loader is not None:
            data = content_loader(uri)
            if not isinstance(data, bytes):
                raise VerticalInvariantError("INTERNAL_FAILURE", "collector content loader must return bytes")
            if len(data) != int(receipt["size_bytes"]):
                raise VerticalInvariantError("BLOCKED", "collected output size mismatch")
            if hashlib.sha256(data).hexdigest() != receipt["sha256"]:
                raise VerticalInvariantError("BLOCKED", "collected output digest mismatch")
        seen_labels.add(label)
        seen_output_ids.add(str(receipt["output_id"]))
        validated.append(dict(receipt))

    if set(declared) != seen_labels:
        raise VerticalInvariantError("BLOCKED", "declared Worker outputs were not fully materialized")
    return tuple(validated)


def _record_id(prefix: str, context: TrustedDispatchContext, receipt: dict[str, Any]) -> str:
    material = {
        "prefix": prefix,
        "role": context.role,
        "task": context.task_id,
        "dispatch": context.dispatch_id,
        "output": receipt["output_id"],
    }
    return f"{prefix}-{digest_json(material)[:20]}"


def _receipt_changes(
    *, context: TrustedDispatchContext, receipts: tuple[dict[str, Any], ...], pass_status: bool
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    changes: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    evidence_ids: list[str] = []
    for receipt in receipts:
        if receipt["kind"] == "artifact":
            record_id = _record_id("vertical-artifact", context, receipt)
            artifact_ids.append(record_id)
            changes.append(
                {
                    "kind": "artifact-record",
                    "record": {
                        "id": record_id,
                        "type": "implementation",
                        "uri": receipt["trusted_uri"],
                        "status": "draft",
                    },
                }
            )
        else:
            record_id = _record_id("vertical-evidence", context, receipt)
            evidence_ids.append(record_id)
            evidence_type = "review" if context.role == "reviewer" else "verification" if context.role == "qa" else "implementation"
            changes.append(
                {
                    "kind": "evidence",
                    "record": {
                        "id": record_id,
                        "type": evidence_type,
                        "status": "pass" if pass_status else "fail",
                        "uri": receipt["trusted_uri"],
                    },
                }
            )
    return changes, artifact_ids, evidence_ids


def _event(context: TrustedDispatchContext, feature: FeatureSnapshot, changes: list[dict[str, Any]], *, occurred_at: str, purpose: str) -> dict[str, Any]:
    event_id = "EVT-" + feature.feature_id + "-VERTICAL-" + purpose.upper().replace("_", "-") + "-" + digest_json(
        {"dispatch_id": context.dispatch_id, "revision": feature.revision, "changes": changes}
    )[:12].upper()
    return {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": feature.feature_id,
        "expected_revision": feature.revision,
        "occurred_at": occurred_at,
        "changes": changes,
    }


def translate_developer_result(
    *, context: TrustedDispatchContext, feature: FeatureSnapshot, payload: dict[str, Any], receipts: tuple[dict[str, Any], ...], occurred_at: str
) -> dict[str, Any] | None:
    if context.role != "developer":
        raise VerticalInvariantError("POLICY_DENIED", "Developer translator requires developer role")
    status = payload["status"]
    if status in {"BLOCKED", "NEEDS_USER"}:
        return None
    advisory_head = payload.get("candidate_head_sha")
    if advisory_head is not None and advisory_head != feature.candidate_head_sha:
        raise VerticalInvariantError("STALE_REVISION", "Developer advisory candidate head is stale")
    changes, _, _ = _receipt_changes(context=context, receipts=receipts, pass_status=True)
    remediation = next((task for task in feature.remediation_tasks if task["id"] == context.task_id), None)
    if remediation is not None:
        changes.append({"kind": "task", "id": remediation["id"], "status": "DONE"})
        return _event(context, feature, changes, occurred_at=occurred_at, purpose="code_remediation_done")
    if feature.current_stage != "implementation" or feature.stages.get("implementation") != "WORKING":
        raise VerticalInvariantError("STALE_REVISION", "Developer completion is not valid in current Feature stage")
    changes.extend(
        [
            {"kind": "stage", "id": "implementation", "status": "DONE"},
            {"kind": "stage", "id": "code-review", "status": "READY"},
        ]
    )
    return _event(context, feature, changes, occurred_at=occurred_at, purpose="implementation_done")


def _draft_implementation_artifact(feature: FeatureSnapshot) -> str:
    rows = [row for row in feature.artifacts if row.get("type") == "implementation" and row.get("status", "draft") == "draft"]
    if len(rows) != 1:
        raise VerticalInvariantError("BLOCKED", "Reviewer PASS requires exactly one draft implementation artifact")
    return str(rows[0]["id"])


def translate_reviewer_result(
    *, context: TrustedDispatchContext, feature: FeatureSnapshot, payload: dict[str, Any], receipts: tuple[dict[str, Any], ...], occurred_at: str
) -> dict[str, Any] | None:
    if context.role != "reviewer":
        raise VerticalInvariantError("POLICY_DENIED", "Reviewer translator requires reviewer role")
    verdict = payload["verdict"]
    if verdict in {"BLOCKED", "NEEDS_USER"}:
        return None
    changes, _, evidence_ids = _receipt_changes(context=context, receipts=receipts, pass_status=verdict == "PASS")
    if not evidence_ids:
        raise VerticalInvariantError("BLOCKED", "Reviewer result requires collected review evidence")
    if verdict == "PASS":
        artifact_id = _draft_implementation_artifact(feature)
        changes.extend(
            [
                {"kind": "artifact", "id": artifact_id, "status": "approved", "evidence": evidence_ids},
                {"kind": "gate", "id": "code-gate", "status": "PASS", "evidence": evidence_ids},
                {"kind": "stage", "id": "code-review", "status": "DONE"},
                {"kind": "stage", "id": "verification", "status": "READY"},
            ]
        )
        return _event(context, feature, changes, occurred_at=occurred_at, purpose="code_review_pass")

    findings = [row for row in payload.get("findings", []) if row["severity"] in {"BLOCKER", "MAJOR", "MINOR"}]
    if not findings:
        raise VerticalInvariantError("INVALID_REQUEST", "REWORK verdict requires actionable findings")
    feedback = "; ".join(f"[{row['severity']}/{row['code']}] {row['summary']}" for row in findings)[:4000]
    task_id = feature.feature_id + "-CODE-REMEDIATION-" + digest_json(
        {"candidate": feature.candidate_head_sha, "findings": findings}
    )[:12].upper()
    changes.append(
        {
            "kind": "task-record",
            "record": {
                "id": task_id,
                "kind": "remediation",
                "stage": "implementation",
                "role": "developer",
                "source_stage": "code-review",
                "feedback": feedback,
                "status": "TODO",
                "runtime": "operator-vertical",
            },
        }
    )
    return _event(context, feature, changes, occurred_at=occurred_at, purpose="code_review_rework")


def translate_qa_result(
    *, context: TrustedDispatchContext, feature: FeatureSnapshot, payload: dict[str, Any], receipts: tuple[dict[str, Any], ...], occurred_at: str
) -> dict[str, Any] | None:
    if context.role != "qa":
        raise VerticalInvariantError("POLICY_DENIED", "QA translator requires qa role")
    verdict = payload["verdict"]
    if verdict in {"BLOCKED", "NEEDS_USER"}:
        return None
    if verdict == "REWORK":
        raise VerticalInvariantError("BLOCKED", "QA REWORK is unsupported without an explicit repository lifecycle transition")
    changes, _, evidence_ids = _receipt_changes(context=context, receipts=receipts, pass_status=True)
    if not evidence_ids:
        raise VerticalInvariantError("BLOCKED", "QA PASS requires collected verification evidence")
    changes.extend(
        [
            {"kind": "gate", "id": "verification-gate", "status": "PASS", "evidence": evidence_ids},
            {"kind": "stage", "id": "verification", "status": "DONE"},
            {"kind": "stage", "id": "acceptance", "status": "READY"},
        ]
    )
    return _event(context, feature, changes, occurred_at=occurred_at, purpose="verification_pass")


def translate_result(
    *,
    context: TrustedDispatchContext,
    feature: FeatureSnapshot,
    worker_payload: dict[str, Any],
    receipts: list[dict[str, Any]],
    independence_policy: RoleIndependencePolicy,
    occurred_at: str,
    content_loader: Callable[[str], bytes] | None = None,
) -> dict[str, Any] | None:
    if context.operation_profile != VERTICAL_PROFILE:
        raise VerticalInvariantError("CAPABILITY_UNAVAILABLE", "Operation is not bound to the supported vertical profile")
    if feature.repository != normalize_repository(context.target_repository) or feature.feature_id != context.feature_id:
        raise VerticalInvariantError("STALE_REVISION", "trusted dispatch does not match current Feature")
    independence_policy.verify(context)
    payload = validate_worker_result(context.role, worker_payload)
    validated_receipts = validate_collected_outputs(
        context=context,
        feature=feature,
        worker_payload=payload,
        receipts=receipts,
        content_loader=content_loader,
    )
    if context.role == "developer":
        return translate_developer_result(context=context, feature=feature, payload=payload, receipts=validated_receipts, occurred_at=occurred_at)
    if context.role == "reviewer":
        return translate_reviewer_result(context=context, feature=feature, payload=payload, receipts=validated_receipts, occurred_at=occurred_at)
    return translate_qa_result(context=context, feature=feature, payload=payload, receipts=validated_receipts, occurred_at=occurred_at)
