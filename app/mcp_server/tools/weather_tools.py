from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP

from app.errors import ValidationError
from app.mcp_server.tools.common import local_today_for, run_tool
from app.services.container import Services


def register(mcp: FastMCP, svc: Services) -> None:
    @mcp.tool()
    def get_weather(ctx: Context, location: str | None = None, date: str | None = None) -> dict:
        """Look up real weather from Open-Meteo. Supports today (current + daily), a forecast up to 15 days ahead,
        and past dates. location is a place name; if omitted the configured default is used, otherwise ask the user.
        date is ISO YYYY-MM-DD; omit for today. Report only what the result contains; if success is false say so."""
        def body(uid):
            place = (location or svc.settings.default_location).strip()
            if not place:
                raise ValidationError("Which location should I check the weather for?", code="location_required")
            return {"weather": svc.weather.get_weather(place, date, today=local_today_for(svc, uid))}
        return run_tool(ctx, body)
