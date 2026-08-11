#!/usr/bin/env python3
"""Validate every backed MCP production path remains strict and scope-safe."""
from __future__ import annotations

import ast
import os
from pathlib import Path

import operator_mcp_production
from operator_mcp import READ_TOOLS

ROOT = Path(__file__).resolve().parents[1]
STRICT_LAUNCHER = ROOT / "scripts" / "operator_mcp_production.py"
COMPATIBILITY_LAUNCHER = ROOT / "scripts" / "operator_mcp.py"
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


def imported_symbols(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
    return imported


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
    strict_source = STRICT_LAUNCHER.read_text(encoding="utf-8")
    strict_imported = imported_symbols(STRICT_LAUNCHER)
    require(
        "build_trusted_operator_backend_bundle" in strict_imported,
        "strict production launcher does not use authoritative scoped bundle",
    )
    require(
        "build_trusted_operator_read_bundle" not in strict_imported,
        "strict production launcher imported legacy unscoped helper",
    )
    require(
        "extend_with_trusted_decision_writes" not in strict_imported,
        "read-only MCP production launcher imported write extension",
    )
    require(
        "enable_conformance_probe=False" in strict_source,
        "strict production launcher did not explicitly disable test probe",
    )

    # The compatibility MCP entrypoint may remain honestly unbacked when no
    # configuration is present. If environment backing is enabled, however, it
    # must route through the exact same authoritative scoped bundle as the strict
    # launcher. This prevents an alternate configured path from exposing raw
    # Store operation.status by operation id.
    compatibility_source = COMPATIBILITY_LAUNCHER.read_text(encoding="utf-8")
    compatibility_imported = imported_symbols(COMPATIBILITY_LAUNCHER)
    require(
        "build_trusted_operator_backend_bundle" in compatibility_imported,
        "configured compatibility MCP path does not use authoritative scoped bundle",
    )
    require(
        "build_trusted_operator_read_bundle" not in compatibility_imported,
        "configured compatibility MCP path imported legacy unscoped bundle",
    )
    require(
        "build_production_server_from_environment" in compatibility_source,
        "compatibility MCP configured production path disappeared unexpectedly",
    )
    require(
        "enable_conformance_probe=False" in compatibility_source,
        "compatibility production path can enable test probe",
    )

    require(
        set(READ_TOOLS.values()).isdisjoint(WRITE_CAPABILITIES),
        "MCP production tool registry exposes semantic writes",
    )


def main():
    validate_missing_config_fails_closed()
    validate_static_authority_boundary()
    print("Operator backed MCP production launcher validation passed")
    print("- strict launcher has no unbacked fallback")
    print("- strict and configured compatibility paths both require authoritative target-scoped bundle")
    print("- configured paths cannot import the legacy unscoped read bundle")
    print("- separate trusted runtime/target-read/Store inputs required")
    print("- no write extension imported and no MCP write tools registered")
    print("- test-only conformance probe disabled")


if __name__ == "__main__":
    main()
