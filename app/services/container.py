from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.database.connection import Database
from app.messaging.telegram_sender import MessageSender, TelegramSender
from app.services.delivery_service import DeliveryService
from app.services.diary_service import DiaryService
from app.services.reminder_service import ReminderService
from app.services.summary_service import SummaryService
from app.services.user_service import UserService
from app.services.weather_service import WeatherService


@dataclass
class Services:
    settings: Settings
    db: Database
    users: UserService
    diary: DiaryService
    reminders: ReminderService
    summaries: SummaryService
    weather: WeatherService
    delivery: DeliveryService

    @classmethod
    def build(cls, settings: Settings, weather: WeatherService | None = None,
              sender: MessageSender | None = None) -> "Services":
        db = Database(settings.database_path)
        users = UserService(db)
        diary = DiaryService(db, users)
        reminders = ReminderService(db, users, settings.reminder_lease_seconds, settings.reminder_max_attempts)
        if sender is None and settings.telegram_bot_token:
            sender = TelegramSender(settings.telegram_bot_token)
        return cls(
            settings=settings, db=db, users=users, diary=diary,
            reminders=reminders,
            summaries=SummaryService(db, users, diary),
            weather=weather or WeatherService(settings.weather_timeout_seconds),
            delivery=DeliveryService(reminders, sender),
        )
