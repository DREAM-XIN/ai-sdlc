#!/usr/bin/env python3
"""Deterministic validation for the supported read-only MCP stdio adapter."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_api import REGISTRY
from operator_conformance import (
    DirectFixtureAdapter,
    FROZEN_CONFORMANCE_SUBSET,
    assert_materially_independent,
    run_conformance_suite,
)
from operator_mcp import ADAPTER_ID, CONFORMANCE_PROBE, READ_TOOLS, TRANSPORT_KIND
from operator_mcp_conformance import (
    McpStdioConformanceAdapter,
    call_project_inspect_with_backend,
    list_tools,
)

EXPECTED_PRODUCTION_TOOLS = tuple(READ_TOOLS)
WRITE_CAPABILITIES = {
    "operation.start",
    "operation.resume",
    "operation.cancel",
    "decision.respond",
    "notification.ack",
}


def main() -> None:
    production_tools = list_tools(production=True)
    assert set(production_tools) == set(EXPECTED_PRODUCTION_TOOLS), production_tools
    assert len(production_tools) == 7, production_tools
    assert CONFORMANCE_PROBE not in production_tools

    conformance_tools = list_tools(production=False)
    assert set(conformance_tools) == set(EXPECTED_PRODUCTION_TOOLS) | {CONFORMANCE_PROBE}, conformance_tools
    assert len(conformance_tools) == 8, conformance_tools

    assert set(READ_TOOLS.values()).isdisjoint(WRITE_CAPABILITIES)
    assert set(READ_TOOLS.values()) == {
        "system.capabilities",
        "project.inspect",
        "feature.status",
        "operator.inbox",
        "operation.status",
        "decision.list",
        "notification.list",
    }

    adapter = McpStdioConformanceAdapter()
    report = run_conformance_suite(adapter)
    assert report.adapter.adapter_id == ADAPTER_ID
    assert report.adapter.transport_kind == TRANSPORT_KIND
    assert report.adapter.wrapper_depth == 0
    assert report.exercised_capabilities == FROZEN_CONFORMANCE_SUBSET

    discovery = adapter.invoke(
        {
            "api_version": "ai-sdlc.operator/v1",
            "request_id": "mcp-discovery",
            "capability": "system.capabilities",
            "target": {"repository": "DREAM-XIN/fixture", "feature_id": "F-CONFORMANCE-0001"},
            "client_identity": {"adapter_id": ADAPTER_ID},
            "payload": {},
        }
    )
    assert discovery["ok"] is True, discovery
    rows = {row["id"]: row for row in discovery["result"]["capabilities"]}
    assert set(rows) == set(REGISTRY)
    assert len(rows) == 12
    for capability in WRITE_CAPABILITIES:
        assert rows[capability]["available"] is False, (capability, rows[capability])

    project = call_project_inspect_with_backend()
    assert project["ok"] is True, project
    assert project["result"] == {"repository": "DREAM-XIN/fixture", "installed": True}

    direct = DirectFixtureAdapter()
    direct_evidence, mcp_evidence = assert_materially_independent(direct, adapter)
    assert direct_evidence.transport_kind != mcp_evidence.transport_kind
    assert mcp_evidence.root_implementation_type.endswith("McpStdioConformanceAdapter")

    print("Operator MCP validation passed")
    print(f"- adapter_id: {ADAPTER_ID}")
    print(f"- transport_kind: {TRANSPORT_KIND}")
    print(f"- production_tools: {len(production_tools)} read-only")
    print(f"- canonical_registry: {len(rows)} capabilities")
    print(f"- conformance_subset: {len(report.exercised_capabilities)} over real MCP stdio")
    print("- conformance probe: test-only, absent from production tool list")
    print("- semantic writes: no MCP tool registration")


if __name__ == "__main__":
    main()
