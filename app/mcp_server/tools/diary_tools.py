from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from app.mcp_server.tools.common import run_tool
from app.services.container import Services


def register(mcp: FastMCP, svc: Services) -> None:
    @mcp.tool()
    def add_diary_entry(ctx: Context, description: str, entry_date: str | None = None,
                        category: str | None = None, duration_minutes: int | None = None) -> dict:
        """Save an activity to the user's diary.

        description: what the user did, in their own words (do not embellish).
        entry_date: ISO date YYYY-MM-DD in the user's local calendar; omit for today.
        category: optional short label (e.g. study, work, health) only if obvious.
        duration_minutes: only if the user stated a duration explicitly.
        """
        return run_tool(ctx, lambda uid: {"entry": svc.diary.add_entry(uid, description, entry_date, category, duration_minutes)})

    @mcp.tool()
    def get_diary_entries(ctx: Context, start_date: str | None = None, end_date: str | None = None,
                          search: str | None = None) -> dict:
        """List the user's diary entries between two ISO dates inclusive (equal dates = a single day).
        Omit both dates for today. `search` filters by a keyword in the description (use it to find an entry to update)."""
        def body(uid):
            entries = svc.diary.get_entries(uid, start_date, end_date, search)
            return {"count": len(entries), "entries": entries}
        return run_tool(ctx, body)

    @mcp.tool()
    def update_diary_entry(ctx: Context, entry_id: int, description: str | None = None,
                           category: str | None = None, duration_minutes: int | None = None) -> dict:
        """Change an existing diary entry by its id. If several entries could match the user's request,
        ask which one before calling this."""
        return run_tool(ctx, lambda uid: {"entry": svc.diary.update_entry(uid, entry_id, description, category, duration_minutes)})
