"""Entry points:  python -m app.main {server|bot|chat}

server  MCP server + REST API for n8n (needs MCP_AUTH_TOKEN)
bot     Telegram bot + agent (needs TELEGRAM_BOT_TOKEN, GROQ_API_KEY, MCP_AUTH_TOKEN; server must be running)
chat    Terminal chat as a local test user (no Telegram needed; same agent + MCP path)
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.agent.agent import DayMateAgent
from app.agent.llm import GroqLLM
from app.chat import ChatController
from app.config import Settings
from app.messaging.interface import IncomingMessage
from app.messaging.telegram_bot import TelegramBot
from app.services.container import Services


def _controller(settings: Settings) -> tuple[ChatController, Services]:
    settings.require("groq_api_key", "mcp_auth_token")
    svc = Services.build(settings)
    agent = DayMateAgent(GroqLLM(settings.groq_api_key, settings.llm_model), svc.users,
                         settings.mcp_url, settings.mcp_auth_token)
    return ChatController(svc.users, agent), svc


async def run_bot(settings: Settings) -> None:
    settings.require("telegram_bot_token")
    controller, _ = _controller(settings)
    await TelegramBot(settings.telegram_bot_token, controller.handle).run()


async def run_chat(settings: Settings) -> None:
    controller, _ = _controller(settings)
    print("DayMate terminal chat (local user). Ctrl+C to quit.")
    loop = asyncio.get_running_loop()
    while True:
        text = await loop.run_in_executor(None, input, "you> ")
        print("daymate>", await controller.handle(IncomingMessage("cli", "local", "local", text)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs include the bot token
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    settings = Settings.from_env()
    if mode == "server":
        from app.mcp_server.server import main as serve
        serve()
    elif mode == "bot":
        asyncio.run(run_bot(settings))
    elif mode == "chat":
        try:
            asyncio.run(run_chat(settings))
        except (KeyboardInterrupt, EOFError):
            pass
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
