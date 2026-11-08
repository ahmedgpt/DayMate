from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from app.mcp_server.tools.common import run_tool
from app.services.container import Services


def register(mcp: FastMCP, svc: Services) -> None:
    @mcp.tool()
    def get_daily_summary(ctx: Context, date: str | None = None) -> dict:
        """Get the recorded facts for one day (entries, counts, recorded-duration total) so you can write a short
        factual summary. date is ISO YYYY-MM-DD; omit for today. Never add facts that are not in the result."""
        return run_tool(ctx, lambda uid: {"summary": svc.summaries.daily(uid, date)})

    @mcp.tool()
    def get_weekly_summary(ctx: Context, start_date: str | None = None, end_date: str | None = None) -> dict:
        """Get recorded facts for a date range (default: the last 7 days including today) with counts computed
        from stored data. Provide both dates or neither."""
        return run_tool(ctx, lambda uid: {"summary": svc.summaries.weekly(uid, start_date, end_date)})
