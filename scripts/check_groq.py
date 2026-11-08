"""Live end-to-end check of the Groq-powered agent against the real MCP tools.

    set GROQ_API_KEY=...        (or put it in .env)
    python scripts/check_groq.py [model]

Uses a temporary database and an in-process MCP server, so your real data is untouched.
Only Groq's API is called for the LLM (free tier is enough) and Open-Meteo is not used.
"""
from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import groq  # noqa: E402
import uvicorn  # noqa: E402

from app.agent.agent import DayMateAgent  # noqa: E402
from app.agent.llm import GroqLLM  # noqa: E402
from app.config import Settings  # noqa: E402
from app.mcp_server.server import create_app  # noqa: E402
from app.services.container import Services  # noqa: E402
from app.utils.datetime_utils import local_today  # noqa: E402

TOKEN = "live-check"
TZ = "Asia/Karachi"


async def pick_model(api_key: str, wanted: str) -> str:
    client = groq.AsyncGroq(api_key=api_key)
    ids = sorted(m.id for m in (await client.models.list()).data)
    print(f"Models visible to your account ({len(ids)}):\n  " + "\n  ".join(ids))
    if wanted not in ids:
        raise SystemExit(f"\nModel {wanted!r} is not available to your account. Pass one of the models above that "
                         "supports tool calling, or set LLM_MODEL (e.g. openai/gpt-oss-120b).")
    print(f"\nUsing model: {wanted}")
    return wanted


def start_server(svc: Services) -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/mcp"


async def main() -> int:
    base = Settings.from_env()
    base.require("groq_api_key")
    model = await pick_model(base.groq_api_key, sys.argv[1] if len(sys.argv) > 1 else base.llm_model)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(database_path=f"{tmp}/live.db", mcp_auth_token=TOKEN, groq_api_key=base.groq_api_key,
                            llm_model=model)
        svc = Services.build(settings)
        user = svc.users.get_or_create("cli", "live", "live")
        svc.users.set_timezone(user["id"], TZ)
        agent = DayMateAgent(GroqLLM(settings.groq_api_key, model), svc.users, start_server(svc), TOKEN)
        uid = user["id"]
        today = local_today(TZ)
        tomorrow = (today + timedelta(days=1)).isoformat()
        results: list[tuple[str, bool, str]] = []

        async def ask(text: str) -> str:
            reply = await agent.handle(uid, text)
            print(f"\nyou> {text}\ndaymate> {reply}")
            await asyncio.sleep(2)  # stay under free-tier request limits
            return reply

        def entries():
            return svc.diary.get_entries(uid, today.isoformat(), today.isoformat())

        def check(name: str, ok: bool, detail: str = "") -> None:
            results.append((name, ok, detail))

        await ask("Today I worked on my Python project for 3 hours and attended a team meeting.")
        check("diary: entry saved for today", any("python" in e["description"].lower() for e in entries()), str(entries()))

        reply = await ask("What did I do today?")
        check("diary: retrieval mentions saved entry", "python" in reply.lower(), reply)

        await ask("Actually, change my Python entry to say I worked on the DayMate project for 3 hours.")
        check("diary: entry updated", any("daymate" in e["description"].lower() for e in entries()), str(entries()))

        await ask("Remind me tomorrow at 10 AM to submit my assignment.")
        pending = svc.reminders.list(uid)
        check("reminder: created for tomorrow 10:00 local",
              len(pending) == 1 and f"{tomorrow} 10:00" in pending[0]["scheduled_local"]
              and "assignment" in pending[0]["message"].lower(), str(pending))

        reply = await ask("Show my upcoming reminders.")
        check("reminder: listing mentions it", "assignment" in reply.lower(), reply)

        before = len(svc.reminders.list(uid))
        await ask("Remind me tomorrow morning to call my mother.")
        check("reminder: vague time -> asks instead of guessing", len(svc.reminders.list(uid)) == before,
              str(svc.reminders.list(uid)))

        reply = await ask("Summarize today.")
        check("summary: daily mentions recorded activity", "daymate" in reply.lower() or "project" in reply.lower(), reply)

        reply = await ask("Give me a summary of my week.")
        check("summary: weekly responds with content", len(reply) > 20 and "couldn't" not in reply.lower(), reply)

        await ask("Cancel my assignment reminder.")
        check("reminder: cancelled", not any("assignment" in r["message"].lower() for r in svc.reminders.list(uid)),
              str(svc.reminders.list(uid)))

        print("\n==== RESULTS ====")
        for name, ok, detail in results:
            print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n     detail: {detail}"))
        return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
