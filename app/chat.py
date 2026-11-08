"""Platform-independent chat controller: identify user, handle commands, delegate to the agent."""
from __future__ import annotations

from app.agent.agent import DayMateAgent
from app.messaging.interface import IncomingMessage
from app.services.user_service import UserService

WELCOME = """\
Hi! I'm DayMate, your personal daily assistant. You can tell me what you did, ask me to set reminders, or request a summary.

Try:
- Log that I studied for 2 hours.
- Remind me tomorrow at 10 AM to submit my assignment.
- What did I do today?
- Show my upcoming reminders.
- Summarize my week.
- What's the weather in Lahore?

First, tell me which city or timezone you're in so I get times right."""

UNSUPPORTED = "I can only read text messages for now. Please type what you'd like to log or schedule."


class ChatController:
    def __init__(self, users: UserService, agent: DayMateAgent):
        self.users, self.agent = users, agent

    async def handle(self, msg: IncomingMessage) -> str:
        user = self.users.get_or_create(msg.platform, msg.platform_user_id, msg.chat_id)
        text = (msg.text or "").strip()
        if msg.text is None or not text:
            return UNSUPPORTED
        if text.split()[0].lower().split("@")[0] in ("/start", "/help"):
            return WELCOME
        return await self.agent.handle(user["id"], text)
