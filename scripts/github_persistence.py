#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import Draft202012Validator

from apply_feature_event import apply_event

ROOT = Path(__file__).resolve().parents[1]
PLAN_SCHEMA = ROOT / "spec" / "github-persistence-plan.schema.json"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def validate_plan(plan):
    with PLAN_SCHEMA.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(plan):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"plan:{location}: {error.message}")
    return errors


def validate_manifest_path(value: str):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return "manifest path must be repository-relative without parent traversal"
    if len(path.parts) < 3 or path.parts[:2] != ("state", "features"):
        return "manifest path must be under state/features/"
    if path.suffix not in {".yaml", ".yml"}:
        return "manifest path must end in .yaml or .yml"
    return None


def conclusion_for(status: str) -> str:
    if status == "DONE":
        return "success"
    if status == "BLOCKED":
        return "failure"
    return "neutral"


def build_plan(manifest, event, repository: str, manifest_path: str, target_ref: str, issue: int | None = None):
    path_error = validate_manifest_path(manifest_path)
    if path_error:
        return {"outcome": "INVALID", "errors": [path_error]}
    if not repository.strip() or not target_ref.strip():
        return {"outcome": "INVALID", "errors": ["repository and target_ref must be non-empty"]}

    applied = apply_event(manifest, event)
    if applied["outcome"] != "APPLIED":
        return {"outcome": "INVALID", "errors": applied["errors"]}

    updated = applied["manifest"]
    content = yaml.safe_dump(updated, sort_keys=False)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    status = updated["workflow"]["status"]
    current_stage = updated["workflow"]["current_stage"]
    feature_id = updated["feature"]["id"]
    message = f"{feature_id}: workflow={status}, current_stage={current_stage}"

    mutations = [
        {
            "kind": "update-file",
            "path": manifest_path,
            "ref": target_ref,
            "sha256": digest,
        },
        {
            "kind": "check-run",
            "name": f"AI-SDLC {feature_id}",
            "conclusion": conclusion_for(status),
            "summary": message,
        },
    ]
    if issue is not None:
        mutations.append(
            {
                "kind": "issue-comment",
                "issue": issue,
                "body": f"<!-- ai-sdlc-feature-status:{feature_id} -->\n{message}\nManifest: `{manifest_path}` @ `{target_ref}`",
            }
        )

    plan = {
        "version": "0.1.0",
        "repository": repository,
        "target": {
            "ref": target_ref,
            "manifest_path": manifest_path,
            **({"issue": issue} if issue is not None else {}),
        },
        "manifest": {
            "feature_id": feature_id,
            "sha256": digest,
            "content": content,
        },
        "mutations": mutations,
        "summary": {
            "workflow_status": status,
            "current_stage": current_stage,
            "message": message,
        },
    }
    errors = validate_plan(plan)
    if errors:
        return {"outcome": "INVALID", "errors": errors}
    return {"outcome": "PLANNED", "errors": [], "plan": plan}


def main():
    parser = argparse.ArgumentParser(description="Build a deterministic GitHub persistence plan")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("event", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--issue", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = build_plan(
        load_yaml(args.manifest),
        load_yaml(args.event),
        repository=args.repository,
        manifest_path=args.manifest_path,
        target_ref=args.target_ref,
        issue=args.issue,
    )
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
