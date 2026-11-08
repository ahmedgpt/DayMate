"""Outbound-only Telegram delivery used by the REST API (so n8n never needs direct Telegram access)."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import httpx

log = logging.getLogger("daymate.telegram")


class DeliveryError(Exception):
    """The message was not (confirmed) delivered. Text is safe to store/log: it never contains the bot token."""


class MessageSender(Protocol):
    async def send(self, chat_id: str, text: str) -> None: ...


class TelegramSender:
    CONNECT_RETRIES = 2  # only for failures where the request provably never reached Telegram

    def __init__(self, token: str, timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.timeout, self.transport = timeout, transport

    async def send(self, chat_id: str, text: str) -> None:
        last = "unknown error"
        for attempt in range(1 + self.CONNECT_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                    resp = await client.post(self._url, json={"chat_id": chat_id, "text": text[:4000]})
            except (httpx.ConnectError, httpx.ConnectTimeout):
                last = "could not connect to Telegram"
                await asyncio.sleep(1 + attempt)
                continue
            except httpx.HTTPError as e:  # e.g. read timeout: Telegram may or may not have the message; don't retry
                raise DeliveryError(f"Telegram request failed ({type(e).__name__})") from None
            if resp.status_code == 200 and resp.json().get("ok"):
                return
            try:
                desc = resp.json().get("description", "")
            except ValueError:
                desc = ""
            raise DeliveryError(f"Telegram rejected the message (HTTP {resp.status_code}): {desc[:200]}")
        raise DeliveryError(last)
