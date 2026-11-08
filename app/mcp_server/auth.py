"""Trust boundary. The agent (our code, not the model) sets these headers per request."""
from __future__ import annotations

import hmac
import json

from mcp.server.fastmcp import Context

from app.errors import DayMateError

USER_HEADER = "x-daymate-user-id"


class BearerAuthMiddleware:
    """Pure-ASGI shared-secret check for every path except /health (safe for streaming responses)."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        supplied = headers.get(b"authorization", b"")
        expected = b"Bearer " + self.token
        if not self.token or not hmac.compare_digest(supplied, expected):
            body = json.dumps({"error": "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def current_user_id(ctx: Context) -> int:
    """User id comes from the trusted header, never from model-supplied tool arguments."""
    request = ctx.request_context.request
    raw = request.headers.get(USER_HEADER) if request is not None else None
    if not raw or not raw.isdigit():
        raise DayMateError("Missing or invalid user context.", code="unauthenticated")
    return int(raw)
