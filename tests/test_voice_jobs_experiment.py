"""Voice notes, the daily nudges, the experiment maths and the admin tool."""

import asyncio
import itertools
from datetime import date, datetime, timedelta

import pytest
from conftest import OWNER, onboard, photo, tap, text

from bot import admin
from bot import messages as M
from bot.experiment import assign_groups, fisher_exact_two_sided, min_n_per_group, readout, wilson
from bot.jobs import IST, run_daily
from bot.store import MemoryStore

TODAY = datetime.now(IST).date()


_voice_ids = itertools.count(1)


def voice(sender=OWNER):
    # unique id per note: Meta never reuses ids, and the bot drops repeats as duplicates
    return {"id": f"wamid.voice{next(_voice_ids)}", "from": sender, "type": "audio", "audio": {"id": "aud1"}}


def confirmed_bill(w, **extra):
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580} | extra
    w.run(photo())
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith("ok:"))))


def heard(transcript):
    async def t(_audio):
        if isinstance(transcript, Exception):
            raise transcript
        return transcript
    return t


# ---------------------------------------------------------------- voice
def test_voice_off_without_key(world):
    onboard(world)
    world.run(voice())
    assert world.wa.last["body"] == M.VOICE_SOON


def test_voice_payment_with_digits_saved_as_voice(world):
    onboard(world)
    confirmed_bill(world)
    world.bot.transcriber = heard("Sharma ji ko 2000 diye")
    world.run(voice())
    assert world.wa.last["kind"] == "buttons" and "₹2,000" in world.wa.last["body"]
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("pok:"))))
    assert "₹2,580" in world.wa.last["body"]
    shop = world.store.get_shop_by_wa(OWNER)
    assert world.store.event_exists(shop["id"], "payment_logged", "source", "voice")


def test_voice_payment_in_words_read_by_rules_no_ai_call(world):
    onboard(world)
    confirmed_bill(world)
    world.bot.transcriber = heard("sharma ko paanch hazaar diye")
    calls = []
    world.bot.voice_parser = lambda t: calls.append(t) or (5000, "sharma")
    world.run(voice())
    assert calls == [] and "₹5,000" in world.wa.last["body"] and "Sharma Traders" in world.wa.last["body"]


def test_voice_model_fallback_only_when_rules_find_nothing(world):
    onboard(world)
    confirmed_bill(world)
    world.bot.transcriber = heard("शर्मा जी को पांच हज़ार दिए")  # Hindi script: turned into Roman letters, rules read it
    calls = []
    world.bot.voice_parser = lambda t: calls.append(t) or (5000, "sharma")
    world.run(voice())
    assert calls == [] and "₹5,000" in world.wa.last["body"] and "Sharma Traders" in world.wa.last["body"]
    world.bot.transcriber = heard("sharma ji ko half lakh diye")   # an amount the rules do not read: the model tries
    world.bot.voice_parser = lambda t: calls.append(t) or (50000, "sharma")
    world.run(voice())
    assert calls == ["sharma ji ko half lakh diye"]
    assert world.wa.last["kind"] == "buttons" and "₹50,000" in world.wa.last["body"]  # a card: the owner confirms


def test_voice_dues_unclear_and_failure(world):
    onboard(world)
    confirmed_bill(world)
    world.bot.transcriber = heard("kitna baaki hai bhai")
    world.run(voice())
    assert "Kul baaki: ₹4,580" in world.wa.last["body"]
    world.bot.transcriber = heard("aaj dukaan jaldi band hogi")
    world.bot.voice_parser = lambda t: None
    world.run(voice())
    assert world.wa.last["body"] == M.voice_not_understood("aaj dukaan jaldi band hogi")
    world.bot.transcriber = heard(RuntimeError("sarvam down"))
    world.run(voice())
    assert world.wa.last["body"] == M.VOICE_FAILED


# ---------------------------------------------------------------- daily nudges
def test_due_reminder_sent_once_and_not_to_control(world):
    # Pinned to a Wednesday (today or the next one): on a Sunday the weekly summary is also sent,
    # which made this test fail one day a week.
    day = TODAY + timedelta(days=(2 - TODAY.weekday()) % 7)
    assert day.weekday() == 2
    onboard(world)
    confirmed_bill(world, due_date=(day + timedelta(days=2)).isoformat())
    c1 = asyncio.run(run_daily(world.store, world.wa, day))
    sent = [m for m in world.wa.sent if m["kind"] == "template"]
    assert c1["reminders"] == 1 and sent[-1]["name"] == "due_reminder"
    assert sent[-1]["params"][0] == "Sharma Traders" and sent[-1]["params"][3] == "₹4,580"
    assert asyncio.run(run_daily(world.store, world.wa, day))["reminders"] == 0  # no repeat
    shop = world.store.get_shop_by_wa(OWNER)
    world.store.update_shop(shop["id"], experiment_group="control")
    c3 = asyncio.run(run_daily(world.store, world.wa, day))
    assert c3["skipped_control"] == 1


