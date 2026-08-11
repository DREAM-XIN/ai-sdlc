#!/usr/bin/env python3
"""Trusted v0.3 write-ready Operator runtime composition.

This is the canonical composition target for a future approved write-capable AI
client adapter. It forces the v0.3 vertical Operation profile, target-scoped
Store backends, and the existing trusted Decision/Notification authority. It is
transport-neutral and grants no capabilities by itself.
"""
from __future__ import annotations

from typing import Any, Callable

from operator_production_bundle import build_trusted_operator_backend_bundle
from operator_production_runtime import TrustedOperatorRuntimeConfig, _default_get
from operator_production_write_bundle import (
    REQUIRED_V03_WRITE_SLICE,
    TrustedOperatorWriteBundle,
    extend_with_trusted_decision_writes,
)
from operator_vertical import VERTICAL_PROFILE


def build_v03_write_ready_operator_bundle(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    store_token: str,
    policy_verifier: Any,
    feature_gateway: Any,
    trusted_context_digest: str,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    protection_verifier: Any = None,
) -> TrustedOperatorWriteBundle:
    read_bundle = build_trusted_operator_backend_bundle(
        config=config,
        adapter_id=adapter_id,
        target_read_token=target_read_token,
        store_token=store_token,
        github_api_base=github_api_base,
        reader_http_get=reader_http_get,
        protection_verifier=protection_verifier,
        operation_profile=VERTICAL_PROFILE,
    )
    write_bundle = extend_with_trusted_decision_writes(
        read_bundle,
        policy_verifier=policy_verifier,
        feature_gateway=feature_gateway,
        trusted_context_digest=trusted_context_digest,
    )
    missing = REQUIRED_V03_WRITE_SLICE - set(write_bundle.backends)
    if missing:
        raise RuntimeError(f"v0.3 write-ready runtime missing frozen write slice: {sorted(missing)}")
    return write_bundle
