from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.errors import NotFoundError, ValidationError
from app.services.weather_service import WeatherError, WeatherService
from app.utils.datetime_utils import format_local, parse_local_datetime, utcnow


# ------------------------------------------------------------ datetime utils
def test_naive_datetime_uses_user_timezone():
    assert parse_local_datetime("2026-11-02T10:00:00", "Asia/Karachi") == datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)


def test_explicit_offset_is_honoured():
    assert parse_local_datetime("2026-11-02T10:00:00+00:00", "Asia/Karachi").hour == 10


def test_dst_gap_rejected():
    with pytest.raises(ValidationError):
        parse_local_datetime("2026-03-08T02:30:00", "America/New_York")


def test_missing_and_invalid_timezone():
    with pytest.raises(ValidationError) as e:
        parse_local_datetime("2026-11-02T10:00:00", None)
    assert e.value.code == "timezone_not_set"
    with pytest.raises(ValidationError) as e:
        parse_local_datetime("2026-11-02T10:00:00", "Mars/Base")
    assert e.value.code == "invalid_timezone"


def test_format_local():
    assert format_local("2026-11-02T05:00:00Z", "Asia/Karachi") == "Mon 2026-11-02 10:00 (Asia/Karachi)"


# ------------------------------------------------------------ diary
def test_diary_add_get_update(svc, user):
    e = svc.diary.add_entry(user["id"], "  Worked on Python  ", entry_date="2026-11-01", duration_minutes=180)
    assert e["description"] == "Worked on Python" and e["entry_date"] == "2026-11-01"
    assert [x["id"] for x in svc.diary.get_entries(user["id"], "2026-11-01", "2026-11-01")] == [e["id"]]
    upd = svc.diary.update_entry(user["id"], e["id"], description="Worked on DayMate")
    assert upd["description"] == "Worked on DayMate" and upd["duration_minutes"] == 180


def test_diary_defaults_to_user_local_date(svc, user):
    e = svc.diary.add_entry(user["id"], "x")
    today = (utcnow() + timedelta(hours=5)).date().isoformat()
    assert e["entry_date"] == today


def test_diary_validation(svc, user):
    with pytest.raises(ValidationError):
        svc.diary.add_entry(user["id"], "   ")
    with pytest.raises(ValidationError):
        svc.diary.add_entry(user["id"], "x", entry_date="yesterday")
    with pytest.raises(ValidationError):
        svc.diary.get_entries(user["id"], "2026-11-02", "2026-11-01")
    with pytest.raises(ValidationError):
        svc.diary.update_entry(user["id"], 1)


def test_diary_search_escapes_wildcards(svc, user):
    svc.diary.add_entry(user["id"], "100% done", entry_date="2026-11-01")
    svc.diary.add_entry(user["id"], "nothing", entry_date="2026-11-01")
    assert len(svc.diary.get_entries(user["id"], "2026-11-01", "2026-11-01", search="%")) == 1


def test_user_isolation_diary(svc, user, other_user):
    e = svc.diary.add_entry(user["id"], "private", entry_date="2026-11-01")
    assert svc.diary.get_entries(other_user["id"], "2026-11-01", "2026-11-01") == []
    with pytest.raises(NotFoundError):
        svc.diary.update_entry(other_user["id"], e["id"], description="hacked")
    assert svc.diary.get_entries(user["id"], "2026-11-01", "2026-11-01")[0]["description"] == "private"


# ------------------------------------------------------------ reminders
FUTURE = (utcnow() + timedelta(days=1)).astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%dT%H:%M:00")


def test_reminder_create_list_cancel(svc, user):
    r = svc.reminders.create(user["id"], "Submit assignment", FUTURE)
    assert r["status"] == "pending" and "Asia/Karachi" in r["scheduled_local"]
    assert [x["id"] for x in svc.reminders.list(user["id"])] == [r["id"]]
    assert svc.reminders.cancel(user["id"], r["id"])["status"] == "cancelled"
    assert svc.reminders.list(user["id"]) == []
    assert svc.reminders.cancel(user["id"], r["id"])["status"] == "cancelled"  # idempotent


