from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from app.mcp_server.tools.common import run_tool
from app.services.container import Services


def register(mcp: FastMCP, svc: Services) -> None:
    @mcp.tool()
    def set_timezone(ctx: Context, timezone: str) -> dict:
        """Save the user's IANA timezone (e.g. Asia/Karachi, Europe/London) after the user has told you
        where they are. Never guess a timezone the user did not state."""
        return run_tool(ctx, lambda uid: {"timezone": svc.users.set_timezone(uid, timezone)["timezone"]})
