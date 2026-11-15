"""Prints one full chat per kind of entry (fake shop, fake WhatsApp, no AI, no network), as the owner would see it.

  python scripts/entry_demo.py            (the output is pasted in docs/16_voice_typed_entries.md)

Taps are written as [tap: <button title>]; list picks as [pick: <row title>].
"""

from __future__ import annotations

import asyncio
import itertools
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.conversation import Bot  # noqa: E402
from bot.extract import Extraction  # noqa: E402
from bot.store import MemoryStore  # noqa: E402

OWNER = "910000000009"  # a fake number  # secret-scan: ignore
TODAY = date(2026, 10, 28)
_ids = itertools.count(1)


class PrintWA:
    def __init__(self):
        self.last = None

    async def _show(self, body, extra=""):
        print("  Bot:   " + body.replace("\n", "\n         ") + extra)
        return f"demo{next(_ids)}"

    async def send_text(self, to, body):
        self.last = None
        return await self._show(body)

    async def send_buttons(self, to, body, buttons, header=None, footer=None):
        self.last = buttons
        body = "\n".join(x for x in (f"*{header}*" if header else "", body, f"_{footer}_" if footer else "") if x)
        return await self._show(body, "\n         " + "  ".join(f"[{t}]" for _, t in buttons))

    async def send_list(self, to, body, button, rows):
        self.last = [(i, t) for i, t, _ in rows]
        return await self._show(body, "\n         " + "\n         ".join(f"- {t}" + (f" ({d})" if d else "")
                                                                         for _, t, d in rows))

    async def send_template(self, to, name, params, language="en"):
        return await self._show(f"(template {name}: {params})")

    async def download_media(self, media_id):
        return b"\xff\xd8 fake photo"


class Reader:
    next = {}

    def __call__(self, image):
        f = {"is_bill": True, "supplier_name": None, "bill_date": None, "invoice_number": None, "total_amount": None,
             "due_date": None, "unsure_fields": []} | self.next
        return Extraction(fields=f, raw_text="{}", model="fake", prompt_version="v2")


def world(new_shop=False):
    store, wa, reader = MemoryStore(), PrintWA(), Reader()
    if new_shop:   # a brand-new shop: no consent, no name, nothing saved
        store.create_shop(OWNER, shop_code="DEMO", status="invited")
        bot = Bot(store, wa, reader, image_dir=tempfile.mkdtemp(), today_fn=lambda: TODAY)

        async def heard(_audio):
            return bot._said
        bot.transcriber = heard
        return bot, wa, reader
    shop = store.create_shop(OWNER, shop_code="DEMO", status="active", shop_name="Ramesh Kirana")
    now = datetime.now(timezone.utc)
    store.update_shop(shop["id"], consent_at=now, onboarded_at=now)
    for name, total in (("Sharma Traders", 5000), ("Sharma Medical", 900), ("Gupta Agency", 3000)):
        sup = store.find_or_create_supplier(shop["id"], name)
        b = store.create_bill(shop["id"], {"supplier_name": name, "total_amount": total, "bill_date": "2026-10-26"},
                              {"model": "typed", "source": "typed"})
        store.confirm_bill(b["id"], sup["id"])
        store.bills[b["id"]]["confirmed_at"] = now - timedelta(days=2)
    old = store.create_bill(shop["id"], {"supplier_name": "Gupta Agency", "total_amount": 6000, "bill_date": "2026-10-10"},
                            {"model": "typed", "source": "typed"})     # an entry older than the 7-day edit window
    store.confirm_bill(old["id"], store.find_or_create_supplier(shop["id"], "Gupta Agency")["id"])
    store.bills[old["id"]]["created_at"] = store.bills[old["id"]]["confirmed_at"] = now - timedelta(days=20)
    tmp = tempfile.mkdtemp()
    bot = Bot(store, wa, reader, image_dir=tmp, today_fn=lambda: TODAY)

    async def heard_text(_audio):
        return bot._said
    bot.transcriber = heard_text
    return bot, wa, reader


def run(bot, msg):
    # WhatsApp sends the owner's profile name with every message (a fake one here)
    asyncio.run(bot.handle(msg | {"from": OWNER, "id": f"wamid.demo{next(_ids)}", "_profile_name": "Ramesh Kirana"}))


def say(bot, text):
    print(f"\n  Owner: {text}")
    run(bot, {"type": "text", "text": {"body": text}})


def voice(bot, transcript):
    print("\n  Owner: 🎙️ (voice note)")
    bot._said = transcript
    run(bot, {"type": "audio", "audio": {"id": "a"}})


def photo(bot, reader, fields):
    print("\n  Owner: 📷 (bill photo)")
    reader.next = fields
    run(bot, {"type": "image", "image": {"id": f"m{next(_ids)}"}})


def tap(bot, wa, starts):
    bid, title = next((b, t) for b, t in wa.last if b.startswith(starts) or t == starts)
    print(f"\n  Owner: [tap: {title}]" if ":" in bid or bid else "")
    kind = "list_reply" if bid.split(":")[0] in ("sp", "spn", "spt", "pf", "ef", "fsel", "q", "qs", "qp", "qd", "qr",
                                                 "adjc") \
        else "button_reply"
    run(bot, {"type": "interactive", "interactive": {"type": kind, kind: {"id": bid, "title": title}}})


