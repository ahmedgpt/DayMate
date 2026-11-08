"""Provider-neutral LLM interface plus the Groq implementation.

The agent speaks one message shape (Anthropic-style content blocks: text / tool_use / tool_result).
`GroqLLM.complete` translates that to and from Groq's OpenAI-compatible chat format.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

import groq


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_content: list[dict] = field(default_factory=list)


class LLMError(Exception):
    """Temporary provider failure (rate limit, outage, auth, unusable output)."""


class LLM(Protocol):
    async def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply: ...


def to_groq_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


def to_groq_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text")
            msg: dict = {"role": "assistant", "content": text or None}
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
                     for b in content if b["type"] == "tool_use"]
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user turn made of tool results -> one 'tool' message per result
            for b in content:
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
    return out


def _clean_args(raw: str | None) -> dict:
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(args, dict):
        return {}
    # Smaller models sometimes send null / "null" for optional arguments; treat them as omitted.
    return {k: v for k, v in args.items() if v is not None and v != "null"}


class GroqLLM:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1024, client: groq.AsyncGroq | None = None):
        self.client = client or groq.AsyncGroq(api_key=api_key)
        self.model, self.max_tokens = model, max_tokens

    async def _create(self, **kwargs):
        try:
            return await self.client.chat.completions.create(**kwargs)
        except groq.BadRequestError as e:
            if "tool_use_failed" in str(e):  # model produced a malformed tool call; one retry usually fixes it
                try:
                    return await self.client.chat.completions.create(**kwargs)
                except groq.APIError as e2:
                    raise LLMError(str(e2)) from e2
            raise LLMError(str(e)) from e
        except groq.APIError as e:
            raise LLMError(str(e)) from e

    async def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        resp = await self._create(
            model=self.model, messages=to_groq_messages(system, messages), tools=to_groq_tools(tools),
            tool_choice="auto", max_tokens=self.max_tokens, temperature=0.2)
        if not resp.choices:
            raise LLMError("Groq returned no choices")
        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        calls = [ToolCall(c.id, c.function.name, _clean_args(c.function.arguments)) for c in (msg.tool_calls or [])]
        content: list[dict] = ([{"type": "text", "text": text}] if text else [])
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls]
        return LLMReply(text, calls, content)
