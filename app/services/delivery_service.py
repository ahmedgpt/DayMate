"""Host-side reminder delivery. The ONLY place a reminder moves leased -> sent / retry / failed.

n8n schedules and claims (lease); this service sends and records the outcome, so a reminder is marked
`sent` strictly after Telegram confirmed the message.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from app.messaging.telegram_sender import DeliveryError, MessageSender
from app.services.reminder_service import ReminderService
from app.utils.datetime_utils import format_local, to_iso_utc, utcnow

log = logging.getLogger("daymate.delivery")


@dataclass
class DeliveryResult:
    """http_status: 200 handled (sent or intentionally skipped), 409 not claimed, 404 unknown,
    502 send failed (state already recorded), 503 sender not configured."""
    http_status: int
    status: str  # sent | skipped | pending | failed | not_claimed | not_found | unavailable
    detail: str = ""

    def as_dict(self) -> dict:
        return {"status": self.status, "delivered": self.status == "sent", "detail": self.detail}


class DeliveryService:
    def __init__(self, reminders: ReminderService, sender: MessageSender | None):
        self.reminders, self.sender = reminders, sender
        self._locks: dict[int, asyncio.Lock] = {}

    async def deliver(self, reminder_id: int, now: datetime | None = None) -> DeliveryResult:
        if self.sender is None:
            return DeliveryResult(503, "unavailable", "Telegram is not configured on the DayMate server.")
        # One delivery per reminder at a time within this process: a duplicate/retried call waits, then sees `sent`.
        async with self._locks.setdefault(reminder_id, asyncio.Lock()):
            return await self._deliver(reminder_id, now or utcnow())

    async def _deliver(self, reminder_id: int, now: datetime) -> DeliveryResult:
        row = await asyncio.to_thread(self.reminders.get_for_delivery, reminder_id)
        if row is None:
            return DeliveryResult(404, "not_found", "No such reminder.")
        if row["status"] != "pending":  # already sent / cancelled / failed: never send again
            return DeliveryResult(200, "skipped", f"Reminder is {row['status']}; nothing sent.")
        lease = row["lease_until_utc"]
        if not lease or lease <= to_iso_utc(now):
            return DeliveryResult(409, "not_claimed", "Reminder has no active lease; claim it first.")
        if row["platform"] != "telegram" or not row["chat_id"]:
            await asyncio.to_thread(self.reminders.mark_failed, reminder_id, "user has no Telegram chat", True)
            return DeliveryResult(200, "failed", "User has no Telegram chat; marked failed.")

        text = f"⏰ Reminder: {row['message']}\n(scheduled for {format_local(row['scheduled_at_utc'], row['timezone'])})"
        try:
            await self.sender.send(row["chat_id"], text)
        except DeliveryError as e:
            status = await asyncio.to_thread(self.reminders.mark_failed, reminder_id, str(e))
            log.warning("reminder %s delivery failed (%s) -> %s", reminder_id, e, status)
            return DeliveryResult(502, status or "pending", str(e))
        # Telegram confirmed. If this write fails the lease eventually expires and the reminder is re-sent
        # (at-least-once); it is never marked sent before this point.
        marked = await asyncio.to_thread(self.reminders.mark_sent, reminder_id)
        log.info("reminder %s delivered (marked_sent=%s)", reminder_id, marked)
        return DeliveryResult(200, "sent")

    async def send_to_known_chat(self, chat_id: str, text: str) -> DeliveryResult:
        """Plain message to a registered user's chat (used by the n8n summary workflows)."""
        if self.sender is None:
            return DeliveryResult(503, "unavailable", "Telegram is not configured on the DayMate server.")
        if not await asyncio.to_thread(self.reminders.is_known_chat, "telegram", str(chat_id)):
            return DeliveryResult(403, "failed", "chat_id does not belong to a registered user.")
        try:
            await self.sender.send(str(chat_id), text)
        except DeliveryError as e:
            return DeliveryResult(502, "failed", str(e))
        return DeliveryResult(200, "sent")
