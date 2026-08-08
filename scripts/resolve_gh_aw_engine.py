#!/usr/bin/env python3
"""Resolve a trusted gh-aw engine/provider profile to its compiled worker workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    if args.profile not in profiles:
        allowed = ", ".join(sorted(profiles))
        raise SystemExit(f"unknown gh-aw engine profile {args.profile!r}; allowed: {allowed}")
    cfg = profiles[args.profile]
    result = {
        "profile": args.profile,
        "engine": cfg["engine"],
        "provider": cfg.get("provider", "native"),
        "protocol": cfg.get("protocol", "native"),
        "model": cfg.get("model"),
        "worker_workflow": cfg["worker_workflow"],
        "credential": cfg["credential"],
        "maturity": cfg["maturity"],
    }
    if args.json:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print(result["worker_workflow"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
