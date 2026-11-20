"""20 Nov evening phone test: '30 nahi 300' on a card that is still open, the template spend cap, the WhatsApp cost
line. Runs on both stores."""

import asyncio
from datetime import date, datetime, timedelta, timezone

from conftest import OWNER, _events, onboard, photo, tap, text

from bot import health, jobs
from bot import messages as M


def test_amount_fix_changes_the_open_card(world):
    onboard(world)
    world.reader.next = {"supplier_name": "Gupta Agency", "total_amount": 5000}
    world.run(photo())
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("ok:"))))
    world.run(text("Gupta ne 30 ka maal wapas liya"))       # a slip: 300 was meant; the card is NOT tapped
    world.run(text("30 nahi 300"))
    body = world.wa.last["body"]
    assert "300" in body and "7 din" not in body              # the open card, now with 300
    assert "fix_open_card" in [e["event"] for e in _events(world.store)]
    ok = next(b for b in world.wa.button_ids() if b.split(":")[0] in ("ok", "pok"))
    world.run(tap(ok))
    shop = world.store.get_shop_by_wa(OWNER)
    due = {r["supplier"]: r["due"] for r in world.store.dues(shop["id"])}
    assert due["Gupta Agency"] == 4700


def test_fix_without_an_open_card_still_searches_saved_entries(world):
    onboard(world)
    world.run(text("30 nahi 300"))
    assert world.wa.last["body"] == M.fix_not_found(30)


class _WA:
    def __init__(self):
        self.sent = 0

    async def send_template(self, *a):
        self.sent += 1
        return f"wamid.t{self.sent}"


def test_template_cap_stops_sends(world):
    onboard(world)
    shop = world.store.get_shop_by_wa(OWNER)
    today = date(2026, 11, 22)                                # a Sunday
    for i in range(3):
        world.store.add_reminder(shop["id"], None, f"yaad {i}", today)
    budget = jobs.TemplateBudget(world.store, today, cap=2)
    wa = _WA()
    n = asyncio.run(jobs.send_owner_reminders(world.store, wa, shop, today, budget=budget))
    assert wa.sent == 2 and n == 2
    assert [e["event"] for e in _events(world.store)].count("template_cap_hit") == 1


def test_whatsapp_cost_line():
    now = datetime(2026, 11, 20, 13, 0, tzinfo=timezone.utc)
    win = [{"event": "summary_sent", "props": {}, "created_at": now, "shop_id": "1"}] * 3
    line = health.whatsapp_line(win, [{"wa_n": 2}, {"wa_n": 1}])
    assert line.startswith("- WhatsApp: 3 templates sent (about Rs 0") and "3 bot messages" in line
