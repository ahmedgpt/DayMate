import asyncio
from datetime import timedelta

import httpx
import pytest

from app.messaging.telegram_sender import DeliveryError, TelegramSender
from app.services.delivery_service import DeliveryService
from app.utils.datetime_utils import utcnow
from tests.unit.test_services import FUTURE


class FakeSender:
    def __init__(self, fail=None, delay=0.0):
        self.sent, self.fail, self.delay = [], fail, delay

    async def send(self, chat_id, text):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise DeliveryError(self.fail)
        self.sent.append((chat_id, text))


def _due(svc, user):
    r = svc.reminders.create(user["id"], "Submit assignment", FUTURE)
    svc.reminders.claim_due(now=utcnow() + timedelta(days=2))
    # claim_due used a future 'now' for selection; re-lease relative to the real clock so the lease is active
    with svc.db.tx() as c:
        c.execute("UPDATE reminders SET lease_until_utc = ? WHERE id = ?",
                  ((utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), r["id"]))
    return r["id"]


def run(coro):
    return asyncio.run(coro)


def test_success_sends_to_chat_then_marks_sent(svc, user):
    rid, sender = _due(svc, user), FakeSender()
    res = run(DeliveryService(svc.reminders, sender).deliver(rid))
    assert (res.http_status, res.status) == (200, "sent")
    assert sender.sent[0][0] == "111" and "Submit assignment" in sender.sent[0][1]
    assert svc.reminders.list(user["id"], "sent")[0]["id"] == rid


def test_failure_does_not_mark_sent_and_stays_retryable(svc, user):
    rid = _due(svc, user)
    res = run(DeliveryService(svc.reminders, FakeSender(fail="could not connect")).deliver(rid))
    assert (res.http_status, res.status) == (502, "pending")
    row = svc.reminders.get_for_delivery(rid)
    assert row["status"] == "pending" and row["last_error"] == "could not connect" and row["lease_until_utc"] is None
    assert svc.reminders.list(user["id"], "sent") == []


def test_gives_up_after_max_attempts(svc, user):  # max_attempts=3 in fixture
    r = svc.reminders.create(user["id"], "x", FUTURE)
    d = DeliveryService(svc.reminders, FakeSender(fail="down"))
    statuses = []
    for _ in range(3):
        svc.reminders.claim_due(now=utcnow() + timedelta(days=2))
        with svc.db.tx() as c:
            c.execute("UPDATE reminders SET lease_until_utc = ? WHERE id = ?",
                      ((utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), r["id"]))
        statuses.append(run(d.deliver(r["id"])).status)
    assert statuses == ["pending", "pending", "failed"]


def test_duplicate_concurrent_deliver_sends_once(svc, user):
    rid, sender = _due(svc, user), FakeSender(delay=0.05)
    d = DeliveryService(svc.reminders, sender)

    async def go():
        return await asyncio.gather(d.deliver(rid), d.deliver(rid), d.deliver(rid))

    results = run(go())
    assert len(sender.sent) == 1
    assert sorted(r.status for r in results) == ["sent", "skipped", "skipped"]


def test_retry_after_sent_is_skipped(svc, user):
    rid, sender = _due(svc, user), FakeSender()
    d = DeliveryService(svc.reminders, sender)
    run(d.deliver(rid))
    again = run(d.deliver(rid))
    assert again.status == "skipped" and len(sender.sent) == 1


def test_unclaimed_cancelled_unknown_and_unconfigured(svc, user):
    sender = FakeSender()
    d = DeliveryService(svc.reminders, sender)
    r = svc.reminders.create(user["id"], "x", FUTURE)
    assert run(d.deliver(r["id"])).http_status == 409          # never claimed
    svc.reminders.cancel(user["id"], r["id"])
    assert run(d.deliver(r["id"])).status == "skipped"          # cancelled
    assert run(d.deliver(999)).http_status == 404
    assert run(DeliveryService(svc.reminders, None).deliver(r["id"])).http_status == 503
    assert sender.sent == []


def test_expired_lease_is_not_delivered(svc, user):
    r = svc.reminders.create(user["id"], "x", FUTURE)
    svc.reminders.claim_due(now=utcnow() + timedelta(days=2))
    with svc.db.tx() as c:
        c.execute("UPDATE reminders SET lease_until_utc = '2020-01-01T00:00:00Z' WHERE id = ?", (r["id"],))
    sender = FakeSender()
    assert run(DeliveryService(svc.reminders, sender).deliver(r["id"])).http_status == 409 and sender.sent == []


def test_non_telegram_user_is_failed_not_sent(svc):
    u = svc.users.get_or_create("cli", "local", "local")
    svc.users.set_timezone(u["id"], "Asia/Karachi")
    r = svc.reminders.create(u["id"], "x", FUTURE)
    svc.reminders.claim_due(now=utcnow() + timedelta(days=2))
    with svc.db.tx() as c:
        c.execute("UPDATE reminders SET lease_until_utc = ? WHERE id = ?",
                  ((utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), r["id"]))
    sender = FakeSender()
    assert run(DeliveryService(svc.reminders, sender).deliver(r["id"])).status == "failed" and sender.sent == []


def test_send_to_known_chat_only(svc, user):
    sender = FakeSender()
    d = DeliveryService(svc.reminders, sender)
    assert run(d.send_to_known_chat("111", "hello")).status == "sent"
    denied = run(d.send_to_known_chat("424242", "spam"))
    assert denied.http_status == 403 and sender.sent == [("111", "hello")]


# ---- TelegramSender against a mocked Telegram API
def _sender(handler):
    return TelegramSender("TOKEN123", transport=httpx.MockTransport(handler))


def test_telegram_sender_success_and_payload():
    seen = {}

    def h(req):
        seen["url"], seen["body"] = str(req.url), req.content
        return httpx.Response(200, json={"ok": True, "result": {}})
    run(_sender(h).send("111", "hi"))
    assert seen["url"].endswith("/botTOKEN123/sendMessage") and b'"chat_id":"111"' in seen["body"].replace(b" ", b"")


def test_telegram_sender_rejection_and_errors_never_leak_token():
    with pytest.raises(DeliveryError) as e:
        run(_sender(lambda r: httpx.Response(400, json={"ok": False, "description": "chat not found"})).send("1", "x"))
    assert "chat not found" in str(e.value) and "TOKEN123" not in str(e.value)

    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)
    with pytest.raises(DeliveryError) as e:
        run(_sender(timeout).send("1", "x"))
    assert "TOKEN123" not in str(e.value)


def test_telegram_sender_retries_only_connection_failures():
    calls = []

    def flaky(req):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, json={"ok": True})
    run(_sender(flaky).send("1", "x"))
    assert len(calls) == 3

    calls.clear()

    def always_down(req):
        calls.append(1)
        raise httpx.ConnectError("refused", request=req)
    with pytest.raises(DeliveryError):
        run(_sender(always_down).send("1", "x"))
    assert len(calls) == 3
