#!/usr/bin/env python3
"""Supported read-only MCP stdio adapter for ai-sdlc.operator/v1."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Literal, Protocol
from uuid import uuid4

from mcp.server import MCPServer

from operator_api import API_VERSION, dispatch

ADAPTER_ID = "ai-sdlc.mcp.stdio"
TRANSPORT_KIND = "mcp-stdio"
CONFORMANCE_PROBE = "__ai_sdlc_conformance_probe"

READ_TOOLS: dict[str, str] = {
    "ai_sdlc_system_capabilities": "system.capabilities",
    "ai_sdlc_project_inspect": "project.inspect",
    "ai_sdlc_feature_status": "feature.status",
    "ai_sdlc_operator_inbox": "operator.inbox",
    "ai_sdlc_operation_status": "operation.status",
    "ai_sdlc_decision_list": "decision.list",
    "ai_sdlc_notification_list": "notification.list",
}


class TrustedContextProvider(Protocol):
    def for_request(self, target: dict[str, Any] | None) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class StaticTrustedContextProvider:
    trusted_context: dict[str, Any]

    def for_request(self, target: dict[str, Any] | None) -> dict[str, Any]:
        return dict(self.trusted_context)


DEFAULT_TRUSTED_CONTEXT = {
    "trusted_identity": {
        "service_id": "ai-sdlc-mcp",
        "runtime_id": "mcp-stdio",
        "authorization_context": "operator-read-only",
    }
}


def _request_id() -> str:
    return f"mcp-{uuid4().hex}"


def invoke_canonical(
    *,
    capability: str,
    api_version: str,
    target: dict[str, Any] | None,
    context: dict[str, Any] | None,
    payload: dict[str, Any],
    trusted_context: dict[str, Any],
    backends: dict[str, Any],
    extra_envelope_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "api_version": api_version,
        "request_id": _request_id(),
        "capability": capability,
        "client_identity": {"adapter_id": ADAPTER_ID},
        "payload": dict(payload),
    }
    if target is not None:
        request["target"] = dict(target)
    if context is not None:
        # Bounded canonical context only. Trusted identity/authorization stays
        # server-owned and cannot be supplied through MCP tool arguments.
        request["context"] = dict(context)
    if extra_envelope_fields:
        request.update(extra_envelope_fields)
    return dispatch(request, trusted_context=trusted_context, backends=backends)


def _register_read_tool(
    server: MCPServer,
    *,
    tool_name: str,
    capability: str,
    trusted_context_provider: TrustedContextProvider,
    backends: dict[str, Any],
) -> None:
    async def read_tool(
        api_version: str = API_VERSION,
        target: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trusted_context = trusted_context_provider.for_request(target)
        return invoke_canonical(
            capability=capability,
            api_version=api_version,
            target=target,
            context=context,
            payload=dict(payload or {}),
            trusted_context=trusted_context,
            backends=backends,
        )

    server.add_tool(
        read_tool,
        name=tool_name,
        description=f"Read-only AI-SDLC Operator capability: {capability}",
        structured_output=True,
    )


def _register_conformance_probe(
    server: MCPServer,
    *,
    trusted_context_provider: TrustedContextProvider,
    backends: dict[str, Any],
) -> None:
    async def conformance_probe(
        case: Literal["unknown_capability", "trusted_identity_injection"],
    ) -> dict[str, Any]:
        target = {"repository": "DREAM-XIN/fixture", "feature_id": "F-CONFORMANCE-0001"}
        trusted_context = trusted_context_provider.for_request(target)
        if case == "unknown_capability":
            return invoke_canonical(
                capability="not.real",
                api_version=API_VERSION,
                target=target,
                context=None,
                payload={},
                trusted_context=trusted_context,
                backends=backends,
            )
        return invoke_canonical(
            capability="feature.status",
            api_version=API_VERSION,
            target=target,
            context=None,
            payload={},
            trusted_context=trusted_context,
            backends=backends,
            extra_envelope_fields={"trusted_identity": {"service_id": "client-forged"}},
        )

    server.add_tool(
        conformance_probe,
        name=CONFORMANCE_PROBE,
        description="Reserved deterministic conformance probe",
        structured_output=True,
    )


def build_server(
    *,
    trusted_context_provider: TrustedContextProvider | None = None,
    backends: dict[str, Any] | None = None,
    enable_conformance_probe: bool = False,
) -> MCPServer:
    provider = trusted_context_provider or StaticTrustedContextProvider(DEFAULT_TRUSTED_CONTEXT)
    backend_map = dict(backends or {})
    server = MCPServer("AI-SDLC Operator")
    for tool_name, capability in READ_TOOLS.items():
        _register_read_tool(
            server,
            tool_name=tool_name,
            capability=capability,
            trusted_context_provider=provider,
            backends=backend_map,
        )
    if enable_conformance_probe:
        _register_conformance_probe(
            server,
            trusted_context_provider=provider,
            backends=backend_map,
        )
    return server


def build_production_server_from_environment() -> MCPServer:
    """Enable trusted backing only when the server launcher supplies it.

    Absence of config preserves the accepted fail-closed MCP behavior: read
    tools remain discoverable, while unbacked canonical capabilities report
    `CAPABILITY_UNAVAILABLE`. When configured, target-repository reads and Store
    protection inspection use separately named credentials.
    """
    config_path = os.environ.get("AI_SDLC_OPERATOR_RUNTIME_CONFIG", "").strip()
    if not config_path:
        return build_server(enable_conformance_probe=False)

    target_read_token = os.environ.get("AI_SDLC_OPERATOR_TARGET_READ_TOKEN", "").strip()
    store_token = os.environ.get("AI_SDLC_OPERATOR_STORE_TOKEN", "").strip()
    if not target_read_token or not store_token:
        raise RuntimeError(
            "trusted Operator runtime config requires separate target-read and Store credentials"
        )

    from operator_production_runtime import (
        TrustedOperatorRuntimeConfig,
        build_trusted_operator_read_bundle,
    )

    config = TrustedOperatorRuntimeConfig.from_file(config_path)
    bundle = build_trusted_operator_read_bundle(
        config=config,
        adapter_id=ADAPTER_ID,
        target_read_token=target_read_token,
        store_token=store_token,
        github_api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    return build_server(
        trusted_context_provider=bundle.trusted_context_provider,
        backends=bundle.backends,
        enable_conformance_probe=False,
    )


def main() -> None:
    # Production startup has no code path that enables the test-only probe and
    # no MCP argument can widen configured repository/Feature scope.
    build_production_server_from_environment().run(transport="stdio")


if __name__ == "__main__":
    main()
