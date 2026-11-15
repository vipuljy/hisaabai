"""The web layer: Meta's handshake, signature check, and handing messages to the bot."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from bot.app import create_app

SECRET = "test-secret"


class RecordingBot:
    def __init__(self):
        self.got = []

    async def handle(self, msg):
        self.got.append(msg)


def client():
    bot = RecordingBot()
    return TestClient(create_app(bot=bot, verify_token="vt123", app_secret=SECRET)), bot


def signed(payload):
    raw = json.dumps(payload).encode()
    return raw, {"X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()}


PAYLOAD = {"entry": [{"changes": [{"value": {
    "messages": [{"id": "m1", "from": "919876543210", "type": "text", "text": {"body": "hi"}}],
    "statuses": [{"status": "delivered"}]}}]}]}


def test_health():
    c, _ = client()
    assert c.get("/").json()["status"] == "ok"


def test_meta_handshake():
    c, _ = client()
    ok = c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "vt123", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403


def test_signed_message_reaches_bot_and_statuses_are_ignored():
    c, bot = client()
    raw, headers = signed(PAYLOAD)
    assert c.post("/webhook", content=raw, headers=headers).status_code == 200
    assert [m["id"] for m in bot.got] == ["m1"]


def test_bad_signature_rejected():
    c, bot = client()
    raw, _ = signed(PAYLOAD)
    assert c.post("/webhook", content=raw, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    assert bot.got == []


def test_whatsapp_profile_name_is_attached_to_each_message():
    c, bot = client()
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "919876543210", "profile": {"name": "Ramesh Kirana"}}],
        "messages": [{"id": "m1", "from": "919876543210", "type": "text", "text": {"body": "hi"}},
                     {"id": "m2", "from": "919811111111", "type": "text", "text": {"body": "hi"}}]}}]}]}
    raw, headers = signed(payload)
    assert c.post("/webhook", content=raw, headers=headers).status_code == 200
    assert [(m["id"], m["_profile_name"]) for m in bot.got] == [("m1", "Ramesh Kirana"), ("m2", "")]
