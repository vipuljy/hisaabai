"""Render readiness: Meta gets 200 at once, the work runs after; retried deliveries are processed once;
/health refuses to go live when bills or photos would land on Render's wiped disk."""

import asyncio
import hashlib
import hmac
import json

import pytest
from conftest import OWNER, _events, onboard
from fastapi.testclient import TestClient

from bot.app import create_app

SECRET = "test-secret"


def signed(payload):
    raw = json.dumps(payload).encode()
    return raw, {"X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
                 "Content-Type": "application/json"}


def delivery(*messages):
    return {"entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}


PHOTO = {"id": "wamid.retry1", "from": OWNER, "type": "image", "image": {"id": "media-retry"}}


def test_meta_gets_200_before_the_work_starts():
    order = []

    class SlowBot:
        async def handle(self, msg):
            order.append("work started")
            await asyncio.sleep(0.2)
            order.append("work done")

    app = create_app(bot=SlowBot(), verify_token="v", app_secret=SECRET)
    raw, headers = signed(delivery(PHOTO))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/webhook", "raw_path": b"/webhook", "query_string": b"", "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    sent = iter([{"type": "http.request", "body": raw, "more_body": False}])

    async def receive():
        return next(sent, {"type": "http.disconnect"})

    async def send(message):
        if message["type"] == "http.response.start":
            order.append(f"HTTP {message['status']}")
        elif message["type"] == "http.response.body" and not message.get("more_body"):
            order.append("response sent")

    asyncio.run(app(scope, receive, send))
    assert order == ["HTTP 200", "response sent", "work started", "work done"]


def test_a_crash_while_handling_still_answers_200():
    class Broken:
        async def handle(self, msg):
            raise RuntimeError("boom")

    c = TestClient(create_app(bot=Broken(), verify_token="v", app_secret=SECRET))
    raw, headers = signed(delivery(PHOTO))
    assert c.post("/webhook", content=raw, headers=headers).status_code == 200


def test_meta_retries_are_processed_once(world):
    """Meta re-sends a message it got no quick 200 for (e.g. during a cold start): same message id, one bill, one reply."""
    onboard(world)
    world.reader.next = {"supplier_name": "Shiv Medical Distributors", "total_amount": 4769}
    calls = []
    real = world.reader

    def counting(image):
        calls.append(1)
        return real(image)
    world.bot.extractor = counting
    c = TestClient(create_app(bot=world.bot, verify_token="v", app_secret=SECRET))
    sent_before = len(world.wa.sent)
    for payload in (delivery(PHOTO), delivery(PHOTO), delivery(PHOTO, PHOTO)):  # 2 retries + a doubled batch
        raw, headers = signed(payload)
        assert c.post("/webhook", content=raw, headers=headers).status_code == 200
    assert len(calls) == 1 and [e["event"] for e in _events(world.store)].count("bill_received") == 1
    assert len(world.wa.sent) - sent_before == 2  # "padh raha hoon" + the summary, once


def test_two_deliveries_at_the_same_moment_are_processed_once(world):
    onboard(world)
    world.reader.next = {"supplier_name": "Shiv Medical Distributors", "total_amount": 4769}
    msg = dict(PHOTO, id="wamid.same-moment")

    async def both():
        await asyncio.gather(world.bot.handle(dict(msg)), world.bot.handle(dict(msg)))
    asyncio.run(both())
    assert [e["event"] for e in _events(world.store)].count("bill_received") == 1


@pytest.mark.parametrize("pg, supa, ok", [(False, False, False), (True, False, False), (True, True, True)])
def test_health_refuses_storage_that_render_would_wipe(monkeypatch, pg, supa, ok):
    from bot import photos as ph
    from bot import store as st

    class Store(st.PgStore if pg else st.MemoryStore):
        def __init__(self):
            pass

        def ping(self):
            return True

    class Bot:
        store = Store()
        photos = object.__new__(ph.SupabasePhotos) if supa else ph.LocalPhotos("unused")

    monkeypatch.setenv("REQUIRE_PERSISTENT_STORAGE", "1")
    r = TestClient(create_app(bot=Bot(), verify_token="v", app_secret=SECRET)).get("/health")
    assert (r.status_code == 200) == ok
    if not ok:
        assert r.json()["status"] == "down" and ("restart" in r.json()["storage"] or "wiped" in r.json()["storage"])
    monkeypatch.setenv("REQUIRE_PERSISTENT_STORAGE", "0")  # the laptop: memory store is fine
    assert TestClient(create_app(bot=Bot(), verify_token="v", app_secret=SECRET)).get("/health").status_code == 200
