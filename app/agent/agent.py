"""Tool-calling loop: LLM <-> MCP tools. Persistent data lives only in the database."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque

from app.agent.llm import LLM, LLMError
from app.agent.mcp_client import Toolbox
from app.agent.prompts import build_system_prompt
from app.services.user_service import UserService
from app.utils.datetime_utils import get_zone, utcnow

log = logging.getLogger("daymate.agent")

MAX_TOOL_ROUNDS = 8
OVERALL_TIMEOUT = 120  # seconds for one user message
HISTORY_TURNS = 8  # short in-memory chat context for clarifications only; the diary itself is in SQLite

LLM_DOWN = "I'm having trouble reaching my language model right now, so I haven't done anything. Please try again in a moment."
TOOLS_DOWN = "I can't reach my diary tools right now, so I haven't saved or changed anything. Please try again shortly."
GAVE_UP = "I couldn't finish that request. Please try rephrasing it; nothing is confirmed unless I said so."


def _contains(exc: BaseException, kind: type) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return any(_contains(e, kind) for e in exc.exceptions)
    return isinstance(exc, kind)


class DayMateAgent:
    def __init__(self, llm: LLM, users: UserService, mcp_url: str, mcp_token: str):
        self.llm, self.users, self.mcp_url, self.mcp_token = llm, users, mcp_url, mcp_token
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))

    def _system_prompt(self, user_id: int) -> str:
        tz = self.users.get(user_id)["timezone"]
        try:
            return build_system_prompt(tz, utcnow().astimezone(get_zone(tz))) if tz else build_system_prompt(None, None)
        except Exception:  # noqa: BLE001 - stored tz became invalid; fall back to asking again
            return build_system_prompt(None, None)

    async def handle(self, user_id: int, text: str) -> str:
        history = self._history[user_id]
        messages = [*history, {"role": "user", "content": text}]
        try:
            async with asyncio.timeout(OVERALL_TIMEOUT):
                async with Toolbox.open(self.mcp_url, self.mcp_token, user_id) as tools:
                    reply = await self._loop(user_id, messages, tools)
        except Exception as e:  # noqa: BLE001 - never claim success on failure
            # Errors raised inside the MCP session's task groups arrive wrapped in ExceptionGroups.
            if _contains(e, LLMError):
                log.exception("llm failure")
                return LLM_DOWN
            log.exception("agent failure")
            return TOOLS_DOWN
        history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        return reply

    async def _loop(self, user_id: int, messages: list[dict], tools: Toolbox) -> str:
        tool_defs = await tools.list_tools()
        for _ in range(MAX_TOOL_ROUNDS):
            reply = await self.llm.complete(self._system_prompt(user_id), messages, tool_defs)
            if not reply.tool_calls:
                return reply.text or GAVE_UP
            messages.append({"role": "assistant", "content": reply.assistant_content})
            results = []
            for call in reply.tool_calls:
                try:
                    content, is_error = await tools.call(call.name, call.arguments)
                except Exception:  # noqa: BLE001
                    log.exception("tool call failed: %s", call.name)
                    content, is_error = '{"success": false, "error": "tool_unavailable"}', True
                log.info("tool %s(%s) -> error=%s", call.name, call.arguments, is_error)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        return GAVE_UP