def test_reminder_past_and_no_timezone(svc, user):
    with pytest.raises(ValidationError) as e:
        svc.reminders.create(user["id"], "x", "2020-01-01T10:00:00")
    assert e.value.code == "time_in_past"
    fresh = svc.users.get_or_create("telegram", "333", "333")
    with pytest.raises(ValidationError) as e:
        svc.reminders.create(fresh["id"], "x", FUTURE)
    assert e.value.code == "timezone_not_set"


def test_reminder_isolation(svc, user, other_user):
    r = svc.reminders.create(user["id"], "mine", FUTURE)
    with pytest.raises(NotFoundError):
        svc.reminders.cancel(other_user["id"], r["id"])
    assert svc.reminders.list(other_user["id"]) == []


def test_claim_due_only_returns_due_pending_once(svc, user):
    due = svc.reminders.create(user["id"], "due", FUTURE)
    later = svc.reminders.create(user["id"], "cancelled", FUTURE)
    svc.reminders.cancel(user["id"], later["id"])
    now = utcnow() + timedelta(days=2)
    first = svc.reminders.claim_due(now=now)
    assert [r["id"] for r in first] == [due["id"]] and first[0]["chat_id"] == "111"
    assert svc.reminders.claim_due(now=now) == []  # leased: overlapping run gets nothing
    assert [r["id"] for r in svc.reminders.claim_due(now=now + timedelta(seconds=301))] == [due["id"]]  # lease expired


def test_future_reminder_not_claimed(svc, user):
    svc.reminders.create(user["id"], "future", FUTURE)
    assert svc.reminders.claim_due() == []


def test_sent_only_when_marked_and_not_resent(svc, user):
    r = svc.reminders.create(user["id"], "x", FUTURE)
    now = utcnow() + timedelta(days=2)
    svc.reminders.claim_due(now=now)
    assert svc.reminders.mark_sent(r["id"]) is True
    assert svc.reminders.mark_sent(r["id"]) is False
    assert svc.reminders.claim_due(now=now + timedelta(hours=1)) == []
    assert svc.reminders.list(user["id"], status="sent")[0]["id"] == r["id"]


def test_failed_delivery_stays_retryable_then_gives_up(svc, user):
    r = svc.reminders.create(user["id"], "x", FUTURE)
    now = utcnow() + timedelta(days=2)
    for attempt in (1, 2):
        assert len(svc.reminders.claim_due(now=now)) == 1
        assert svc.reminders.mark_failed(r["id"], "telegram down") == "pending"
    assert len(svc.reminders.claim_due(now=now)) == 1  # 3rd attempt
    assert svc.reminders.mark_failed(r["id"], "telegram down") == "failed"
    assert svc.reminders.claim_due(now=now) == []


def test_cancelled_while_leased_is_not_marked_sent(svc, user):
    r = svc.reminders.create(user["id"], "x", FUTURE)
    svc.reminders.claim_due(now=utcnow() + timedelta(days=2))
    svc.reminders.cancel(user["id"], r["id"])
    assert svc.reminders.mark_sent(r["id"]) is False


# ------------------------------------------------------------ summaries
def test_daily_and_weekly_summary_counts_from_data(svc, user):
    svc.diary.add_entry(user["id"], "coding", entry_date="2026-11-01", category="study", duration_minutes=120)
    svc.diary.add_entry(user["id"], "meeting", entry_date="2026-11-01", duration_minutes=30)
    svc.diary.add_entry(user["id"], "walk", entry_date="2026-11-02")
    d = svc.summaries.daily(user["id"], "2026-11-01")
    assert d["entry_count"] == 2 and d["total_recorded_minutes"] == 150 and d["categories"] == {"study": 1}
    w = svc.summaries.weekly(user["id"], "2026-10-26", "2026-11-01")
    assert w["entry_count"] == 2 and w["days_with_entries"] == 1
    assert svc.summaries.daily(user["id"], "2026-10-01")["entry_count"] == 0
    with pytest.raises(ValidationError):
        svc.summaries.weekly(user["id"], "2026-10-26", None)


