"""Telegram adapter using long polling (no public URL needed). Thin: no business logic."""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.messaging.interface import IncomingMessage, MessageHandler

log = logging.getLogger("daymate.telegram")
POLL_TIMEOUT = 30


class TelegramBot:
    def __init__(self, token: str, handler: MessageHandler):
        self.base = f"https://api.telegram.org/bot{token}"
        self.handler = handler
        self._offset: int | None = None  # advancing the offset also de-duplicates redelivered updates
        self._client = httpx.AsyncClient(timeout=POLL_TIMEOUT + 10)

    async def _call(self, method: str, **params):
        resp = await self._client.post(f"{self.base}/{method}", json=params)
        resp.raise_for_status()
        return resp.json()["result"]

    async def send(self, chat_id: str, text: str) -> None:
        for i in range(0, len(text) or 1, 4000):  # Telegram's limit is 4096 chars
            await self._call("sendMessage", chat_id=chat_id, text=text[i:i + 4000])

    async def _process(self, update: dict) -> None:
        msg = update.get("message")
        if not msg or "from" not in msg or msg["chat"].get("type") != "private":
            return
        incoming = IncomingMessage("telegram", str(msg["from"]["id"]), str(msg["chat"]["id"]), msg.get("text"))
        try:
            reply = await self.handler(incoming)
        except Exception:  # noqa: BLE001
            log.exception("handler failed")
            reply = "Sorry, something went wrong on my side. Nothing was changed."
        await self.send(incoming.chat_id, reply)

    async def run(self) -> None:
        log.info("Telegram bot polling...")
        while True:
            try:
                updates = await self._call("getUpdates", offset=self._offset, timeout=POLL_TIMEOUT,
                                           allowed_updates=["message"])
            except (httpx.HTTPError, KeyError) as e:
                log.warning("getUpdates failed: %s", type(e).__name__)  # message may contain the token URL; omit it
                await asyncio.sleep(5)
                continue
            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    await self._process(update)
                except httpx.HTTPError as e:
                    log.warning("send failed: %s", type(e).__name__)
