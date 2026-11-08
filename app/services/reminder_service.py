from __future__ import annotations

from datetime import datetime

from app.database import repository as repo
from app.database.connection import Database
from app.errors import NotFoundError, ValidationError
from app.services.user_service import UserService
from app.utils.datetime_utils import format_local, parse_local_datetime, to_iso_utc, utcnow

VALID_STATUSES = {"pending", "sent", "cancelled", "failed"}


class ReminderService:
    def __init__(self, db: Database, users: UserService, lease_seconds: int = 300, max_attempts: int = 5):
        self.db, self.users = db, users
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts

    def _present(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "message": row["message"],
            "status": row["status"],
            "scheduled_at_utc": row["scheduled_at_utc"],
            "scheduled_local": format_local(row["scheduled_at_utc"], row["timezone"]),
            "timezone": row["timezone"],
        }

    def create(self, user_id: int, message: str, scheduled_at: str, now: datetime | None = None) -> dict:
        message = (message or "").strip()
        if not message:
            raise ValidationError("Reminder message must not be empty.")
        if len(message) > 1000:
            raise ValidationError("Reminder message is too long (max 1000 characters).")
        tz = self.users.get(user_id)["timezone"]
        when = parse_local_datetime(scheduled_at, tz)  # raises timezone_not_set if unset
        if when <= (now or utcnow()):
            raise ValidationError("That time is in the past. Please give a future date and time.", code="time_in_past")
        with self.db.tx() as conn:
            return self._present(repo.insert_reminder(conn, user_id, message, to_iso_utc(when), tz))

    def list(self, user_id: int, status: str | None = "pending", from_datetime: str | None = None,
             to_datetime: str | None = None) -> list[dict]:
        if status is not None and status not in VALID_STATUSES:
            raise ValidationError(f"status must be one of {sorted(VALID_STATUSES)}.")
        tz = self.users.get(user_id)["timezone"]
        lo = to_iso_utc(parse_local_datetime(from_datetime, tz)) if from_datetime else None
        hi = to_iso_utc(parse_local_datetime(to_datetime, tz)) if to_datetime else None
        with self.db.tx() as conn:
            return [self._present(r) for r in repo.list_reminders(conn, user_id, status, lo, hi)]

    def cancel(self, user_id: int, reminder_id: int) -> dict:
        with self.db.tx() as conn:
            row = repo.get_reminder(conn, user_id, reminder_id)
            if not row:
                raise NotFoundError(f"No reminder with id {reminder_id}.")
            if row["status"] == "cancelled":
                return self._present(row)
            if not repo.cancel_reminder(conn, user_id, reminder_id):
                raise ValidationError(f"Reminder {reminder_id} is already {row['status']} and cannot be cancelled.",
                                      code="not_cancellable")
            return self._present(repo.get_reminder(conn, user_id, reminder_id))

    # --- delivery side (used by the n8n-facing REST API; not user scoped) ---
    def claim_due(self, limit: int = 20, now: datetime | None = None) -> list[dict]:
        with self.db.tx() as conn:
            rows = repo.claim_due_reminders(conn, self.lease_seconds, limit, now)
        return [{**self._present(r), "chat_id": r["chat_id"], "platform": r["platform"],
                 "attempt_count": r["attempt_count"]} for r in rows]

    def get_for_delivery(self, reminder_id: int) -> dict | None:
        with self.db.tx() as conn:
            return repo.get_reminder_for_delivery(conn, reminder_id)

    def is_known_chat(self, platform: str, chat_id: str) -> bool:
        with self.db.tx() as conn:
            return repo.user_owns_chat(conn, platform, chat_id)

    def mark_sent(self, reminder_id: int) -> bool:
        with self.db.tx() as conn:
            return repo.mark_reminder_sent(conn, reminder_id)

    def mark_failed(self, reminder_id: int, error: str, permanent: bool = False) -> str | None:
        with self.db.tx() as conn:
            return repo.mark_reminder_failed(conn, reminder_id, error or "unknown error",
                                             0 if permanent else self.max_attempts)
