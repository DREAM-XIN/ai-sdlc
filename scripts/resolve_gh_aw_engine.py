#!/usr/bin/env python3
"""Resolve a trusted gh-aw engine/provider profile to its compiled worker workflow."""
from __future__ import annotations

import argparse
import json

from gh_aw_provider_registry import RegistryValidationError, load_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        profile = load_registry().require_profile(args.profile)
    except RegistryValidationError as exc:
        raise SystemExit(str(exc)) from None

    result = {
        "profile": profile.profile_id,
        "engine": profile.engine,
        "provider": profile.provider,
        "protocol": profile.protocol,
        "model": profile.model,
        "worker_workflow": profile.worker_workflow,
        "credential": profile.credential,
        "maturity": profile.maturity,
    }
    if args.json:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print(result["worker_workflow"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
