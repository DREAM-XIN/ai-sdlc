#!/usr/bin/env python3
"""Build profile-level readiness from trusted presence-only credential signals."""
from __future__ import annotations

import json
import os
from typing import Mapping

from gh_aw_provider_registry import ProviderRegistry, RegistryValidationError, load_registry


def presence_env_name(credential: str) -> str:
    return f"HAS_{credential}"


def _bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise RegistryValidationError(f"trusted readiness signal {label!r} must be boolean")


def readiness_from_presence(
    registry: ProviderRegistry,
    presence: Mapping[str, object],
) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for profile in registry.profiles:
        identities = (profile.credential, *profile.credential_aliases)
        flags: list[bool] = []
        for identity in identities:
            if identity not in presence:
                raise RegistryValidationError(
                    f"missing trusted credential-presence signal for {identity!r}"
                )
            flags.append(_bool(presence[identity], identity))
        result[profile.profile_id] = any(flags)
    return result


def readiness_from_environment(
    registry: ProviderRegistry,
    environ: Mapping[str, str] | None = None,
) -> dict[str, bool]:
    source = os.environ if environ is None else environ
    presence = {
        identity: source[presence_env_name(identity)]
        for profile in registry.profiles
        for identity in (profile.credential, *profile.credential_aliases)
        if presence_env_name(identity) in source
    }
    return readiness_from_presence(registry, presence)


def main() -> int:
    try:
        registry = load_registry()
        result = readiness_from_environment(registry)
    except RegistryValidationError as exc:
        print(json.dumps({"status": "INVALID_READINESS", "error": str(exc)}, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
