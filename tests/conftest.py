"""Shared test helpers: a fake WhatsApp, a fake bill reader, message builders, and both stores.

The Postgres tests run only when TEST_DATABASE_URL points at a throwaway database
(they wipe it). On a laptop without that, they are skipped and the in-memory tests still run.
"""

import asyncio
import itertools
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bot.conversation import Bot  # noqa: E402
from bot.extract import Extraction  # noqa: E402
from bot.store import MemoryStore  # noqa: E402

_ids = itertools.count(1)
OWNER = "919876543210"


class FakeWA:
    def __init__(self):
        self.sent = []
        self.downloads = []

    async def send_text(self, to, body):
        self.sent.append({"kind": "text", "to": to, "body": body})
        return f"wamid.out{next(_ids)}"

    async def send_buttons(self, to, body, buttons, header=None, footer=None):
        assert 1 <= len(buttons) <= 3 and all(len(t) <= 20 for _, t in buttons)
        assert header is None or len(header) <= 60
        assert footer is None or len(footer) <= 60
        self.sent.append({"kind": "buttons", "to": to, "body": body, "buttons": buttons, "header": header,
                          "footer": footer})
        return f"wamid.out{next(_ids)}"

    async def send_list(self, to, body, button, rows):
        # WhatsApp's limits for interactive lists
        assert 1 <= len(rows) <= 10 and len(button) <= 20 and len(body) <= 1024
        assert all(len(t) <= 24 and len(d or "") <= 72 and len(i) <= 200 for i, t, d in rows), rows
        assert len({i for i, _, _ in rows}) == len(rows)
        self.sent.append({"kind": "list", "to": to, "body": body, "button": button, "rows": rows})
        return f"wamid.out{next(_ids)}"

    def list_ids(self):
        return [i for i, _, _ in self.last["rows"]]

    async def send_template(self, to, name, params, language="en"):
        self.sent.append({"kind": "template", "to": to, "name": name, "params": params})
        return f"wamid.out{next(_ids)}"

    async def download_media(self, media_id):
        self.downloads.append(media_id)
        return b"\xff\xd8fake-jpeg"

    @property
    def last(self):
        return self.sent[-1]

    def button_ids(self):
        return [b for b, _ in self.last["buttons"]]


class FakeReader:
    """Stands in for the vision model. Set .next to the fields the next photo 'contains'."""

    def __init__(self):
        self.next = None
        self.fail = False

    def __call__(self, image_bytes):
        if self.fail:
            raise RuntimeError("model down")
        fields = {"is_bill": True, "supplier_name": None, "bill_date": None, "invoice_number": None,
                  "total_amount": None, "due_date": None, "unsure_fields": []} | (self.next or {})
        return Extraction(fields=fields, raw_text="{}", model="fake", prompt_version="v2",
                          input_tokens=1500, output_tokens=80, latency_ms=900)


def text(body, sender=OWNER):
    return {"id": f"wamid.in{next(_ids)}", "from": sender, "type": "text", "text": {"body": body}}


def photo(sender=OWNER):
    return {"id": f"wamid.in{next(_ids)}", "from": sender, "type": "image", "image": {"id": f"media{next(_ids)}"}}


def tap(button_id, sender=OWNER):
    return {"id": f"wamid.in{next(_ids)}", "from": sender, "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": "x"}}}


def _pg_store():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg

    from bot.store import PgStore
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("drop schema if exists public cascade; create schema public;")
        c.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    return PgStore(url)


@pytest.fixture(autouse=True)
def no_paid_api_keys(monkeypatch):
    """bot.app runs load_dotenv() on import, which can put real keys in the environment: no test may ever make
    a paid call, so every test starts without them (tests that need a key set a fake one)."""
    for key in ("ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    return MemoryStore() if request.param == "memory" else _pg_store()


@pytest.fixture()
def world(store, tmp_path):
    """A pilot-open bot wired to fakes. world.run(msg) delivers one message."""
    wa, reader = FakeWA(), FakeReader()
    bot = Bot(store, wa, reader, pilot_open=True, image_dir=tmp_path / "bills")

    class World:
        pass

    w = World()
    w.store, w.wa, w.reader, w.bot = store, wa, reader, bot
    w.run = lambda msg: asyncio.run(bot.handle(msg))
    w.events = lambda: [e["event"] for e in _events(store)]
    return w


def _events(store):
    if isinstance(store, MemoryStore):
        return store.events
    return store._all("select event, props from events order by id")


def onboard(w, sender=OWNER):
    w.run(text("hi", sender))
    w.run(tap("consent_yes", sender))
