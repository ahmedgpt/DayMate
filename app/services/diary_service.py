from __future__ import annotations

from app.database import repository as repo
from app.database.connection import Database
from app.errors import NotFoundError, ValidationError
from app.services.user_service import UserService
from app.utils.datetime_utils import local_today, parse_date

MAX_DESCRIPTION = 2000
MAX_RANGE_DAYS = 400


def _clean_text(value: str | None, field: str, required: bool = True) -> str | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise ValidationError(f"{field} must not be empty.")
        return None
    if len(value) > MAX_DESCRIPTION:
        raise ValidationError(f"{field} is too long (max {MAX_DESCRIPTION} characters).")
    return value


def _check_duration(minutes: int | None) -> int | None:
    if minutes is None:
        return None
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 < minutes <= 24 * 60:
        raise ValidationError("duration_minutes must be an integer between 1 and 1440.")
    return minutes


class DiaryService:
    def __init__(self, db: Database, users: UserService):
        self.db, self.users = db, users

    def add_entry(self, user_id: int, description: str, entry_date: str | None = None,
                  category: str | None = None, duration_minutes: int | None = None) -> dict:
        description = _clean_text(description, "description")
        category = _clean_text(category, "category", required=False)
        duration_minutes = _check_duration(duration_minutes)
        if entry_date:
            day = parse_date(entry_date, "entry_date").isoformat()
        else:
            day = local_today(self.users.get(user_id)["timezone"]).isoformat()
        with self.db.tx() as conn:
            return repo.insert_diary(conn, user_id, day, description, category, duration_minutes)

    def get_entries(self, user_id: int, start_date: str | None = None, end_date: str | None = None,
                    search: str | None = None) -> list[dict]:
        if not start_date and not end_date:
            start_date = end_date = local_today(self.users.get(user_id)["timezone"]).isoformat()
        start = parse_date(start_date or end_date, "start_date")
        end = parse_date(end_date or start_date, "end_date")
        if end < start:
            raise ValidationError("end_date must not be before start_date.")
        if (end - start).days > MAX_RANGE_DAYS:
            raise ValidationError(f"Date range too large (max {MAX_RANGE_DAYS} days).")
        with self.db.tx() as conn:
            return repo.list_diary(conn, user_id, start.isoformat(), end.isoformat(), (search or "").strip() or None)

    def update_entry(self, user_id: int, entry_id: int, description: str | None = None,
                     category: str | None = None, duration_minutes: int | None = None) -> dict:
        fields: dict = {}
        if description is not None:
            fields["description"] = _clean_text(description, "description")
        if category is not None:
            fields["category"] = _clean_text(category, "category", required=False)
        if duration_minutes is not None:
            fields["duration_minutes"] = _check_duration(duration_minutes)
        if not fields:
            raise ValidationError("Nothing to update: provide description, category or duration_minutes.")
        with self.db.tx() as conn:
            if not repo.get_diary(conn, user_id, entry_id):
                raise NotFoundError(f"No diary entry with id {entry_id}.")
            return repo.update_diary(conn, user_id, entry_id, fields)
