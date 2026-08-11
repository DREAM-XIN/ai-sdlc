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
    """Server-owned source for trusted runtime identity and authorization context."""

    def for_request(self, target: dict[str, Any] | None) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class StaticTrustedContextProvider:
    """Explicit provider useful for trusted startup wiring and deterministic tests."""

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
    """Construct one canonical envelope and preserve canonical structured responses."""

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
        # `context` is the bounded canonical request context only
        # (operation id/generation/expected revision). It is not trusted
        # identity or authorization context and remains schema-validated.
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
        """Invoke one fixed read-only canonical Operator capability."""

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
        """Closed, test-only negative probe. Never registered by production main()."""

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
    """Build the supported server; the production entrypoint never enables the probe."""

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
    """Compose production backing only when trusted process config is explicit.

    The environment/config file is controlled by the MCP server launcher, not
    by MCP tool arguments. Absence preserves the accepted fail-closed behavior:
    tools remain registered but unconfigured canonical capabilities report
    `CAPABILITY_UNAVAILABLE`.
    """
    config_path = os.environ.get("AI_SDLC_OPERATOR_RUNTIME_CONFIG", "").strip()
    if not config_path:
        return build_server(enable_conformance_probe=False)

    token = (
        os.environ.get("AI_SDLC_OPERATOR_GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )
    if not token:
        raise RuntimeError("trusted Operator runtime config requires a GitHub token")

    from operator_production_runtime import (
        TrustedOperatorRuntimeConfig,
        build_trusted_operator_read_bundle,
    )

    config = TrustedOperatorRuntimeConfig.from_file(config_path)
    bundle = build_trusted_operator_read_bundle(
        config=config,
        adapter_id=ADAPTER_ID,
        github_token=token,
        github_api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    return build_server(
        trusted_context_provider=bundle.trusted_context_provider,
        backends=bundle.backends,
        enable_conformance_probe=False,
    )


def main() -> None:
    # Security invariant: production startup has no flag/env/config path that
    # enables the reserved conformance probe. Trusted runtime configuration may
    # add canonical backends, but it cannot add MCP write tools.
    server = build_production_server_from_environment()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
