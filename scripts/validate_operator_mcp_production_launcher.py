#!/usr/bin/env python3
"""Validate the backed MCP production launcher remains strict and scope-safe."""
from __future__ import annotations

import ast
import os
from pathlib import Path

import operator_mcp_production
from operator_mcp import READ_TOOLS

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "operator_mcp_production.py"
WRITE_CAPABILITIES = {
    "operation.start",
    "operation.resume",
    "operation.cancel",
    "decision.respond",
    "notification.ack",
}
REQUIRED_ENV = (
    "AI_SDLC_OPERATOR_RUNTIME_CONFIG",
    "AI_SDLC_OPERATOR_TARGET_READ_TOKEN",
    "AI_SDLC_OPERATOR_STORE_TOKEN",
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_missing_config_fails_closed():
    saved = {name: os.environ.get(name) for name in REQUIRED_ENV}
    try:
        for name in REQUIRED_ENV:
            os.environ.pop(name, None)
        try:
            operator_mcp_production.build_backed_production_server()
            raise AssertionError("backed MCP production launcher unexpectedly started without trusted config")
        except RuntimeError as exc:
            require("AI_SDLC_OPERATOR_RUNTIME_CONFIG" in str(exc), exc)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def validate_static_authority_boundary():
    source = LAUNCHER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
    require("build_trusted_operator_backend_bundle" in imported, "production launcher does not use authoritative scoped bundle")
    require("build_trusted_operator_read_bundle" not in imported, "production launcher imported legacy unscoped helper")
    require("extend_with_trusted_decision_writes" not in imported, "read-only MCP production launcher imported write extension")
    require(set(READ_TOOLS.values()).isdisjoint(WRITE_CAPABILITIES), "MCP production tool registry exposes semantic writes")
    require("enable_conformance_probe=False" in source, "production launcher did not explicitly disable test probe")


def main():
    validate_missing_config_fails_closed()
    validate_static_authority_boundary()
    print("Operator backed MCP production launcher validation passed")
    print("- no unbacked fallback")
    print("- authoritative target-scoped bundle required")
    print("- separate trusted runtime/target-read/Store inputs required")
    print("- no write extension imported and no MCP write tools registered")
    print("- test-only conformance probe disabled")


if __name__ == "__main__":
    main()
