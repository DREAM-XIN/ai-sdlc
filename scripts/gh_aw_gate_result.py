#!/usr/bin/env python3
"""Translate validated autonomous Reviewer/QA recommendations into bounded Feature Events."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from apply_feature_event import validate_event
from gh_aw_candidate import CandidateError, resolve_current_candidate

ROOT = Path(__file__).resolve().parents[1]
REVIEWER_SCHEMA = ROOT / "runtimes" / "gh-aw" / "reviewer-result.schema.json"
QA_SCHEMA = ROOT / "runtimes" / "gh-aw" / "qa-result.schema.json"


class GateResultError(ValueError):
    pass


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_schema(result: dict, schema_path: Path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = []
    for error in Draft202012Validator(schema).iter_errors(result):
        where = ".".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"result:{where}: {error.message}")
    if errors:
        raise GateResultError("; ".join(errors))


def _stage_status(manifest: dict, stage_id: str):
    matches = [s for s in manifest.get("workflow", {}).get("stages", []) if s.get("id") == stage_id]
    if len(matches) != 1:
        raise GateResultError(f"manifest must contain exactly one {stage_id} stage")
    return matches[0].get("status")


def _validate_identity(result: dict, manifest: dict, *, repository: str, target_ref: str, current_pr_head_sha: str, candidate_status: str):
    if result["feature_id"] != manifest.get("feature", {}).get("id"):
        raise GateResultError("result feature_id differs from manifest")
    if result["expected_revision"] != manifest.get("revision"):
        raise GateResultError("result expected_revision is stale")
    if result["target_repository"] != repository or result["target_ref"] != target_ref:
        raise GateResultError("result target repository/ref differs from trusted transport")
    candidate = resolve_current_candidate(manifest, status=candidate_status)
    if candidate.repository != repository:
        raise GateResultError("manifest candidate repository differs from trusted transport")
    if result["candidate_pr_number"] != candidate.pr_number:
        raise GateResultError("result candidate PR differs from manifest candidate")
    if result["candidate_head_sha"] != candidate.head_sha:
        raise GateResultError("result candidate SHA differs from manifest candidate")
    if current_pr_head_sha != candidate.head_sha:
        raise GateResultError("current PR head moved after candidate binding")
    return candidate


def _evidence_changes(result: dict):
    return [{"kind": "evidence", "record": dict(item)} for item in result["evidence"]]


def _review_feedback(result: dict):
    messages = [f"{item['severity']} {item['code']}: {item['message']}" for item in result.get("findings", [])]
    return result.get("reason") or " | ".join(messages) or "Autonomous Code Review requested remediation."


def reviewer_event(result: dict, manifest: dict, *, repository: str, target_ref: str, current_pr_head_sha: str):
    validate_schema(result, REVIEWER_SCHEMA)
    if _stage_status(manifest, "code-review") != "WORKING":
        raise GateResultError("code-review must be WORKING for autonomous Reviewer result")
    candidate = _validate_identity(
        result, manifest, repository=repository, target_ref=target_ref,
        current_pr_head_sha=current_pr_head_sha, candidate_status="draft",
    )
    changes = _evidence_changes(result)
    verdict = result["verdict"]
    if verdict == "PASS":
        if any(item.get("severity") in {"BLOCKER", "MAJOR"} for item in result["findings"]):
            raise GateResultError("Reviewer PASS cannot contain BLOCKER or MAJOR findings")
        if not all(item["status"] == "pass" for item in result["evidence"]):
            raise GateResultError("Reviewer PASS requires pass Evidence")
        evidence_ids = [item["id"] for item in result["evidence"]]
        reviewed_id = f"reviewed-candidate-head-{candidate.head_sha[:12]}"
        changes.extend([
            {"kind": "artifact", "id": candidate.artifact_id, "status": "approved", "evidence": evidence_ids},
            {"kind": "artifact", "id": candidate.head_artifact_id, "status": "approved", "evidence": evidence_ids},
            {"kind": "artifact-record", "record": {"id": reviewed_id, "type": "reviewed-candidate-head", "uri": candidate.head_url, "status": "draft"}},
            {"kind": "artifact", "id": reviewed_id, "status": "approved", "evidence": evidence_ids},
            {"kind": "gate", "id": "code-gate", "status": "PASS", "evidence": evidence_ids},
            {"kind": "stage", "id": "code-review", "status": "DONE"},
            {"kind": "stage", "id": "verification", "status": "READY"},
        ])
    elif verdict == "REWORK":
        if not result["findings"]:
            raise GateResultError("Reviewer REWORK requires at least one finding")
        task_id = f"{result['feature_id']}-CODE-REMEDIATION-R{result['expected_revision']}"
        changes.append({
            "kind": "task-record",
            "record": {
                "id": task_id,
                "kind": "remediation",
                "stage": "implementation",
                "role": "developer",
                "source_stage": "code-review",
                "feedback": _review_feedback(result),
                "target_pr": candidate.pr_url,
                "status": "TODO",
                "runtime": "gh-aw",
            },
        })
    else:
        changes.append({"kind": "stage", "id": "code-review", "status": "BLOCKED", "reason": result["reason"]})
    return _event(result, changes)


def _require_reviewed_head(manifest: dict, head_url: str):
    matches = [
        item for item in manifest.get("artifacts", [])
        if isinstance(item, dict)
        and item.get("type") == "reviewed-candidate-head"
        and item.get("status") == "approved"
        and item.get("uri") == head_url
    ]
    if len(matches) != 1:
        raise GateResultError("QA candidate is not bound to exactly one approved reviewed-candidate-head")


def qa_event(result: dict, manifest: dict, *, repository: str, target_ref: str, current_pr_head_sha: str):
    validate_schema(result, QA_SCHEMA)
    if _stage_status(manifest, "verification") != "WORKING":
        raise GateResultError("verification must be WORKING for autonomous QA result")
    candidate = _validate_identity(
        result, manifest, repository=repository, target_ref=target_ref,
        current_pr_head_sha=current_pr_head_sha, candidate_status="approved",
    )
    _require_reviewed_head(manifest, candidate.head_url)
    changes = _evidence_changes(result)
    verdict = result["verdict"]
    if verdict == "PASS":
        if any(item["status"] != "pass" for item in result["checks"] + result["coverage"]):
            raise GateResultError("QA PASS requires every check and acceptance criterion to pass")
        if not all(item["status"] == "pass" for item in result["evidence"]):
            raise GateResultError("QA PASS requires pass Evidence")
        evidence_ids = [item["id"] for item in result["evidence"]]
        verified_id = f"verified-candidate-head-{candidate.head_sha[:12]}"
        changes.extend([
            {"kind": "artifact-record", "record": {"id": verified_id, "type": "verified-candidate-head", "uri": candidate.head_url, "status": "draft"}},
            {"kind": "artifact", "id": verified_id, "status": "approved", "evidence": evidence_ids},
            {"kind": "gate", "id": "verification-gate", "status": "PASS", "evidence": evidence_ids},
            {"kind": "stage", "id": "verification", "status": "DONE"},
            {"kind": "stage", "id": "acceptance", "status": "READY"},
        ])
    elif verdict == "FAIL":
        changes.extend([
            {"kind": "gate", "id": "verification-gate", "status": "FAIL", "evidence": [item["id"] for item in result["evidence"]]},
            {"kind": "stage", "id": "verification", "status": "BLOCKED", "reason": result["reason"]},
        ])
    else:
        changes.append({"kind": "stage", "id": "verification", "status": "BLOCKED", "reason": result["reason"]})
    return _event(result, changes)


def _event(result: dict, changes: list[dict]):
    event = {
        "version": "0.1.0",
        "id": f"EVT-{result['id']}",
        "feature_id": result["feature_id"],
        "expected_revision": result["expected_revision"],
        "occurred_at": result["occurred_at"],
        "changes": changes,
    }
    errors = validate_event(event)
    if errors:
        raise GateResultError("invalid translated Feature Event: " + "; ".join(errors))
    return event


def translate(result: dict, manifest: dict, *, repository: str, target_ref: str, current_pr_head_sha: str):
    contract = result.get("contract")
    if contract == "ai-sdlc-gh-aw-reviewer-result-v0.1":
        return reviewer_event(result, manifest, repository=repository, target_ref=target_ref, current_pr_head_sha=current_pr_head_sha)
    if contract == "ai-sdlc-gh-aw-qa-result-v0.1":
        return qa_event(result, manifest, repository=repository, target_ref=target_ref, current_pr_head_sha=current_pr_head_sha)
    raise GateResultError("unsupported Gate-role result contract")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--current-pr-head-sha", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        event = translate(
            load_yaml(args.result), load_yaml(args.manifest), repository=args.repository,
            target_ref=args.target_ref, current_pr_head_sha=args.current_pr_head_sha,
        )
    except (CandidateError, GateResultError) as exc:
        print(json.dumps({"outcome": "INVALID", "errors": [str(exc)]}, indent=2))
        raise SystemExit(2)
    text = yaml.safe_dump(event, sort_keys=False)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