def main():
    flows = [
        ("A new shop: the intro, an example, one tap, and the name asked only after the first entry", lambda b, w, r: (
            say(b, "hi"), tap(b, w, "consent_example"), tap(b, w, "consent_yes"), say(b, "Sharma Traders ka bill 4580"),
            tap(b, w, "ok:"), tap(b, w, "sn_yes"))),
        ("A voice bill with credit days, then a photo that is READ and offered as its proof", lambda b, w, r: (
            voice(b, "Gupta Agency ka bill aaya 12 hazaar ka, 15 din ka udhaar"), tap(b, w, "ok:"),
            photo(b, r, {"supplier_name": "Gupta Agency", "total_amount": 12000}), tap(b, w, "pfy:"))),
        ("A photo that is not a bill: read, nothing saved, no proof question", lambda b, w, r: (
            say(b, "Gupta Agency ka bill 900"), tap(b, w, "ok:"), photo(b, r, {"is_bill": False}))),
        ("Proof said in words after tapping 'Naya bill hai'", lambda b, w, r: (
            say(b, "Sharma Traders ka bill 4000"), tap(b, w, "ok:"),
            photo(b, r, {"supplier_name": "Sharma Traders", "total_amount": 4000}), tap(b, w, "pfn:"),
            say(b, "ye sharma ke bill ka proof hai"))),
        ("Hindi script and a spelling slip", lambda b, w, r: (
            say(b, "गुप्ता को 2000 दिए"), tap(b, w, "pok:"), say(b, "gupta ka hiasb"))),
        ("The same payment sent twice: the second card says so", lambda b, w, r: (
            say(b, "Gupta ko 1000 diye"), tap(b, w, "pok:"), say(b, "Gupta ko 1000 diye"), tap(b, w, "pcancel:"))),
        ("A typed payment to a name that fits two suppliers", lambda b, w, r: (
            say(b, "Sharma ko 2000 diye"), tap(b, w, "Sharma Traders"), tap(b, w, "pok:"))),
        ("A first payment to a supplier the bot does not know: dues from before?", lambda b, w, r: (
            say(b, "Mehta Steel ko 2000 diye"), tap(b, w, "old_yes:"), say(b, "12000"), tap(b, w, "pok:"))),
        ("A new supplier: created only on the tap", lambda b, w, r: (
            say(b, "Om Steel ka bill saat hazaar ka aaya"), tap(b, w, "ok:"))),
        ("Bought something, said the everyday way", lambda b, w, r: (
            say(b, "Gupta se 500 ka bartan liya"), tap(b, w, "ok:"))),
        ("Two entries in one message", lambda b, w, r: (
            say(b, "Gupta ko 1000 diye aur Sharma Traders ka bill 4580"),)),
        ("Money that came IN: what was it, and why", lambda b, w, r: (
            say(b, "Gupta se 500 aaye"), tap(b, w, "rcv_refund:"), tap(b, w, "rfw_g:"), tap(b, w, "pok:"))),
        ("Money back because of paying too much", lambda b, w, r: (
            say(b, "Sharma Traders ne galti se zyada liye the, 300 wapas kiye"), tap(b, w, "pok:"))),
        ("\"Ne\" can mean to or from: the bot asks the direction", lambda b, w, r: (
            say(b, "Sharma Traders ne 2000 diye"), tap(b, w, "rcv_pay:"), tap(b, w, "pok:"))),
        ("Money still to pay, more than the bot knows: added only on a tap", lambda b, w, r: (
            say(b, "Gupta ko 12000 dene hai"), tap(b, w, "fut_add:"), tap(b, w, "ok:"), say(b, "kitna baaki hai"))),
        ("Money still to pay, already inside the baaki: a reminder first", lambda b, w, r: (
            say(b, "Sharma Traders ko kal 3000 dena hai"), tap(b, w, "fut_rem:"))),
        ("Old dues with no bill, and goods sent back", lambda b, w, r: (
            say(b, "Sharma Traders ka 2000 pichla hisab jodo"), tap(b, w, "ok:"),
            say(b, "Gupta ko 800 ka maal wapas kiya"), tap(b, w, "ok:"))),
        ("No verb: the owner says what it is", lambda b, w, r: (
            say(b, "Gupta ko 900"), tap(b, w, "kind_pay:"), tap(b, w, "pok:"))),
        ("Fixing an amount saved by mistake", lambda b, w, r: (
            say(b, "Gupta ko 3200 diye"), tap(b, w, "pok:"), say(b, "3200 nahi 2300"), tap(b, w, "fix_ok:"))),
        ("Removing the last entry", lambda b, w, r: (
            say(b, "Gupta ko 700 diye"), tap(b, w, "pok:"), say(b, "last wala galat hai"), tap(b, w, "udel:"))),
        ("An entry older than 7 days: never rewritten, a dated correction instead", lambda b, w, r: (
            say(b, "Gupta ka 6000 wala bill 5000 tha"), tap(b, w, "adj:"), say(b, "gupta agency ka hisaab"))),
        ("STOP keeps the hisaab; DELETE removes everything after one confirm tap", lambda b, w, r: (
            say(b, "STOP"), say(b, "mera data delete karo"), tap(b, w, "delall_yes:"))),
    ]
    for title, flow in flows:
        print(f"\n### {title}\n```")
        bot, wa, reader = world(new_shop=title.startswith("A new shop"))
        flow(bot, wa, reader)
        print("```")


if __name__ == "__main__":
    main()
