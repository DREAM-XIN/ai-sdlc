#!/usr/bin/env python3
"""Trusted provenance writer for v0.3 Issue #221 live evidence artifacts.

This helper is intentionally post-effect and side-effect-free with respect to the
Operator runtime. It reads already-retained evidence bytes, verifies the exact
trusted-main/live-policy authority object supplied by the release preflight, and
writes only local JSON authority/provenance files for the closed #221 ledger.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from operator_store_model import normalize_repository
from operator_store_protection import require_protected

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class LiveEvidenceProvenanceError(ValueError):
    pass


def _exact_sha(value: object, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA40.fullmatch(text):
        raise LiveEvidenceProvenanceError(f"exact {label} SHA is required")
    return text


def _positive_run_id(value: object) -> int:
    try:
        run_id = int(str(value))
    except Exception as exc:
        raise LiveEvidenceProvenanceError("positive GitHub workflow run id is required") from exc
    if run_id < 1 or str(run_id) != str(value).strip():
        raise LiveEvidenceProvenanceError("positive canonical GitHub workflow run id is required")
    return run_id


def _executor_config(preflight):
    bundle = getattr(getattr(preflight, "composition", None), "bundle", None)
    executor = getattr(bundle, "executor", None)
    base = getattr(executor, "base", executor)
    config = getattr(base, "config", None)
    if config is None:
        raise LiveEvidenceProvenanceError("full-runtime preflight lacks exact executor config")
    if getattr(config, "effect_lineage_required", None) is not True:
        raise LiveEvidenceProvenanceError("live evidence executor is not Effect-Lineage-required")
    if getattr(config, "old_writers_quiesced", None) is not True:
        raise LiveEvidenceProvenanceError("live evidence executor lacks quiesced writer fence")
    return config


def live_authority_document(*, preflight, workflow_sha: str) -> dict[str, Any]:
    """Build the exact authority anchor consumed by the closed #221 ledger."""
    execution = getattr(preflight, "execution", None)
    live = getattr(preflight, "live_authority", None)
    composition = getattr(preflight, "composition", None)
    if execution is None or live is None or composition is None:
        raise LiveEvidenceProvenanceError("full-runtime live preflight is required")

    installation = _exact_sha(getattr(execution, "installation_commit_sha", None), "installation")
    workflow = _exact_sha(workflow_sha, "workflow")
    if workflow != installation:
        raise LiveEvidenceProvenanceError("workflow SHA differs from trusted-main installation")
    repository = normalize_repository(str(getattr(execution, "repository", "")))
    state_ref = str(getattr(execution, "state_ref", ""))
    try:
        require_protected(
            getattr(live, "protection_receipt", None),
            repository=repository,
            state_ref=state_ref,
        )
    except Exception as exc:
        raise LiveEvidenceProvenanceError("live evidence authority is not stably PROTECTED") from exc

    policy = getattr(live, "policy", None)
    materialization = _exact_sha(getattr(live, "materialization_commit_sha", None), "materialization")
    if getattr(policy, "installation_commit_sha", None) != installation:
        raise LiveEvidenceProvenanceError("policy authority installation differs from workflow installation")
    if getattr(policy, "materialization_commit_sha", None) != materialization:
        raise LiveEvidenceProvenanceError("policy authority materialization anchor differs from live authority")
    bundle_digest = str(getattr(policy, "bundle_digest", ""))
    if len(bundle_digest) != 64 or any(ch not in "0123456789abcdef" for ch in bundle_digest):
        raise LiveEvidenceProvenanceError("exact policy bundle digest is required")

    _executor_config(preflight)
    feature_id = str(getattr(composition, "feature_id", ""))
    target_ref = str(getattr(composition, "target_ref", ""))
    if not feature_id or not target_ref:
        raise LiveEvidenceProvenanceError("live evidence Feature/ref scope is incomplete")

    return {
        "schema_version": "ai-sdlc.v03-effect-safety-live-authority/v1",
        "repository": repository,
        "feature_id": feature_id,
        "target_ref": target_ref,
        "trusted_main_head_sha": installation,
        "materialization_commit_sha": materialization,
        "policy_bundle_digest": bundle_digest,
        "runtime_kind": "gh-aw-actions",
        "protected_policy_status": "PROTECTED",
        "effect_lineage_required": True,
        "writer_fence_quiesced": True,
    }


def live_provenance_document(
    *,
    preflight,
    evidence_path: Path,
    github_workflow_run_id: object,
    workflow_sha: str,
    record_id: str | None = None,
) -> dict[str, Any]:
    authority = live_authority_document(preflight=preflight, workflow_sha=workflow_sha)
    path = Path(evidence_path)
    try:
        raw = path.read_bytes()
    except Exception as exc:
        raise LiveEvidenceProvenanceError("live evidence artifact is not readable") from exc
    if not raw:
        raise LiveEvidenceProvenanceError("live evidence artifact is empty")
    try:
        document = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise LiveEvidenceProvenanceError("live evidence artifact is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or not document.get("schema_version"):
        raise LiveEvidenceProvenanceError("live evidence artifact lacks schema identity")

    run_id = _positive_run_id(github_workflow_run_id)
    digest = hashlib.sha256(raw).hexdigest()
    identifier = str(record_id or f"issue-221:{path.stem}:{run_id}:{digest[:16]}")
    if not identifier or len(identifier) > 160:
        raise LiveEvidenceProvenanceError("live evidence record id is invalid")

    return {
        "schema_version": "ai-sdlc.v03-live-evidence-provenance/v1",
        "evidence_class": "release-live-real-runtime",
        "record_id": identifier,
        "artifact_sha256": digest,
        "github_workflow_run_id": run_id,
        "trusted_main_head_sha": authority["trusted_main_head_sha"],
        "repository": authority["repository"],
        "feature_id": authority["feature_id"],
        "target_ref": authority["target_ref"],
        "materialization_commit_sha": authority["materialization_commit_sha"],
        "policy_bundle_digest": authority["policy_bundle_digest"],
        "runtime_kind": authority["runtime_kind"],
        "protected_policy_status": authority["protected_policy_status"],
        "effect_lineage_required": authority["effect_lineage_required"],
        "writer_fence_quiesced": authority["writer_fence_quiesced"],
    }


def write_live_evidence_envelope(
    *,
    preflight,
    evidence_path: Path,
    provenance_path: Path,
    authority_path: Path,
    github_workflow_run_id: object,
    workflow_sha: str,
    record_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    authority = live_authority_document(preflight=preflight, workflow_sha=workflow_sha)
    provenance = live_provenance_document(
        preflight=preflight,
        evidence_path=evidence_path,
        github_workflow_run_id=github_workflow_run_id,
        workflow_sha=workflow_sha,
        record_id=record_id,
    )
    for path, value in (
        (Path(authority_path), authority),
        (Path(provenance_path), provenance),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return authority, provenance
