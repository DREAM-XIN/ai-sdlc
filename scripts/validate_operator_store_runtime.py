#!/usr/bin/env python3
"""Validate the trusted production Operator Store composition boundary."""
from pathlib import Path
import tempfile

from operator_store_protection import PROTECTED, UNKNOWN, StaticProtectionVerifier
from operator_store_runtime import (
    DEFAULT_OPERATOR_STATE_REF,
    TrustedOperatorStoreConfig,
    build_trusted_operator_api_backends,
    build_trusted_operator_store_runtime,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-operator-runtime-") as td:
        config = TrustedOperatorStoreConfig(
            repository="DREAM-XIN/ai-sdlc",
            trusted_checkout=Path(td),
        )
        require(config.state_ref == DEFAULT_OPERATOR_STATE_REF, "trusted default state ref changed")
        runtime = build_trusted_operator_store_runtime(
            config,
            protection_verifier=StaticProtectionVerifier(status=UNKNOWN),
        )
        require(runtime.backend.state_ref == DEFAULT_OPERATOR_STATE_REF, "runtime did not preserve trusted state ref")
        require(runtime.backend.repository == "DREAM-XIN/ai-sdlc", "runtime repository binding changed")
        backends = build_trusted_operator_api_backends(
            config,
            protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        )
        require(set(backends) == {"operation.start", "operation.status", "operation.cancel"}, "runtime exposed out-of-scope capability")
        try:
            TrustedOperatorStoreConfig(
                repository="DREAM-XIN/ai-sdlc",
                trusted_checkout=Path(td),
                state_ref="feature/user-controlled",
            )
            raise AssertionError("non-ref trusted state-ref override unexpectedly accepted")
        except ValueError:
            pass
    print("Operator Store trusted runtime composition validation passed")


if __name__ == "__main__":
    main()
