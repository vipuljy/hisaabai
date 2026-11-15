"""How many database round trips one message costs (each one crosses the network from the host to Supabase).

  TEST_DATABASE_URL=postgresql://... python scripts/query_count.py      (a THROWAWAY database: it is wiped)

Prints, per typical message, the number of queries and the store methods that made them. Used to check that new
guardrails (duplicates, before -> after, unusual amounts) did not make replies slower: see docs/18.
"""

from __future__ import annotations

import asyncio
import collections
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FakeReader, FakeWA, tap, text  # noqa: E402

from bot.conversation import Bot  # noqa: E402
from bot.store import PgStore  # noqa: E402


def main() -> int:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        print("Set TEST_DATABASE_URL to a throwaway Postgres database (it is wiped).")
        return 1
    store = PgStore(url)
    store._one("drop schema public cascade; create schema public;")
    store._run((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"), (), lambda c: None)
    calls, n = collections.Counter(), [0]
    run = store._run

    def counted(sql, params, fetch):
        n[0] += 1
        who = next((f.name for f in reversed(traceback.extract_stack()[:-1])
                    if f.filename.endswith("store.py") and f.name not in ("_run", "_one", "_all")), "?")
        calls[who] += 1
        return run(sql, params, fetch)
    store._run = counted
    wa = FakeWA()
    bot = Bot(store, wa, FakeReader(), pilot_open=True, image_dir=tempfile.mkdtemp())

    def go(msg, label):
        calls.clear()
        n[0] = 0
        asyncio.run(bot.handle(msg))
        print(f"{label:30} {n[0]:3} queries   {dict(calls.most_common(5))}")

    def ok(prefix):
        return tap(next(b for b in wa.button_ids() if b.startswith(prefix)))
    go(text("hi"), "hi (new number)")
    go(tap("consent_yes"), "consent tap")
    go(text("Sharma Traders ka bill 5000"), "typed bill -> card")
    go(ok("ok:"), "Sahi hai (bill)")
    go(text("Sharma ko 2000 diye"), "payment -> card")
    go(ok("pok:"), "Sahi hai (payment)")
    go(text("kitna baaki hai"), "kitna baaki hai")
    go(text("sharma ka hisaab"), "sharma ka hisaab")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
