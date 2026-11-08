from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from app.mcp_server.tools.common import run_tool
from app.services.container import Services


def register(mcp: FastMCP, svc: Services) -> None:
    @mcp.tool()
    def create_reminder(ctx: Context, message: str, scheduled_at: str) -> dict:
        """Create a reminder. scheduled_at is the ISO local datetime in the USER's timezone,
        e.g. 2026-11-02T10:00:00 (no offset). Only call when the user gave a specific date and time."""
        return run_tool(ctx, lambda uid: {"reminder": svc.reminders.create(uid, message, scheduled_at)})

    @mcp.tool()
    def list_reminders(ctx: Context, status: str | None = "pending", from_datetime: str | None = None,
                       to_datetime: str | None = None) -> dict:
        """List reminders. status: pending (default), sent, cancelled, failed, or null for all.
        Optional from_datetime/to_datetime are ISO local datetimes bounding the scheduled time."""
        def body(uid):
            rows = svc.reminders.list(uid, status, from_datetime, to_datetime)
            return {"count": len(rows), "reminders": rows}
        return run_tool(ctx, body)

    @mcp.tool()
    def delete_reminder(ctx: Context, reminder_id: int) -> dict:
        """Cancel a pending reminder by id (the record is kept with status=cancelled).
        If the user describes it rather than giving an id, call list_reminders first and ask which one if unclear."""
        return run_tool(ctx, lambda uid: {"reminder": svc.reminders.cancel(uid, reminder_id)})
