"""MCP server (streamable HTTP) + n8n REST API on one port."""
from __future__ import annotations

import logging

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import Settings
from app.mcp_server import http_api
from app.mcp_server.auth import BearerAuthMiddleware
from app.mcp_server.tools import diary_tools, profile_tools, reminder_tools, summary_tools, weather_tools
from app.services.container import Services


def create_mcp(svc: Services) -> FastMCP:
    s = svc.settings
    mcp = FastMCP(
        "DayMate", host=s.mcp_host, port=s.mcp_port, stateless_http=True,
        instructions="DayMate diary and reminder tools. The calling user is identified by the transport, not by arguments.",
        # Reachable from other hosts (e.g. n8n in Docker) only if the operator opts in via MCP_HOST.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    for module in (diary_tools, reminder_tools, summary_tools, weather_tools, profile_tools):
        module.register(mcp, svc)
    http_api.register(mcp, svc)
    return mcp


def create_app(svc: Services):
    s = svc.settings
    if not s.mcp_auth_token:
        raise SystemExit("MCP_AUTH_TOKEN must be set (see .env.example).")
    app = create_mcp(svc).streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=s.mcp_auth_token)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    uvicorn.run(create_app(Services.build(settings)), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
