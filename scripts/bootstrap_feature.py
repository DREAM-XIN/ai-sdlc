#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from validate_feature_manifest import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCHEMA = ROOT / "spec" / "feature-bootstrap.schema.json"
PROFILES = ROOT / "profiles"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def validate_bootstrap(doc):
    with BOOTSTRAP_SCHEMA.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(doc):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"bootstrap:{location}: {error.message}")
    return errors


def load_profile(profile_id: str):
    path = PROFILES / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"workflow profile not found: {profile_id}")
    profile = load_yaml(path)
    if profile.get("id") != profile_id:
        raise ValueError(f"workflow profile id mismatch: expected={profile_id} actual={profile.get('id')}")
    return profile


def build_manifest(bootstrap, profile):
    errors = validate_bootstrap(bootstrap)
    if errors:
        return {"outcome": "INVALID", "errors": errors}
    if profile.get("id") != bootstrap["profile"]:
        return {
            "outcome": "INVALID",
            "errors": [f"profile mismatch: bootstrap={bootstrap['profile']} profile={profile.get('id')}"],
        }
    profile_stages = profile.get("stages", [])
    if not profile_stages:
        return {"outcome": "INVALID", "errors": ["workflow profile has no stages"]}

    stages = []
    gate_ids = []
    for index, stage in enumerate(profile_stages):
        item = {
            "id": stage["id"],
            "status": "READY" if index == 0 else "TODO",
        }
        if stage.get("gate"):
            item["gate"] = stage["gate"]
            if stage["gate"] not in gate_ids:
                gate_ids.append(stage["gate"])
        stages.append(item)

    manifest = {
        "protocol_version": "0.1.0",
        "revision": 0,
        "feature": dict(bootstrap["feature"]),
        "workflow": {
            "profile": profile["id"],
            "status": "ACTIVE",
            "current_stage": stages[0]["id"],
            "stages": stages,
        },
        "tasks": [],
        "artifacts": [],
        "gates": [{"id": gate_id, "status": "PENDING"} for gate_id in gate_ids],
        "evidence": [],
        "applied_events": [],
        "updated_at": bootstrap["created_at"],
    }
    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        return {"outcome": "INVALID", "errors": manifest_errors}
    return {"outcome": "BOOTSTRAPPED", "errors": [], "manifest": manifest}


def main():
    parser = argparse.ArgumentParser(description="Bootstrap an AI-SDLC Feature Manifest from a workflow profile")
    parser.add_argument("bootstrap", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--format", choices=["yaml", "json"], default="yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    bootstrap = load_yaml(args.bootstrap)
    profile = load_yaml(args.profile) if args.profile else load_profile(bootstrap["profile"])
    result = build_manifest(bootstrap, profile)
    if result["outcome"] == "INVALID":
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(2)

    if args.format == "json":
        text = json.dumps(result["manifest"], indent=2, sort_keys=True) + "\n"
    else:
        text = yaml.safe_dump(result["manifest"], sort_keys=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
