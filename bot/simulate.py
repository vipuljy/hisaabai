"""Chat with the bot in this window, as a shop owner would on WhatsApp. No Meta account needed.

  python -m bot.simulate

Type messages as the owner would. Special commands:
  /photo C:\\path\\to\\bill.jpg   send a bill photo (real AI reading if ANTHROPIC_API_KEY is in .env)
  1, 2, 3                       tap the buttons shown under the last message
  /quit                         exit
Without an API key, a photo is "read" as a sample bill so you can still try the flow.
Data lives in memory only and disappears when you exit.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from bot.conversation import Bot
from bot.extract import Extraction, extract_bill
from bot.store import MemoryStore

ME = "910000000000"
_ids = itertools.count(1)


class PrintWA:
    def __init__(self):
        self.buttons = []
        self.photo = None

    def _show(self, body):
        print("\n  Vahi > " + body.replace("\n", "\n         "))

    async def send_text(self, to, body):
        self._show(body)
        self.buttons = []
        return f"sim{next(_ids)}"

    async def send_buttons(self, to, body, buttons, header=None, footer=None):
        self._show("\n".join(x for x in (f"*{header}*" if header else "", body, f"_{footer}_" if footer else "") if x))
        self.buttons = buttons
        print("              " + "   ".join(f"[{i}] {t}" for i, (_, t) in enumerate(buttons, start=1)))
        return f"sim{next(_ids)}"

    async def send_list(self, to, body, button, rows):
        self._show(body)
        self.buttons = [(rid, title) for rid, title, _ in rows]
        print(f"              [{button}]")
        for i, (_, title, desc) in enumerate(rows, start=1):
            print(f"              [{i}] {title}" + (f"  ({desc})" if desc else ""))
        return f"sim{next(_ids)}"

    async def download_media(self, media_id):
        return self.photo


def sample_reader(_image):
    fields = {"is_bill": True, "supplier_name": "Sharma Traders", "bill_date": "2026-10-05",
              "invoice_number": "INV-2231", "total_amount": 4580, "due_date": None, "unsure_fields": ["invoice_number"]}
    return Extraction(fields=fields, raw_text="sample", model="sample", prompt_version="-")


def main():
    load_dotenv()
    real = bool(os.getenv("ANTHROPIC_API_KEY"))
    reader = partial(extract_bill, model=os.getenv("BILL_MODEL", "claude-haiku-5-5"),
                     prompt_version=os.getenv("BILL_PROMPT", "v2")) if real else sample_reader
    wa = PrintWA()
    bot = Bot(MemoryStore(), wa, reader, pilot_open=True, image_dir=Path("data/raw/bills_simulator"))
    print(__doc__)
    print("Bill reading:", "REAL (Claude)" if real else "SAMPLE (no API key in .env)")
    print("Start by typing: hi")
    while True:
        try:
            line = input("\nYou > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line.startswith("/photo"):
            path = Path(line[len("/photo"):].strip().strip('"'))
            if real and not path.is_file():
                print("  (file not found)")
                continue
            wa.photo = path.read_bytes() if path.is_file() else b"sample"
            msg = {"id": f"in{next(_ids)}", "from": ME, "type": "image", "image": {"id": "sim-media"}}
        elif line.isdigit() and wa.buttons and 1 <= int(line) <= len(wa.buttons):
            bid = wa.buttons[int(line) - 1][0]
            msg = {"id": f"in{next(_ids)}", "from": ME, "type": "interactive",
                   "interactive": {"type": "button_reply", "button_reply": {"id": bid, "title": ""}}}
        else:
            msg = {"id": f"in{next(_ids)}", "from": ME, "type": "text", "text": {"body": line}}
        asyncio.run(bot.handle(msg))


if __name__ == "__main__":
    main()
