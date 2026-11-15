"""Reply speed (16 Nov): 'typing...' at once, one kept-open connection to WhatsApp, and every reply's time split
into database / WhatsApp / other, logged and summed up in the daily health report."""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from conftest import OWNER, FakeReader, FakeWA, onboard, photo, text

from bot import health
from bot.conversation import Bot
from bot.whatsapp import WhatsAppClient

IST = timezone(timedelta(hours=5, minutes=30))


class TypingWA(FakeWA):
    def __init__(self, delay=0.0):
        super().__init__()
        self.delay = delay

    async def typing(self, message_id):
        await asyncio.sleep(self.delay)
        self.sent.append({"kind": "typing", "id": message_id})


def make(store, tmp_path, wa=None, timing=True):
    wa = wa or TypingWA()
    bot = Bot(store, wa, FakeReader(), pilot_open=True, image_dir=tmp_path / "bills", timing_events=timing)

    class W:
        pass

    w = W()
    w.store, w.wa, w.bot, w.reader = store, wa, bot, bot.extractor
    w.run = lambda m: asyncio.run(bot.handle(m))
    return w


def _events(store, name):
    rows = store.events if hasattr(store, "events") else store._all("select event, props from events order by id")
    return [e["props"] for e in rows if e["event"] == name]


def test_typing_goes_out_before_a_photo_reply(store, tmp_path):
    w = make(store, tmp_path, TypingWA(delay=0.05))     # even a slow typing call lands before the reply
    onboard(w)
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 5000, "bill_date": "2026-11-15"}
    msg = photo()
    w.run(msg)
    i = next(k for k, m in enumerate(w.wa.sent) if m["kind"] == "typing" and m["id"] == msg["id"])
    assert w.wa.sent[i + 1]["kind"] in ("buttons", "list", "text")   # the typing call lands before the reply


def test_text_replies_skip_the_typing_call(store, tmp_path):
    w = make(store, tmp_path)                            # measured: it only made fast replies later
    onboard(w)
    w.run(text("Sharma Traders ka bill 5000"))
    assert not any(m["kind"] == "typing" for m in w.wa.sent)


def test_a_hanging_typing_call_never_holds_the_reply(store, tmp_path):
    w = make(store, tmp_path, TypingWA(delay=30))
    onboard(w)
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 5000, "bill_date": "2026-11-15"}
    w.run(photo())                                       # the reply waits at most 2 s for the courtesy call
    assert w.wa.sent[-1]["kind"] != "typing"


def test_every_reply_is_timed_and_split(store, tmp_path, caplog):
    w = make(store, tmp_path)
    onboard(w)
    with caplog.at_level(logging.INFO, logger="hisaabai"):
        w.run(text("Sharma Traders ka bill 5000"))
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("handled text in"))
    assert "database" in line and "whatsapp 1 sends" in line and "other" in line
    t = _events(store, "reply_timed")[-1]
    assert {"ms", "type", "db_q", "db_ms", "wa_n", "wa_ms", "other_ms"} <= set(t) and t["wa_n"] == 1
    if not hasattr(store, "events"):                     # Postgres: the queries were counted
        assert t["db_q"] >= 5


def test_tests_and_evals_do_not_log_timing_events(store, tmp_path):
    w = make(store, tmp_path, timing=False)
    onboard(w)
    assert _events(store, "reply_timed") == []


def test_whatsapp_keeps_one_connection_per_event_loop(monkeypatch):
    made = []

    class Counting(httpx.AsyncClient):
        def __init__(self, *a, **k):
            made.append(k.get("limits"))
            super().__init__(*a, transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"messages": [{"id": "wamid.x"}], "success": True})), **k)

    monkeypatch.setattr(httpx, "AsyncClient", Counting)
    wa = WhatsAppClient("token", "123")

    async def three_replies():
        await wa.typing("wamid.in1")
        await wa.send_text(OWNER, "a")
        await wa.send_buttons(OWNER, "b", [("x", "y")])

    asyncio.run(three_replies())
    assert len(made) == 1                                # one connection for the typing call and both replies
    assert made[0].keepalive_expiry >= 60                # kept open between messages, not closed after 5 s
    asyncio.run(three_replies())                         # a new event loop (the daily job): a fresh client
    assert len(made) == 2


def test_health_report_shows_the_median_reply_time(tmp_path):
    now = datetime(2026, 11, 16, 12, 0, tzinfo=IST)
    ev = [{"shop_id": None, "event": "reply_timed", "created_at": now,
           "props": {"ms": ms, "type": kind, "db_ms": 400, "wa_ms": 700, "other_ms": ms - 1100}}
          for ms, kind in ((1500, "text"), (1700, "text"), (5200, "image"))]
    report = health.build([], ev, date(2026, 11, 16))
    assert "Reply time: median 1.7 s over 3 replies (text 1.6 s, image 5.2 s)" in report
    assert "database 0.4 s, WhatsApp 0.7 s" in report
