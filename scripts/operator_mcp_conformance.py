#!/usr/bin/env python3
"""CanonicalAdapter driver that crosses the supported MCP stdio transport."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from operator_mcp import ADAPTER_ID, CONFORMANCE_PROBE, READ_TOOLS, TRANSPORT_KIND

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tests" / "fixtures" / "operator_mcp_conformance_server.py"
CAPABILITY_TO_TOOL = {capability: tool for tool, capability in READ_TOOLS.items()}


async def _with_session(*, project_inspect: bool = False):
    args = [str(LAUNCHER)]
    if project_inspect:
        args.append("--project-inspect")
    params = StdioServerParameters(command=sys.executable, args=args)
    return stdio_client(params)


async def _call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    project_inspect: bool = False,
) -> dict[str, Any]:
    args = [str(LAUNCHER)]
    if project_inspect:
        args.append("--project-inspect")
    params = StdioServerParameters(command=sys.executable, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            structured = result.structured_content
            if not isinstance(structured, dict):
                raise AssertionError(f"MCP tool {tool_name} did not return structured content: {result!r}")
            return structured


async def _list_tools(*, production: bool = False) -> tuple[str, ...]:
    if production:
        server_path = ROOT / "scripts" / "operator_mcp.py"
        params = StdioServerParameters(command=sys.executable, args=[str(server_path)])
    else:
        params = StdioServerParameters(command=sys.executable, args=[str(LAUNCHER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()
            return tuple(tool.name for tool in response.tools)


def list_tools(*, production: bool = False) -> tuple[str, ...]:
    return asyncio.run(_list_tools(production=production))


def call_project_inspect_with_backend() -> dict[str, Any]:
    return asyncio.run(
        _call_tool(
            CAPABILITY_TO_TOOL["project.inspect"],
            {
                "target": {"repository": "DREAM-XIN/fixture", "feature_id": "F-CONFORMANCE-0001"},
                "payload": {},
            },
            project_inspect=True,
        )
    )


class McpStdioConformanceAdapter:
    """Materially independent adapter backed only by a real MCP stdio session."""

    adapter_id = ADAPTER_ID
    transport_kind = TRANSPORT_KIND

    def invoke(self, canonical_request: dict[str, Any]) -> dict[str, Any]:
        capability = canonical_request["capability"]
        if "trusted_identity" in canonical_request:
            return asyncio.run(
                _call_tool(CONFORMANCE_PROBE, {"case": "trusted_identity_injection"})
            )
        if capability not in CAPABILITY_TO_TOOL:
            return asyncio.run(
                _call_tool(CONFORMANCE_PROBE, {"case": "unknown_capability"})
            )
        arguments = {
            "api_version": canonical_request.get("api_version"),
            "target": canonical_request.get("target"),
            "payload": canonical_request.get("payload") or {},
        }
        return asyncio.run(_call_tool(CAPABILITY_TO_TOOL[capability], arguments))
