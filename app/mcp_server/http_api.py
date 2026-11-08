"""REST endpoints for n8n. Same service layer and database as the MCP tools; protected by the bearer token."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.errors import DayMateError
from app.services.container import Services


def register(mcp: FastMCP, svc: Services) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request):
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/api/reminders/claim-due", methods=["POST"])
    async def claim_due(request: Request):
        """Lease due reminders. Each is returned to exactly one caller until the lease expires."""
        limit = min(int(request.query_params.get("limit", 20)), 100)
        return JSONResponse({"reminders": svc.reminders.claim_due(limit)})

    @mcp.custom_route("/api/reminders/{reminder_id:int}/deliver", methods=["POST"])
    async def deliver(request: Request):
        """Send a leased reminder via Telegram FROM THIS SERVER and record the outcome (sent / retry / failed).
        Non-2xx means "not delivered" so an n8n error branch can react; state is already updated server-side."""
        result = await svc.delivery.deliver(request.path_params["reminder_id"])
        return JSONResponse(result.as_dict(), status_code=result.http_status)

    @mcp.custom_route("/api/telegram/send", methods=["POST"])
    async def telegram_send(request: Request):
        """Send a text to a REGISTERED user's chat (summary workflows). Arbitrary chat ids are rejected."""
        try:
            body = await request.json()
            chat_id, text = str(body["chat_id"]), str(body["text"]).strip()
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "JSON body with chat_id and text is required"}, status_code=400)
        if not text:
            return JSONResponse({"error": "text must not be empty"}, status_code=400)
        result = await svc.delivery.send_to_known_chat(chat_id, text)
        return JSONResponse(result.as_dict(), status_code=result.http_status)

    @mcp.custom_route("/api/digests/weekly", methods=["GET"])
    async def weekly_digests(request: Request):
        """Users at local ?weekday=N (0=Mon..6=Sun, default 6) ?hour=H (default 20) with entries in the last 7 days."""
        try:
            weekday = int(request.query_params.get("weekday", 6))
            hour = int(request.query_params.get("hour", 20))
            return JSONResponse({"digests": svc.summaries.weekly_digests_due(weekday, hour)})
        except (DayMateError, ValueError) as e:
            return JSONResponse({"error": getattr(e, "message", "weekday and hour must be integers")}, status_code=400)

    @mcp.custom_route("/api/digests/daily", methods=["GET"])
    async def daily_digests(request: Request):
        """Users whose local clock is at ?hour=N (default 23) and who have entries for their local day."""
        try:
            hour = int(request.query_params.get("hour", 23))
            return JSONResponse({"digests": svc.summaries.digests_due(hour)})
        except (DayMateError, ValueError) as e:
            return JSONResponse({"error": getattr(e, "message", "hour must be an integer 0-23")}, status_code=400)
