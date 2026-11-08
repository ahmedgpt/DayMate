import asyncio
import json
import socket
import threading
import time
from datetime import timedelta

import httpx
import pytest
import uvicorn

from app.agent.agent import DayMateAgent, LLM_DOWN
from app.agent.llm import LLMError, LLMReply, ToolCall
from app.agent.mcp_client import Toolbox
from app.mcp_server.server import create_app
from app.utils.datetime_utils import utcnow

TOKEN = "test-token"


@pytest.fixture
def server(svc):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


async def call(base, user_id, name, args):
    async with Toolbox.open(f"{base}/mcp", TOKEN, user_id) as tb:
        text, is_error = await tb.call(name, args)
    return json.loads(text)


def test_tools_are_discoverable_and_hide_context(server, user):
    async def go():
        async with Toolbox.open(f"{server}/mcp", TOKEN, user["id"]) as tb:
            return await tb.list_tools()
    tools = {t["name"]: t for t in asyncio.run(go())}
    assert {"add_diary_entry", "get_diary_entries", "update_diary_entry", "create_reminder", "list_reminders",
            "delete_reminder", "get_daily_summary", "get_weekly_summary", "get_weather", "set_timezone"} <= set(tools)
    for t in tools.values():
        assert "user_id" not in t["input_schema"].get("properties", {})
        assert "ctx" not in t["input_schema"].get("properties", {})


def test_diary_roundtrip_and_isolation_over_mcp(server, svc, user, other_user):
    added = asyncio.run(call(server, user["id"], "add_diary_entry", {"description": "Python 3h", "entry_date": "2026-11-01"}))
    assert added["success"] and added["entry"]["id"]
    mine = asyncio.run(call(server, user["id"], "get_diary_entries", {"start_date": "2026-11-01", "end_date": "2026-11-01"}))
    theirs = asyncio.run(call(server, other_user["id"], "get_diary_entries", {"start_date": "2026-11-01", "end_date": "2026-11-01"}))
    assert mine["count"] == 1 and theirs["count"] == 0
    hijack = asyncio.run(call(server, other_user["id"], "update_diary_entry",
                              {"entry_id": added["entry"]["id"], "description": "x"}))
    assert hijack["success"] is False and hijack["error"] == "not_found"
    assert svc.diary.get_entries(user["id"], "2026-11-01", "2026-11-01")[0]["description"] == "Python 3h"


def test_validation_errors_are_structured(server, user):
    r = asyncio.run(call(server, user["id"], "create_reminder", {"message": "x", "scheduled_at": "2020-01-01T10:00:00"}))
    assert r == {"success": False, "error": "time_in_past", "message": r["message"]}


def test_missing_user_context_and_bad_token(server):
    async def no_user():
        async with Toolbox.open(f"{server}/mcp", TOKEN, 0) as tb:  # digit but unknown user
            return await tb.call("get_diary_entries", {})
    text, _ = asyncio.run(no_user())
    assert '"success": false' in text and "unknown_user" in text

    assert httpx.post(f"{server}/api/reminders/claim-due").status_code == 401
    assert httpx.get(f"{server}/health").status_code == 200


def test_n8n_rest_flow_claim_deliver_retry_sent(server, svc, user):
    from app.messaging.telegram_sender import DeliveryError

    class Sender:
        def __init__(self):
            self.sent, self.fail = [], True

        async def send(self, chat_id, text):
            if self.fail:
                raise DeliveryError("could not connect to Telegram")
            self.sent.append((chat_id, text))

    sender = Sender()
    svc.delivery.sender = sender
    when = (utcnow() + timedelta(hours=1)).astimezone(__import__("datetime").timezone(timedelta(hours=5)))
    r = svc.reminders.create(user["id"], "ping", when.strftime("%Y-%m-%dT%H:%M:00"))
    h = {"Authorization": f"Bearer {TOKEN}"}
    post = lambda path, **kw: httpx.post(f"{server}{path}", headers=h, **kw)  # noqa: E731
    assert post("/api/reminders/claim-due").json() == {"reminders": []}  # not due yet
    assert post(f"/api/reminders/{r['id']}/deliver").status_code == 409  # cannot deliver unclaimed
    with svc.db.tx() as c:
        c.execute("UPDATE reminders SET scheduled_at_utc = '2020-01-01T00:00:00Z' WHERE id = ?", (r["id"],))
    claimed = post("/api/reminders/claim-due").json()["reminders"]
    assert [x["id"] for x in claimed] == [r["id"]] and claimed[0]["chat_id"] == "111"
    assert post("/api/reminders/claim-due").json() == {"reminders": []}  # leased: overlapping run gets nothing

    failed = post(f"/api/reminders/{r['id']}/deliver")  # Telegram down -> 502, still pending
    assert failed.status_code == 502 and failed.json()["status"] == "pending" and sender.sent == []
    assert svc.reminders.list(user["id"], "sent") == []
    assert len(post("/api/reminders/claim-due").json()["reminders"]) == 1  # retryable

    sender.fail = False
    ok = post(f"/api/reminders/{r['id']}/deliver")
    assert ok.status_code == 200 and ok.json()["delivered"] is True and sender.sent[0][0] == "111"
    again = post(f"/api/reminders/{r['id']}/deliver")  # n8n retry: no duplicate message
    assert again.json()["status"] == "skipped" and len(sender.sent) == 1
    assert svc.reminders.list(user["id"], "sent")[0]["id"] == r["id"]

    assert post("/api/telegram/send", json={"chat_id": "111", "text": "summary"}).status_code == 200
    assert post("/api/telegram/send", json={"chat_id": "999999", "text": "x"}).status_code == 403
    assert post("/api/telegram/send", json={"text": "x"}).status_code == 400
    assert httpx.post(f"{server}/api/reminders/1/deliver").status_code == 401  # no token
    assert httpx.get(f"{server}/api/digests/daily?hour=99", headers=h).status_code == 400
    assert httpx.get(f"{server}/api/digests/weekly?weekday=9", headers=h).status_code == 400
    assert httpx.get(f"{server}/api/digests/weekly", headers=h).json() == {"digests": []}


