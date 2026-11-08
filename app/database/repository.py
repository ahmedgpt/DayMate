"""All SQL lives here. Every user-owned query filters on user_id."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from app.utils.datetime_utils import to_iso_utc, utcnow


def _now() -> str:
    return to_iso_utc(utcnow())


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------- users
def get_or_create_user(conn, platform: str, platform_user_id: str, chat_id: str | None) -> dict:
    now = _now()
    conn.execute(
        "INSERT INTO users (platform, platform_user_id, chat_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (platform, platform_user_id) DO NOTHING",
        (platform, platform_user_id, chat_id, now, now),
    )
    if chat_id:
        conn.execute(
            "UPDATE users SET chat_id = ?, updated_at = ? WHERE platform = ? AND platform_user_id = ? AND chat_id IS NOT ?",
            (chat_id, now, platform, platform_user_id, chat_id),
        )
    return row_dict(conn.execute(
        "SELECT * FROM users WHERE platform = ? AND platform_user_id = ?", (platform, platform_user_id)).fetchone())


def get_user(conn, user_id: int) -> dict | None:
    return row_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def set_user_timezone(conn, user_id: int, tz: str) -> None:
    conn.execute("UPDATE users SET timezone = ?, updated_at = ? WHERE id = ?", (tz, _now(), user_id))


def list_users(conn, platform: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM users WHERE chat_id IS NOT NULL", []
    if platform:
        sql += " AND platform = ?"
        args.append(platform)
    return [dict(r) for r in conn.execute(sql, args)]


# ---------------------------------------------------------------- diary
def insert_diary(conn, user_id: int, entry_date: str, description: str,
                 category: str | None, duration_minutes: int | None) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT INTO diary_entries (user_id, entry_date, description, category, duration_minutes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, entry_date, description, category, duration_minutes, now, now),
    )
    return get_diary(conn, user_id, cur.lastrowid)


def get_diary(conn, user_id: int, entry_id: int) -> dict | None:
    return row_dict(conn.execute(
        "SELECT * FROM diary_entries WHERE id = ? AND user_id = ?", (entry_id, user_id)).fetchone())


def list_diary(conn, user_id: int, start: str, end: str, search: str | None = None) -> list[dict]:
    sql = "SELECT * FROM diary_entries WHERE user_id = ? AND entry_date BETWEEN ? AND ?"
    args: list = [user_id, start, end]
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " AND description LIKE ? ESCAPE '\\'"
        args.append(f"%{escaped}%")
    sql += " ORDER BY entry_date, created_at, id"
    return [dict(r) for r in conn.execute(sql, args)]


def update_diary(conn, user_id: int, entry_id: int, fields: dict) -> dict | None:
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)  # keys come from a fixed server-side whitelist
        conn.execute(f"UPDATE diary_entries SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",
                     (*fields.values(), _now(), entry_id, user_id))
    return get_diary(conn, user_id, entry_id)


# ---------------------------------------------------------------- reminders
def insert_reminder(conn, user_id: int, message: str, scheduled_at_utc: str, tz: str) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT INTO reminders (user_id, message, scheduled_at_utc, timezone, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, message, scheduled_at_utc, tz, now, now),
    )
    return get_reminder(conn, user_id, cur.lastrowid)


def get_reminder(conn, user_id: int, reminder_id: int) -> dict | None:
    return row_dict(conn.execute(
        "SELECT * FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)).fetchone())


def list_reminders(conn, user_id: int, status: str | None, from_utc: str | None,
                   to_utc: str | None, limit: int = 50) -> list[dict]:
    sql, args = "SELECT * FROM reminders WHERE user_id = ?", [user_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    if from_utc:
        sql += " AND scheduled_at_utc >= ?"
        args.append(from_utc)
    if to_utc:
        sql += " AND scheduled_at_utc <= ?"
        args.append(to_utc)
    sql += " ORDER BY scheduled_at_utc, id LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def cancel_reminder(conn, user_id: int, reminder_id: int) -> bool:
    cur = conn.execute(
        "UPDATE reminders SET status = 'cancelled', lease_until_utc = NULL, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND status = 'pending'", (_now(), reminder_id, user_id))
    return cur.rowcount == 1


def claim_due_reminders(conn, lease_seconds: int, limit: int, now: datetime | None = None) -> list[dict]:
    """Atomically lease due pending reminders so concurrent/retried runs cannot pick the same one."""
    now = now or utcnow()
    now_s, lease_s = to_iso_utc(now), to_iso_utc(now + timedelta(seconds=lease_seconds))
    ids = [r["id"] for r in conn.execute(
        "SELECT r.id FROM reminders r WHERE r.status = 'pending' AND r.scheduled_at_utc <= ? "
        "AND (r.lease_until_utc IS NULL OR r.lease_until_utc <= ?) ORDER BY r.scheduled_at_utc, r.id LIMIT ?",
        (now_s, now_s, limit))]
    claimed = []
    for rid in ids:
        conn.execute(
            "UPDATE reminders SET lease_until_utc = ?, last_attempt_at_utc = ?, attempt_count = attempt_count + 1, "
            "updated_at = ? WHERE id = ?", (lease_s, now_s, now_s, rid))
        claimed.append(dict(conn.execute(
            "SELECT r.*, u.chat_id, u.platform FROM reminders r JOIN users u ON u.id = r.user_id WHERE r.id = ?",
            (rid,)).fetchone()))
    return claimed


def get_reminder_for_delivery(conn, reminder_id: int) -> dict | None:
    return row_dict(conn.execute(
        "SELECT r.*, u.chat_id, u.platform FROM reminders r JOIN users u ON u.id = r.user_id WHERE r.id = ?",
        (reminder_id,)).fetchone())


def user_owns_chat(conn, platform: str, chat_id: str) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE platform = ? AND chat_id = ?", (platform, chat_id)).fetchone() is not None


def mark_reminder_sent(conn, reminder_id: int) -> bool:
    now = _now()
    cur = conn.execute(
        "UPDATE reminders SET status = 'sent', sent_at_utc = ?, lease_until_utc = NULL, last_error = NULL, updated_at = ? "
        "WHERE id = ? AND status = 'pending'", (now, now, reminder_id))
    return cur.rowcount == 1


def mark_reminder_failed(conn, reminder_id: int, error: str, max_attempts: int) -> str | None:
    """Release the lease for retry; give up (status=failed) after max_attempts. Returns new status."""
    row = conn.execute("SELECT attempt_count FROM reminders WHERE id = ? AND status = 'pending'", (reminder_id,)).fetchone()
    if row is None:
        return None
    status = "failed" if row["attempt_count"] >= max_attempts else "pending"
    conn.execute(
        "UPDATE reminders SET status = ?, lease_until_utc = NULL, last_error = ?, updated_at = ? WHERE id = ?",
        (status, error[:500], _now(), reminder_id))
    return status
