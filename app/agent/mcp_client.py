"""MCP client used by the agent. One session per user message, authenticated as that user."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

CONNECT_TIMEOUT = 15


class Toolbox:
    def __init__(self, session: ClientSession):
        self._session = session

    @classmethod
    @asynccontextmanager
    async def open(cls, url: str, token: str, user_id: int) -> AsyncIterator["Toolbox"]:
        headers = {"Authorization": f"Bearer {token}", "X-DayMate-User-Id": str(user_id)}
        async with create_mcp_http_client(headers=headers, timeout=httpx.Timeout(30, read=300)) as client:
            async with streamable_http_client(url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield cls(session)

    async def list_tools(self) -> list[dict]:
        """Tool definitions in the Anthropic `tools` format."""
        result = await self._session.list_tools()
        return [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
                for t in result.tools]

    async def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Returns (json_text, is_error)."""
        result = await self._session.call_tool(name, arguments)
        if result.structuredContent is not None:
            payload = result.structuredContent.get("result", result.structuredContent)
            return json.dumps(payload, ensure_ascii=False), bool(result.isError)
        text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
        return text, bool(result.isError)
