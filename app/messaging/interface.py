"""Platform-neutral chat contract. Adapters translate platform events into `IncomingMessage`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class IncomingMessage:
    platform: str
    platform_user_id: str
    chat_id: str
    text: str | None  # None => unsupported message type (photo, voice, ...)


MessageHandler = Callable[[IncomingMessage], Awaitable[str]]