def test_no_reminder_when_already_paid(world):
    onboard(world)
    confirmed_bill(world, due_date=(TODAY + timedelta(days=2)).isoformat())
    world.run(text("Sharma ko 4580 diye"))
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("pok:"))))
    assert asyncio.run(run_daily(world.store, world.wa, TODAY))["reminders"] == 0


def test_sunday_summary(world):
    onboard(world)
    confirmed_bill(world)
    sunday = TODAY + timedelta(days=(6 - TODAY.weekday()) % 7)
    dry = asyncio.run(run_daily(world.store, world.wa, sunday, dry_run=True))
    assert dry["summaries"] == 1 and not any(m["kind"] == "template" for m in world.wa.sent)
    asyncio.run(run_daily(world.store, world.wa, sunday))
    p = world.wa.last["params"]
    assert world.wa.last["name"] == "weekly_summary"
    assert p[1:5] == ["1", "₹4,580", "₹0", "₹4,580"] and p[5] == "Sharma Traders (₹4,580)"


# ---------------------------------------------------------------- experiment maths
def test_assignment_balanced_and_reproducible():
    shops = [{"id": f"s{i}", "segment": seg} for i, seg in
             enumerate(["kirana"] * 3 + ["medical"] * 2 + ["hardware"] * 2)]
    g = assign_groups(shops)
    assert g == assign_groups(list(reversed(shops)))  # same seed, same answer
    t = sum(v == "treatment" for v in g.values())
    assert t in (3, 4)
    for seg in ("kirana", "medical", "hardware"):
        vals = [g[s["id"]] for s in shops if s["segment"] == seg]
        assert abs(vals.count("treatment") - vals.count("control")) <= 1


def test_fisher_matches_known_value():
    assert fisher_exact_two_sided(8, 2, 1, 5) == pytest.approx(0.03497, abs=1e-4)
    assert fisher_exact_two_sided(5, 5, 5, 5) == pytest.approx(1.0)


def test_readout_and_sample_size():
    rows = [("treatment", True)] * 6 + [("treatment", False)] * 4 + [("control", True)] * 4 + [("control", False)] * 6
    r = readout(rows)
    assert r["difference"] == pytest.approx(0.2) and r["fisher_p"] > 0.05
    lo, hi = r["difference_ci95"]
    assert lo < 0 < hi  # 10 shops per group cannot prove a 20-point gap
    assert min_n_per_group() == 97
    assert wilson(0, 10)[0] == 0.0
    with pytest.raises(ValueError):
        readout([("treatment", True)])


# ---------------------------------------------------------------- admin
def test_admin_flow(tmp_path):
    from bot.photos import LocalPhotos
    store, lines = MemoryStore(), []
    run = lambda *a: admin.main(list(a), store=store, out=lines.append, photos=LocalPhotos(tmp_path))  # noqa: E731
    assert run("add-shop", "s01", "kirana", "98765 43210") == 0
    assert run("add-shop", "S01", "kirana", "9876543211") == 1  # duplicate code
    assert run("add-shop", "S02", "bakery", "9876543212") == 1  # bad segment
    run("add-shop", "S02", "medical", "9876543212")
    assert store.get_shop_by_wa("919876543210")["shop_code"] == "S01"
    run("assign-groups")
    assert {s["experiment_group"] for s in store.list_shops()} == {"treatment", "control"}
    lines.clear()
    run("list-shops")
    assert all("98765" not in line for line in lines)  # never prints numbers
    run("delete-shop", "S01")
    assert [s["shop_code"] for s in store.list_shops()] == ["S02"]


def test_week_stats_counts_payments(store):
    shop = store.create_shop("911111111111", status="active")
    sup = store.find_or_create_supplier(shop["id"], "Gupta Agency")
    bill = store.create_bill(shop["id"], {"supplier_name": "Gupta Agency", "total_amount": 1000}, {})
    store.confirm_bill(bill["id"], sup["id"])
    store.add_payment(shop["id"], sup["id"], 400, "text")
    s = store.week_stats(shop["id"], TODAY)
    assert (s["bills"], int(s["billed"]), int(s["paid"]), int(s["total_due"])) == (1, 1000, 400, 600)
    assert store.delete_shop(shop["shop_code"]) and store.get_shop_by_wa("911111111111") is None