def test_digests_due_matches_local_hour_and_skips_empty(svc, user, other_user):
    now = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)  # 23:30 in Karachi
    assert svc.summaries.digests_due(23, now) == []  # nothing logged yet
    svc.diary.add_entry(user["id"], "coding", entry_date="2026-11-02")
    out = svc.summaries.digests_due(23, now)
    assert [d["chat_id"] for d in out] == ["111"] and out[0]["entry_count"] == 1
    assert svc.summaries.digests_due(22, now) == []


def test_digests_skip_users_without_a_messaging_channel(svc):
    cli = svc.users.get_or_create("cli", "local", "local")
    svc.users.set_timezone(cli["id"], "Asia/Karachi")
    svc.diary.add_entry(cli["id"], "terminal entry", entry_date="2026-11-02")
    now = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)
    assert svc.summaries.digests_due(23, now) == []
    assert svc.summaries.weekly_digests_due(0, 23, now) == []


def test_weekly_digests_due_weekday_hour_and_window(svc, user, other_user):
    sunday = datetime(2026, 11, 1, 15, 30, tzinfo=timezone.utc)  # Sun 20:30 Karachi, Sun 15:30 London
    assert svc.summaries.weekly_digests_due(6, 20, sunday) == []  # nothing logged
    svc.diary.add_entry(user["id"], "in window", entry_date="2026-10-26")
    svc.diary.add_entry(user["id"], "too old", entry_date="2026-10-25")
    svc.diary.add_entry(other_user["id"], "london", entry_date="2026-10-30")
    out = svc.summaries.weekly_digests_due(6, 20, sunday)
    assert [d["chat_id"] for d in out] == ["111"]
    assert out[0]["start_date"] == "2026-10-26" and out[0]["end_date"] == "2026-11-01" and out[0]["entry_count"] == 1
    assert svc.summaries.weekly_digests_due(5, 20, sunday) == []
    assert [d["chat_id"] for d in svc.summaries.weekly_digests_due(6, 15, sunday)] == ["222"]
    with pytest.raises(ValidationError):
        svc.summaries.weekly_digests_due(7, 20, sunday)


# ------------------------------------------------------------ weather
def _weather(handler):
    return WeatherService(transport=httpx.MockTransport(handler))


def test_weather_current_uses_real_api_payload():
    def handler(req: httpx.Request):
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [{"name": "Lahore", "country": "Pakistan", "latitude": 31.5,
                                                          "longitude": 74.3, "timezone": "Asia/Karachi"}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 24.1, "weather_code": 1, "wind_speed_10m": 5.0},
                                         "daily": {"weather_code": [2], "temperature_2m_max": [28.0],
                                                   "temperature_2m_min": [17.0], "precipitation_sum": [0.0]}})
    r = _weather(handler).get_weather("Lahore", today=datetime(2026, 11, 2).date())
    assert r["kind"] == "current_and_today" and r["current"]["temperature_c"] == 24.1
    assert r["day"]["max_temp_c"] == 28.0


def test_weather_past_date_uses_archive_endpoint():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [{"name": "X", "latitude": 1, "longitude": 2}]})
        return httpx.Response(200, json={"daily": {"weather_code": [61], "temperature_2m_max": [20],
                                                   "temperature_2m_min": [10], "precipitation_sum": [3.2]}})
    r = _weather(handler).get_weather("X", "2026-10-30", today=datetime(2026, 11, 2).date())
    assert r["kind"] == "historical" and "archive-api.open-meteo.com" in seen


def test_weather_failures_are_honest():
    def boom(req):
        raise httpx.ConnectTimeout("t")
    with pytest.raises(WeatherError):
        _weather(boom).get_weather("Lahore")
    with pytest.raises(ValidationError) as e:
        _weather(lambda r: httpx.Response(200, json={})).get_weather("Nowhere")
    assert e.value.code == "location_not_found"
    with pytest.raises(WeatherError):
        _weather(lambda r: httpx.Response(500)).get_weather("Lahore")
