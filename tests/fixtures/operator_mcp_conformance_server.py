#!/usr/bin/env python3
"""Test-only launcher for the shipped MCP server implementation."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_mcp import ADAPTER_ID, StaticTrustedContextProvider, build_server  # noqa: E402

TRUSTED_CONTEXT = {
    "trusted_identity": {
        "service_id": "conformance-runtime",
        "runtime_id": "mcp-stdio-fixture",
        "authorization_context": "fixture-policy",
    }
}

RESULTS: dict[str, dict[str, Any]] = {
    "feature.status": {
        "feature_id": "F-CONFORMANCE-0001",
        "revision": 7,
        "workflow_status": "ACTIVE",
        "current_stage": "implementation",
    },
    "operator.inbox": {"operations": [], "decisions": [], "notifications": []},
    "operation.status": {"operation_id": "op-conformance-1", "generation": 2, "status": "BLOCKED"},
    "decision.list": {"decisions": []},
    "notification.list": {"notifications": []},
}


class SemanticBackend:
    def __init__(self, result: dict[str, Any]):
        self.result = copy.deepcopy(result)

    def availability(self, capability: str, trusted_context: dict[str, Any]):
        trusted = trusted_context.get("trusted_identity") or {}
        if trusted.get("service_id") != "conformance-runtime":
            raise AssertionError("trusted runtime identity was not propagated")
        return True, "AVAILABLE"

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        if request["client_identity"]["adapter_id"] != ADAPTER_ID:
            raise AssertionError("MCP adapter identity was not preserved")
        trusted = trusted_context.get("trusted_identity") or {}
        if trusted.get("authorization_context") != "fixture-policy":
            raise AssertionError("trusted authorization context was not propagated")
        return copy.deepcopy(self.result)


def backends(include_project_inspect: bool) -> dict[str, Any]:
    values: dict[str, Any] = {key: SemanticBackend(value) for key, value in RESULTS.items()}
    if include_project_inspect:
        values["project.inspect"] = SemanticBackend(
            {"repository": "DREAM-XIN/fixture", "installed": True}
        )
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-inspect", action="store_true")
    args = parser.parse_args()
    server = build_server(
        trusted_context_provider=StaticTrustedContextProvider(TRUSTED_CONTEXT),
        backends=backends(args.project_inspect),
        enable_conformance_probe=True,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
