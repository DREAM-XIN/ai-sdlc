#!/usr/bin/env python3
"""Translate a validated autonomous authoring recommendation into a bounded Feature Event."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import Draft202012Validator

from apply_feature_event import validate_event

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "runtimes" / "gh-aw" / "authoring-result.schema.json"

AUTHORING_MAP = {
    ("product", "requirement"): ("requirement", "requirement.md", "requirement-review"),
    ("architect", "design"): ("design", "design.md", "design-review"),
    ("orchestrator", "plan"): ("plan", "plan.md", "implementation"),
}


class AuthoringResultError(ValueError):
    pass


def _schema_errors(result):
    schema=json.loads(SCHEMA.read_text(encoding="utf-8")); errors=[]
    for error in Draft202012Validator(schema).iter_errors(result):
        location=".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"authoring-result:{location}: {error.message}")
    return errors


def canonical_artifact(feature_id: str, role: str, stage: str):
    try: artifact_type, filename, next_stage=AUTHORING_MAP[(role,stage)]
    except KeyError as exc: raise AuthoringResultError(f"unsupported autonomous authoring role/stage: {role}/{stage}") from exc
    path=PurePosixPath("docs","features",feature_id,filename)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("docs","features"):
        raise AuthoringResultError("canonical authoring path is unsafe")
    return artifact_type,path.as_posix(),next_stage


def _version_id(artifact_type: str, artifacts):
    pattern=re.compile(rf"^{re.escape(artifact_type)}-v([1-9][0-9]*)$")
    versions=[]
    for item in artifacts:
        match=pattern.match(str(item.get("id","")))
        if match: versions.append(int(match.group(1)))
    return f"{artifact_type}-v{max(versions,default=0)+1}"


def _task(manifest, task_id):
    matches=[t for t in manifest.get("tasks",[]) if t.get("id")==task_id]
    if len(matches)!=1: raise AuthoringResultError(f"no unique trusted task: {task_id}")
    return matches[0]


def translate(manifest, result, *, comment_url: str, occurred_at: str):
    errors=_schema_errors(result)
    if errors: raise AuthoringResultError("; ".join(errors))
    feature_id=manifest.get("feature",{}).get("id")
    if result["feature_id"] != feature_id: raise AuthoringResultError("feature identity mismatch")
    if result["expected_revision"] != manifest.get("revision",0): raise AuthoringResultError("authoring result revision is stale")
    role,stage=result["role"],result["stage"]
    artifact_type,artifact_uri,next_stage=canonical_artifact(feature_id,role,stage)
    stage_map={s["id"]:s for s in manifest["workflow"]["stages"]}
    work_kind=result["work_kind"]
    if work_kind=="stage":
        current=stage_map.get(stage)
        if not current or current.get("status")!="WORKING": raise AuthoringResultError("authoring stage is not WORKING")
    else:
        task=_task(manifest,result["task_id"])
        if task.get("kind")!="remediation" or task.get("stage")!=stage or task.get("role")!=role or task.get("status")!="WORKING":
            raise AuthoringResultError("authoring remediation task identity/status mismatch")

    digest=hashlib.sha256(f"{result['task_id']}:{result['expected_revision']}:{stage}".encode()).hexdigest()[:12]
    evidence_id=f"evidence-authoring-{stage}-{digest}"
    if any(e.get("id")==evidence_id for e in manifest.get("evidence",[])):
        raise AuthoringResultError("authoring result replay detected")
    evidence={"id":evidence_id,"type":"implementation","status":"pass" if result["status"]=="COMPLETED" else "fail","uri":comment_url}
    changes=[{"kind":"evidence","record":evidence}]

    if result["status"]=="BLOCKED":
        if work_kind=="remediation": changes.append({"kind":"task","id":result["task_id"],"status":"BLOCKED","reason":result["reason"]})
        else: changes.append({"kind":"stage","id":stage,"status":"BLOCKED","reason":result["reason"]})
    else:
        current_drafts=[a for a in manifest.get("artifacts",[]) if a.get("type")==artifact_type and a.get("status","draft")=="draft"]
        if len(current_drafts)>1: raise AuthoringResultError(f"multiple current draft artifacts for {artifact_type}")
        if current_drafts: changes.append({"kind":"artifact","id":current_drafts[0]["id"],"status":"superseded"})
        artifact_id=_version_id(artifact_type,manifest.get("artifacts",[]))
        changes.append({"kind":"artifact-record","record":{"id":artifact_id,"type":artifact_type,"uri":artifact_uri,"status":"draft"}})
        if work_kind=="remediation":
            changes.append({"kind":"task","id":result["task_id"],"status":"DONE"})
        else:
            changes.append({"kind":"stage","id":stage,"status":"DONE"})
            target=stage_map.get(next_stage)
            if not target or target.get("status")!="TODO": raise AuthoringResultError(f"next stage {next_stage} is not TODO")
            changes.append({"kind":"stage","id":next_stage,"status":"READY"})

    event_id=f"EVT-GHAW-AUTHORING-{feature_id}-{result['expected_revision']}-{digest}"
    event={"version":"0.1.0","id":event_id,"feature_id":feature_id,"expected_revision":result["expected_revision"],"occurred_at":occurred_at,"changes":changes}
    event_errors=validate_event(event)
    if event_errors: raise AuthoringResultError("; ".join(event_errors))
    return {"event":event,"artifact_uri":artifact_uri,"artifact_body":result["artifact_body"] if result["status"]=="COMPLETED" else None}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("manifest",type=Path)
    parser.add_argument("result",type=Path)
    parser.add_argument("--comment-url",required=True)
    parser.add_argument("--occurred-at",required=True)
    parser.add_argument("--event-output",type=Path,required=True)
    parser.add_argument("--artifact-output",type=Path)
    args=parser.parse_args()
    manifest=yaml.safe_load(args.manifest.read_text(encoding="utf-8")); result=json.loads(args.result.read_text(encoding="utf-8"))
    try: translated=translate(manifest,result,comment_url=args.comment_url,occurred_at=args.occurred_at)
    except AuthoringResultError as exc:
        print(json.dumps({"outcome":"INVALID","errors":[str(exc)]},indent=2)); raise SystemExit(2)
    args.event_output.write_text(yaml.safe_dump(translated["event"],sort_keys=False),encoding="utf-8")
    if args.artifact_output and translated["artifact_body"] is not None:
        args.artifact_output.write_text(translated["artifact_body"],encoding="utf-8")
    print(json.dumps({"outcome":"EVENT_READY","artifact_uri":translated["artifact_uri"],"event_id":translated["event"]["id"]},sort_keys=True))


if __name__=="__main__": main()
