#!/usr/bin/env python3
"""Non-invasive static preflight for a trusted gh-aw runtime profile."""
from __future__ import annotations

import argparse
import json

from gh_aw_compiled_worker import (
    InvalidCompiledWorkerError,
    MissingCompiledWorkerError,
    load_compiled_worker,
)
from gh_aw_provider_registry import RegistryValidationError, load_registry


def bool_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def emit(result: dict, code: int) -> int:
    print(json.dumps(result, separators=(",", ":")))
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--credential-present", required=True, type=bool_value)
    parser.add_argument("--workflow-dir", default=None)
    args = parser.parse_args()

    try:
        registry = load_registry()
    except RegistryValidationError as exc:
        return emit(
            {
                "status": "INVALID_REGISTRY",
                "profile": args.profile,
                "error": str(exc),
                "entitlement_verified": False,
            },
            2,
        )
    if args.profile not in registry.profile_ids():
        return emit(
            {
                "status": "UNKNOWN_PROFILE",
                "profile": args.profile,
                "entitlement_verified": False,
            },
            2,
        )
    profile = registry.require_profile(args.profile)
    result = {
        "profile": profile.profile_id,
        "engine": profile.engine,
        "provider": profile.provider,
        "protocol": profile.protocol,
        "model": profile.model,
        "maturity": profile.maturity,
        "credential": profile.credential,
        "worker_workflow": profile.worker_workflow,
        "credential_present": args.credential_present,
        "entitlement_verified": False,
    }

    try:
        compiled = (
            load_compiled_worker(profile)
            if args.workflow_dir is None
            else load_compiled_worker(profile, args.workflow_dir)
        )
    except MissingCompiledWorkerError:
        result["status"] = "MISSING_LOCK"
        return emit(result, 2)
    except InvalidCompiledWorkerError:
        result["status"] = "INVALID_LOCK"
        return emit(result, 2)

    result["compiler_version"] = compiled.metadata["compiler_version"]
    result["lock_strict"] = True
    result["agent_id"] = compiled.metadata.get("agent_id")
    result["agent_model"] = compiled.metadata.get("agent_model")

    if not args.credential_present:
        result["status"] = "MISSING_CREDENTIAL"
        return emit(result, 0)

    result["status"] = "READY_FOR_ENTITLEMENT_PROBE"
    result["note"] = (
        "Credential presence is confirmed; provider entitlement, quota, billing, model "
        "availability, and current rate-limit headroom are intentionally not tested by "
        "static preflight."
    )
    return emit(result, 0)


if __name__ == "__main__":
    raise SystemExit(main())
