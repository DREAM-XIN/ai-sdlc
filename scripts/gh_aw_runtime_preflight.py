#!/usr/bin/env python3
"""Non-invasive preflight for a trusted gh-aw runtime profile.

This command never calls an AI provider and never mutates Feature state. It proves
only the static/runtime prerequisites that can be checked safely before dispatch:
trusted profile resolution, installed compiler-attested lock, and credential
presence as reported by the caller.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
METADATA_PREFIX = "# gh-aw-metadata: "
PINNED_COMPILER = "v0.83.4"


def bool_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def load_metadata(path: Path) -> dict | None:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith(METADATA_PREFIX):
        return None
    try:
        return json.loads(first[len(METADATA_PREFIX) :])
    except json.JSONDecodeError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--credential-present", required=True, type=bool_value)
    parser.add_argument("--workflow-dir", default=str(ROOT / ".github/workflows"))
    args = parser.parse_args()

    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    if args.profile not in profiles:
        print(json.dumps({"status": "UNKNOWN_PROFILE", "profile": args.profile}, separators=(",", ":")))
        return 2

    cfg = profiles[args.profile]
    lock = Path(args.workflow_dir) / cfg["worker_workflow"]
    result = {
        "profile": args.profile,
        "engine": cfg["engine"],
        "credential": cfg["credential"],
        "worker_workflow": cfg["worker_workflow"],
        "credential_present": args.credential_present,
        "entitlement_verified": False,
    }

    if not lock.is_file():
        result["status"] = "MISSING_LOCK"
        print(json.dumps(result, separators=(",", ":")))
        return 2

    metadata = load_metadata(lock)
    if not metadata or metadata.get("strict") is not True or metadata.get("compiler_version") != PINNED_COMPILER or metadata.get("schema_version") != "v4":
        result["status"] = "INVALID_LOCK"
        print(json.dumps(result, separators=(",", ":")))
        return 2

    result["compiler_version"] = metadata["compiler_version"]
    result["lock_strict"] = True
    result["agent_id"] = metadata.get("agent_id")

    if not args.credential_present:
        result["status"] = "MISSING_CREDENTIAL"
        print(json.dumps(result, separators=(",", ":")))
        return 0

    result["status"] = "READY_FOR_ENTITLEMENT_PROBE"
    result["note"] = "Credential presence is confirmed; provider entitlement/API quota is intentionally not tested by preflight."
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
