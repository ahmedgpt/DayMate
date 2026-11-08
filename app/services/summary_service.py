"""Builds grounded summary inputs. Counts/totals are computed here; the LLM only writes prose."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from app.database import repository as repo
from app.database.connection import Database
from app.errors import ValidationError
from app.services.diary_service import DiaryService
from app.services.user_service import UserService
from app.utils.datetime_utils import get_zone, local_today, parse_date, utcnow


def _public(e: dict) -> dict:
    return {k: e[k] for k in ("id", "entry_date", "description", "category", "duration_minutes")}


def _stats(entries: list[dict]) -> dict:
    timed = [e["duration_minutes"] for e in entries if e["duration_minutes"]]
    return {
        "entry_count": len(entries),
        "entries_with_recorded_duration": len(timed),
        "total_recorded_minutes": sum(timed),
        "categories": dict(Counter(e["category"] for e in entries if e["category"])),
    }


class SummaryService:
    def __init__(self, db: Database, users: UserService, diary: DiaryService):
        self.db, self.users, self.diary = db, users, diary

    def daily(self, user_id: int, date: str | None = None) -> dict:
        day = parse_date(date, "date") if date else local_today(self.users.get(user_id)["timezone"])
        entries = self.diary.get_entries(user_id, day.isoformat(), day.isoformat())
        return {"date": day.isoformat(), **_stats(entries), "entries": [_public(e) for e in entries],
                "note": "Only entries and totals listed here are recorded facts; do not add anything else."}

    def weekly(self, user_id: int, start_date: str | None = None, end_date: str | None = None) -> dict:
        """Rolling seven-day window ending today unless both dates are given."""
        if bool(start_date) != bool(end_date):
            raise ValidationError("Provide both start_date and end_date, or neither for the last 7 days.")
        if start_date:
            start, end = parse_date(start_date, "start_date"), parse_date(end_date, "end_date")
        else:
            end = local_today(self.users.get(user_id)["timezone"])
            start = end - timedelta(days=6)
        entries = self.diary.get_entries(user_id, start.isoformat(), end.isoformat())
        by_day: dict[str, list] = defaultdict(list)
        for e in entries:
            by_day[e["entry_date"]].append(_public(e))
        return {"start_date": start.isoformat(), "end_date": end.isoformat(), **_stats(entries),
                "days_with_entries": len(by_day), "entries_by_day": dict(by_day),
                "note": "Counts and totals are computed from stored data; do not invent others."}

    def weekly_digests_due(self, weekday: int, local_hour: int, now: datetime | None = None,
                           platform: str = "telegram") -> list[dict]:
        """Users whose local clock is at `weekday` (0=Mon..6=Sun) `local_hour`:00 and who logged something in
        the rolling 7 days ending today. Uses the same `weekly` logic as the get_weekly_summary tool."""
        if not 0 <= weekday <= 6 or not 0 <= local_hour <= 23:
            raise ValidationError("weekday must be 0-6 (Mon=0) and hour 0-23.")
        now = now or utcnow()
        with self.db.tx() as conn:
            users = repo.list_users(conn, platform)  # only users we can actually message
        out = []
        for u in users:
            if not u["timezone"]:
                continue
            local = now.astimezone(get_zone(u["timezone"]))
            if local.weekday() != weekday or local.hour != local_hour:
                continue
            end = local.date()
            summary = self.weekly(u["id"], (end - timedelta(days=6)).isoformat(), end.isoformat())
            if summary["entry_count"]:
                out.append({"chat_id": u["chat_id"], "platform": u["platform"], "timezone": u["timezone"], **summary})
        return out

    def digests_due(self, local_hour: int, now: datetime | None = None, platform: str = "telegram") -> list[dict]:
        """Users whose local clock is at `local_hour` and who logged something today (for n8n)."""
        if not 0 <= local_hour <= 23:
            raise ValidationError("local_hour must be 0-23.")
        now = now or utcnow()
        with self.db.tx() as conn:
            users = repo.list_users(conn, platform)  # only users we can actually message
        out = []
        for u in users:
            if not u["timezone"] or now.astimezone(get_zone(u["timezone"])).hour != local_hour:
                continue
            summary = self.daily(u["id"], local_today(u["timezone"], now).isoformat())
            if summary["entry_count"]:
                out.append({"chat_id": u["chat_id"], "platform": u["platform"], "timezone": u["timezone"], **summary})
        return out
