#!/usr/bin/env python3
"""Strict production launcher for the read-only MCP adapter.

Unlike the compatibility launcher in `operator_mcp.py`, this entrypoint has no
unbacked fallback. A backed production service must provide explicit trusted
runtime configuration and separate target-read / Store credentials, then uses
the authoritative target-scoped production bundle.
"""
from __future__ import annotations

import os

from operator_mcp import ADAPTER_ID, build_server
from operator_production_bundle import build_trusted_operator_backend_bundle
from operator_production_runtime import TrustedOperatorRuntimeConfig


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for backed Operator MCP production startup")
    return value


def build_backed_production_server():
    config = TrustedOperatorRuntimeConfig.from_file(
        _required_env("AI_SDLC_OPERATOR_RUNTIME_CONFIG")
    )
    bundle = build_trusted_operator_backend_bundle(
        config=config,
        adapter_id=ADAPTER_ID,
        target_read_token=_required_env("AI_SDLC_OPERATOR_TARGET_READ_TOKEN"),
        store_token=_required_env("AI_SDLC_OPERATOR_STORE_TOKEN"),
        github_api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    return build_server(
        trusted_context_provider=bundle.trusted_context_provider,
        backends=bundle.backends,
        enable_conformance_probe=False,
    )


def main() -> None:
    build_backed_production_server().run(transport="stdio")


if __name__ == "__main__":
    main()