class ScriptedLLM:
    """Stands in for the model: replays prepared replies and records what it was given."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    async def complete(self, system, messages, tools):
        self.seen.append((system, [dict(m) for m in messages], tools))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_reply(call_id, name, args):
    return LLMReply("", [ToolCall(call_id, name, args)],
                    [{"type": "tool_use", "id": call_id, "name": name, "input": args}])


def test_agent_invokes_mcp_tool_and_reports_result(server, svc, user):
    llm = ScriptedLLM(tool_reply("t1", "add_diary_entry", {"description": "Worked on Python", "duration_minutes": 180}),
                      LLMReply("Saved to your diary."))
    agent = DayMateAgent(llm, svc.users, f"{server}/mcp", TOKEN)
    assert asyncio.run(agent.handle(user["id"], "Today I worked on Python for 3 hours")) == "Saved to your diary."
    entry = svc.diary.get_entries(user["id"])[0]
    assert entry["description"] == "Worked on Python" and entry["duration_minutes"] == 180
    system, msgs, tools = llm.seen[0]
    assert "Asia/Karachi" in system and len(tools) >= 10
    result_block = llm.seen[1][1][-1]["content"][0]  # tool result fed back to the model
    assert result_block["type"] == "tool_result" and '"success": true' in result_block["content"]


def test_agent_prompts_for_timezone_when_unset(server, svc):
    fresh = svc.users.get_or_create("telegram", "999", "999")
    llm = ScriptedLLM(LLMReply("Which city are you in?"))
    agent = DayMateAgent(llm, svc.users, f"{server}/mcp", TOKEN)
    asyncio.run(agent.handle(fresh["id"], "remind me tomorrow"))
    assert "NOT set" in llm.seen[0][0]


def test_agent_does_not_claim_success_when_llm_or_tools_down(server, svc, user):
    agent = DayMateAgent(ScriptedLLM(LLMError("overloaded")), svc.users, f"{server}/mcp", TOKEN)
    assert asyncio.run(agent.handle(user["id"], "hi")) == LLM_DOWN
    dead = DayMateAgent(ScriptedLLM(LLMReply("ok")), svc.users, "http://127.0.0.1:9/mcp", TOKEN)
    assert "haven't saved" in asyncio.run(dead.handle(user["id"], "log x"))
    assert svc.diary.get_entries(user["id"]) == []


def test_agent_with_groqllm_roundtrip_over_real_mcp(server, svc, user):
    """GroqLLM (faked HTTP client) -> agent -> real MCP tool -> tool result fed back in Groq 'tool' format."""
    from types import SimpleNamespace as NS
    from app.agent.llm import GroqLLM

    def resp(text=None, calls=()):
        tcs = [NS(id=i, function=NS(name=n, arguments=a)) for i, n, a in calls]
        return NS(choices=[NS(message=NS(content=text, tool_calls=tcs or None))])

    sent = []
    outcomes = [resp(None, [("call_1", "add_diary_entry", '{"description": "Went for a walk", "category": null}')]),
                resp("Saved your walk.")]

    async def create(**kw):
        sent.append(kw["messages"])
        return outcomes.pop(0)

    llm = GroqLLM("k", "m", client=NS(chat=NS(completions=NS(create=create))))
    agent = DayMateAgent(llm, svc.users, f"{server}/mcp", TOKEN)
    assert asyncio.run(agent.handle(user["id"], "I went for a walk")) == "Saved your walk."
    assert svc.diary.get_entries(user["id"])[0]["description"] == "Went for a walk"
    tool_msg = sent[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1" and '"success": true' in tool_msg["content"]
