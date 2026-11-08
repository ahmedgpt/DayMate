import asyncio
import json
from types import SimpleNamespace as NS

import groq
import httpx
import pytest

from app.agent.llm import GroqLLM, LLMError, to_groq_messages, to_groq_tools

TOOLS = [{"name": "add_diary_entry", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
REQ = httpx.Request("POST", "https://api.groq.com/x")


def _resp(text=None, calls=()):
    tcs = [NS(id=i, function=NS(name=n, arguments=a)) for i, n, a in calls]
    return NS(choices=[NS(message=NS(content=text, tool_calls=tcs or None))])


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def _bad_request(msg):
    return groq.BadRequestError(msg, response=httpx.Response(400, request=REQ), body=None)


def _complete(client):
    return asyncio.run(GroqLLM("k", "m", client=client).complete("s", [{"role": "user", "content": "x"}], TOOLS))


def test_tool_schema_and_message_translation():
    assert to_groq_tools(TOOLS)[0] == {"type": "function", "function": {
        "name": "add_diary_entry", "description": "d", "parameters": TOOLS[0]["input_schema"]}}
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"},
                                          {"type": "tool_use", "id": "c1", "name": "t", "input": {"a": 1}},
                                          {"type": "tool_use", "id": "c2", "name": "u", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "R1", "is_error": False},
                                     {"type": "tool_result", "tool_use_id": "c2", "content": "R2", "is_error": True}]},
    ]
    out = to_groq_messages("SYS", msgs)
    assert out[0] == {"role": "system", "content": "SYS"} and out[1] == {"role": "user", "content": "hi"}
    assert out[2]["content"] == "ok" and [c["id"] for c in out[2]["tool_calls"]] == ["c1", "c2"]
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"a": 1}
    assert out[3:] == [{"role": "tool", "tool_call_id": "c1", "content": "R1"},
                       {"role": "tool", "tool_call_id": "c2", "content": "R2"}]


def test_tool_only_assistant_turn_has_null_content():
    out = to_groq_messages("S", [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "c", "name": "t", "input": {}}]}])
    assert out[1]["content"] is None and out[1]["tool_calls"][0]["id"] == "c"


def test_complete_parses_tool_calls_and_drops_null_args():
    fake = FakeClient(_resp(None, [("c1", "add_diary_entry",
                                    '{"description": "x", "category": null, "entry_date": "null"}')]))
    r = _complete(fake)
    assert r.tool_calls[0].name == "add_diary_entry" and r.tool_calls[0].arguments == {"description": "x"}
    assert r.assistant_content == [{"type": "tool_use", "id": "c1", "name": "add_diary_entry",
                                    "input": {"description": "x"}}]
    assert fake.calls[0]["model"] == "m" and fake.calls[0]["tool_choice"] == "auto"


def test_complete_plain_text_and_malformed_json():
    r = _complete(FakeClient(_resp(" hello ")))
    assert r.text == "hello" and not r.tool_calls
    assert _complete(FakeClient(_resp(None, [("c", "t", "{oops")]))).tool_calls[0].arguments == {}


def test_tool_use_failed_retried_once_then_surfaced():
    ok = FakeClient(_bad_request("tool_use_failed"), _resp("fine"))
    assert _complete(ok).text == "fine" and len(ok.calls) == 2
    with pytest.raises(LLMError):
        _complete(FakeClient(_bad_request("tool_use_failed"), _bad_request("tool_use_failed")))
    other = FakeClient(_bad_request("model_not_found"))
    with pytest.raises(LLMError):
        _complete(other)
    assert len(other.calls) == 1


def test_rate_limit_becomes_llm_error():
    rl = groq.RateLimitError("slow down", response=httpx.Response(429, request=REQ), body=None)
    with pytest.raises(LLMError):
        _complete(FakeClient(rl))
