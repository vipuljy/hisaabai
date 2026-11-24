"""Voice / typed entries end to end, on the in-memory store AND Postgres: cards, the supplier picker, kind / received /
future questions, fixes with history, proof photos, the AI fallback. Nothing may count before the owner's tap."""

import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from conftest import OWNER, FakeReader, FakeWA, _events, onboard, photo, tap, text

from bot import messages as M
from bot.conversation import Bot
from bot.jobs import purge_old_photos
from bot.photos import LocalPhotos

IST = timezone(timedelta(hours=5, minutes=30))


_picks = itertools.count(1)


def pick(row_id, sender=OWNER):
    # A counter, not the clock: on Windows two picks of the same row in one clock tick got the same message id, and
    # the bot rightly ignores a repeated id (WhatsApp redelivery), so the second pick was lost (laptop, 18 Nov).
    return {"id": f"wamid.pick{row_id[-12:]}.{next(_picks)}", "from": sender, "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": row_id, "title": "x"}}}


def btn(w, prefix):
    return next(b for b in w.wa.button_ids() if b.startswith(prefix))


def row(w, prefix):
    return next(i for i in w.wa.list_ids() if i.startswith(prefix))


def shop(w):
    return w.store.get_shop_by_wa(OWNER)


def dues(w):
    return {r["supplier"]: int(r["due"]) for r in w.store.dues(shop(w)["id"])}


def add(w, msg):
    """Type an entry and tap Sahi hai on its card (the supplier must be clear or new)."""
    w.run(text(msg))
    if w.wa.last["kind"] == "list":
        w.run(pick(row(w, "spn:")))
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith(("ok:", "pok:")))))


@pytest.fixture()
def ready(world):
    onboard(world)
    world.run(text("naam: Ramesh Kirana"))  # the shop's own name
    add(world, "Sharma Traders 5000")
    add(world, "Gupta Agency 3000")
    return world


# ---------------------------------------------------------------- cards: nothing counts before the tap
def test_typed_bill_with_credit_days(ready):
    w = ready
    w.run(text("Sharma Traders ka bill aaya 8000 ka, 15 din ka udhaar"))
    card = w.wa.last
    assert card["kind"] == "buttons" and "Naya bill" in card["body"] and "₹8,000" in card["body"]
    assert "15 din" in card["body"] and "due" in card["body"]
    assert dues(w)["Sharma Traders"] == 5000  # pending: not counted yet
    w.run(tap(btn(w, "ok:")))
    assert "₹13,000" in w.wa.last["body"] and M.PROOF_OFFER.strip() in w.wa.last["body"]
    bill = w.store.get_bill(btn_id_of(card, "ok:"))
    assert bill["source"] == "typed" and bill["credit_days"] == 15 and bill["due_date"] is not None


def btn_id_of(msg, prefix):
    return next(b for b, _ in msg["buttons"] if b.startswith(prefix)).partition(":")[2]


def test_pending_payment_counts_nowhere_until_confirmed_and_cancel_saves_nothing(ready):
    w = ready
    w.run(text("Sharma ko 2000 diye"))  # only one Sharma supplier in this shop
    card = w.wa.last
    assert "Naya payment" in card["body"]
    w.run(text("is mahine kitna payment kiya"))
    assert "koi payment nahi" in w.wa.last["body"]  # the pending one is not counted
    assert dues(w)["Sharma Traders"] == 5000
    w.run(tap(next(b for b, _ in card["buttons"] if b.startswith("pcancel:"))))
    assert w.wa.last["body"] == M.PAY_CANCELLED
    w.run(tap(next(b for b, _ in card["buttons"] if b.startswith("pok:"))))  # an old card tapped later
    assert "cancel ho chuki thi" in w.wa.last["body"] and dues(w)["Sharma Traders"] == 5000   # says what happened


def test_bill_cancel_and_double_tap(ready):
    w = ready
    w.run(text("Gupta Agency ka bill 700"))
    card = w.wa.last
    w.run(tap(btn(w, "bcancel:")))
    assert w.wa.last["body"] == M.BILL_CANCELLED
    w.run(tap(next(b for b, _ in card["buttons"] if b.startswith("ok:"))))
    assert "cancel ho chuki thi" in w.wa.last["body"] and dues(w)["Gupta Agency"] == 3000


# ---------------------------------------------------------------- the supplier step
def test_new_supplier_only_on_tap(ready):
    w = ready
    before = len(w.store.list_suppliers(shop(w)["id"]))
    w.run(text("Ramesh Steel ko 4000 diye"))
    assert "pehle ka koi baaki" in w.wa.last["body"]     # a first payment to a supplier the bot does not know
    w.run(tap(btn(w, "old_no:")))
    assert M.NEW_SUPPLIER_TAG in w.wa.last["body"] and len(w.store.list_suppliers(shop(w)["id"])) == before
    w.run(tap(btn(w, "pok:")))
    assert len(w.store.list_suppliers(shop(w)["id"])) == before + 1 and dues(w)["Ramesh Steel"] == -4000
    assert "supplier_created" in w.events()


def test_two_matches_and_look_alikes_get_a_list(ready):
    w = ready
    add(w, "Sharma Medical 900")       # a second Sharma
    w.run(text("Sharma ko 1000 diye"))
    lst = w.wa.last
    assert lst["kind"] == "list" and {"Sharma Medical", "Sharma Traders"} <= {t for _, t, _ in lst["rows"]}
    w.run(pick(next(i for i, t, _ in lst["rows"] if t == "Sharma Medical")))
    assert "Sharma Medical" in w.wa.last["body"]
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Sharma Medical"] == -100 and dues(w)["Sharma Traders"] == 5000
    w.run(text("Sharma Brothers 600"))  # looks like Sharma Traders / Medical, but is not either
    assert w.wa.last["kind"] == "list" and any(i.startswith("spn:") for i in w.wa.list_ids())
    w.run(pick(row(w, "spt:")))         # "Koi aur (likhunga)"
    w.run(text("Gupta Agency"))         # typed: a clear match -> the card
    assert "Gupta Agency" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Gupta Agency"] == 3600


def test_photo_bill_never_creates_a_supplier_silently(ready):
    w = ready
    w.reader.next = {"supplier_name": "Sharma Trading Co", "total_amount": 1200}
    w.run(photo())   # ₹1,200 is far from the ₹5,000 typed minutes ago: a new bill, never the proof question
    assert w.wa.last["kind"] == "list"   # looks like Sharma Traders: the owner picks
    w.run(pick(row(w, "sp:")))
    assert "Sharma Traders" in w.wa.last["body"] and "bill pe" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Sharma Traders"] == 6200
    w.reader.next = {"supplier_name": "Navrang Enterprises", "total_amount": 500}
    w.run(photo())  # the proof question is asked once per typed bill, not again
    assert M.NEW_SUPPLIER_TAG in w.wa.last["body"] and btn(w, "bcancel:")


# ---------------------------------------------------------------- kind / received / future
def test_kind_received_future(ready):
    w = ready
    w.run(text("Gupta ko 900"))
    choice = w.wa.last
    w.run(tap(btn(w, "kind_pay:")))
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Gupta Agency"] == 2100
    w.run(tap(next(b for b, _ in choice["buttons"] if b.startswith("kind_pay:"))))  # works once only
    assert w.wa.last["body"] == M.ENTRY_GONE
    w.run(text("Sharma se 500 aaye"))                 # goods or money? asked
    w.run(tap(btn(w, "rcv_no:")))
    assert w.wa.last["body"] == M.RECEIVED_NO and dues(w)["Sharma Traders"] == 5000
    w.run(text("Sharma se 500 aaye"))
    w.run(tap(btn(w, "rcv_bill:")))                   # "Maal aaya": a bill card
    assert "Naya bill" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Sharma Traders"] == 5500
    w.run(text("Sharma se 500 aaye"))
    w.run(tap(btn(w, "rcv_refund:")))                 # "Paisa wapas mila": why? (goods back, or paid too much)
    assert "Kyun?" in w.wa.last["body"]
    w.run(tap(btn(w, "rfw_o:")))                      # paid too much earlier: a refund card, the baaki goes UP
    assert "Supplier se paisa wapas mila" in w.wa.last["body"] and "₹5,500 → *₹6,000*" in w.wa.last["body"]
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Sharma Traders"] == 6000 and "paisa wapas mila" in w.wa.last["body"]
    w.run(text("Sharma ne 1000 diya"))               # to or from Sharma? asked
    w.run(tap(btn(w, "rcv_pay:")))
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Sharma Traders"] == 5000
    w.run(text("Gupta ko kal 1000 dena hai"))
    assert w.wa.last["kind"] == "buttons" and dues(w)["Gupta Agency"] == 2100  # not paid: nothing saved
    assert "₹2,100" in w.wa.last["body"]              # what the bot already knows about Gupta is shown
    w.run(tap(btn(w, "fut_rem:")))
    assert w.wa.last["body"].startswith("✅") and "₹1,000 dena" in w.wa.last["body"]
    assert len(w.store.list_reminders(shop(w)["id"], datetime.now(IST).date())) == 1


def test_old_dues_and_goods_sent_back(ready):
    w = ready
    w.run(text("Mehta Traders ko 3000 dena hai"))     # dues the bot did not know: added on a tap
    w.run(tap(btn(w, "fut_add:")))
    assert "Purana baaki jodna" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Mehta Traders"] == 3000 and M.PROOF_OFFER.strip() not in w.wa.last["body"]
    w.run(text("Gupta ko 500 ka maal wapas kiya"))    # goods sent back: the baaki goes DOWN
    assert "Maal wapas" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Gupta Agency"] == 2500
    w.run(text("is hafte kitne bill aaye"))           # udhaar and returns are not bills
    assert "2 bill" in w.wa.last["body"]
    w.run(text("aaj kitna payment kiya"))
    assert "koi payment nahi" in w.wa.last["body"]
    w.run(text("last wala galat hai"))                # fixes know the new kinds
    assert "Maal wapas" in w.wa.last["body"]
    w.run(tap(btn(w, "udel:")))
    assert dues(w)["Gupta Agency"] == 3000


# ---------------------------------------------------------------- fixes: last 7 days, with history
def test_fix_amount_and_undo_with_history(ready):
    w = ready
    add(w, "Gupta ko 2800 diye")
    w.run(text("3000 nahi 2500"))       # two entries of 3000? here only Gupta's bill: one card
    assert "₹3,000 → *₹2,500*" in w.wa.last["body"]
    w.run(tap(btn(w, "fix_no")))
    w.run(text("2800 nahi 3000"))
    assert "₹2,800 → *₹3,000*" in w.wa.last["body"]
    w.run(tap(btn(w, "fix_ok:")))
    assert dues(w)["Gupta Agency"] == 0 and "✅ Badal diya" in w.wa.last["body"]
    edits = w.store.entry_edits(shop(w)["id"])
    assert (edits[-1]["action"], edits[-1]["field"], edits[-1]["old_value"], edits[-1]["new_value"],
            edits[-1]["via"]) == ("edit", "amount", "2800.00", "3000.00", "typed")
    w.run(text("Gupta wala payment hata do"))
    assert "Is entry ka kya karna hai" in w.wa.last["body"]
    w.run(tap(btn(w, "udel:")))
    assert dues(w)["Gupta Agency"] == 3000 and w.store.entry_edits(shop(w)["id"])[-1]["action"] == "delete"
    w.run(text("aaj kitna payment kiya"))
    assert "koi payment nahi" in w.wa.last["body"]  # a removed payment is not an answer any more
    w.run(text("last wala galat hai"))           # the newest entry now: Gupta's 3000 bill
    w.run(tap(btn(w, "ued:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":amount"))))
    w.run(text("tees sau rupay"))                # 'tees sau' = 3000: the same amount, nothing to change
    assert w.wa.last["body"] == M.KEPT
    w.run(text("last wala galat hai"))
    w.run(tap(btn(w, "ued:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":amount"))))
    w.run(text("do hazaar"))
    assert "₹3,000 → *₹2,000*" in w.wa.last["body"]
    w.run(tap(btn(w, "fix_no")))
    assert w.wa.last["body"] == M.KEPT and dues(w)["Gupta Agency"] == 3000


def test_old_entries_cannot_be_fixed_by_message(store, tmp_path):
    wa, reader = FakeWA(), FakeReader()
    day = {"today": datetime.now(IST).date()}
    bot = Bot(store, wa, reader, pilot_open=True, image_dir=tmp_path / "b", today_fn=lambda: day["today"])

    class W:
        pass
    w = W()
    w.store, w.wa, w.reader, w.bot = store, wa, reader, bot
    w.run = lambda msg: asyncio.run(bot.handle(msg))
    onboard(w)
    add(w, "Gupta Agency 3000")
    day["today"] += timedelta(days=9)
    sid = shop(w)["id"]
    if hasattr(w.store, "bills"):             # saved 9 days ago
        for e in w.store.bills.values():
            e["created_at"] = e["confirmed_at"] = e["created_at"] - timedelta(days=9)
    else:
        w.store._one("update bills set created_at = created_at - interval '9 days', "
                     "confirmed_at = confirmed_at - interval '9 days' where shop_id = %s", (sid,))
    w.run(text("Gupta ka bill hatao"))        # never rewritten: a dated correcting entry is offered instead
    assert "Purani entry ka sudhaar" in w.wa.last["body"] and "₹3,000 → *₹0*" in w.wa.last["body"]
    adj_btn = btn(w, "adj:")
    w.run(tap(adj_btn))
    assert "Sudhaar entry jud gayi" in w.wa.last["body"] and dues(w).get("Gupta Agency", 0) == 0
    rows = w.store.recent_entries(sid, datetime(2000, 1, 1, tzinfo=IST))
    assert [r["kind"] for r in rows if int(r["amount"]) == 3000 and not r["reason"]] == ["bill"]   # untouched
    adj = [r for r in rows if (r.get("reason") or "").startswith("sudhaar")]
    assert len(adj) == 1 and adj[0]["kind"] == "return"   # the correction is its own dated entry, with the reason
    w.run(tap(adj_btn))                       # a second tap: no second correcting entry
    assert w.wa.last["body"] == M.ENTRY_GONE
    assert len([r for r in w.store.recent_entries(sid, datetime(2000, 1, 1, tzinfo=IST)) if r.get("reason")]) == 1
    w.run(text("Gupta ka 3000 wala bill 3500 tha"))
    assert "₹3,000 → *₹3,500*" in w.wa.last["body"] and "₹0 → *₹500*" in w.wa.last["body"]
    w.run(tap(btn(w, "adj:")))
    w.run(text("gupta ka hisaab"))            # the correction is its own line, never shown as goods returned
    body = w.wa.last["body"]
    assert "🔧 Sudhaar: −₹2,500" in body and "Maal wapas" not in body and "*₹500* baaki" in body


# ---------------------------------------------------------------- multi, too many, editing a card
def test_multi_and_too_many(ready):
    w = ready
    w.run(text("Sharma Traders ko 2000 aur Gupta Agency ko 1500 diye"))
    cards = [m for m in w.wa.sent[-3:] if m["kind"] == "buttons"]
    assert w.wa.sent[-3]["body"] == M.entries_intro(2) and len(cards) == 2
    for c in cards:
        w.run(tap(next(b for b, _ in c["buttons"] if b.startswith("pok:"))))
    assert dues(w) == {"Sharma Traders": 3000, "Gupta Agency": 1500}
    w.run(text("Sharma ko 1 diye, Gupta ko 2 diye, Sharma ko 3 diye aur Gupta ko 4 diye"))
    assert w.wa.last["body"] == M.TOO_MANY


def test_edit_a_card_before_saving(ready):
    w = ready
    w.run(text("Gupta Agency ka bill 4000"))
    w.run(tap(btn(w, "edit:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":credit_days"))))
    w.run(text("20"))
    assert "20 din" in w.wa.last["body"]
    w.run(tap(btn(w, "edit:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":total_amount"))))
    w.run(text("saade chaar hazaar"))
    assert "₹4,500" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Gupta Agency"] == 7500


# ---------------------------------------------------------------- proof photos
def test_proof_photo_match_mismatch_and_new_bill(ready, tmp_path):
    w = ready
    w.bot.photos = LocalPhotos(tmp_path / "photos")
    add(w, "Gupta Agency ka bill 4580")
    w.reader.next = {"supplier_name": "Gupta Agency", "total_amount": 4580}
    w.run(photo())
    assert "ka *proof* lagti hai" in w.wa.last["body"] and "Photo mein bhi *₹4,580*" in w.wa.last["body"]
    w.run(tap(btn(w, "pfy:")))
    assert w.wa.last["body"] == M.proof_ok(4580) and dues(w)["Gupta Agency"] == 7580   # proof, not a second bill
    add(w, "Sharma Traders ka bill 1000")
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 1100}
    w.run(photo())
    w.run(tap(btn(w, "pfy:")))
    assert "*₹1,000*" in w.wa.last["body"] and "*₹1,100*" in w.wa.last["body"]
    w.run(tap(btn(w, "pfix:")))
    assert dues(w)["Sharma Traders"] == 6100
    assert w.store.entry_edits(shop(w)["id"])[-1]["via"] == "proof"
    add(w, "Gupta Agency ka bill 200")
    w.reader.next = {"supplier_name": "Balaji Enterprises", "total_amount": 999}
    w.run(photo())                   # another supplier's bill: never the proof question, straight to its card
    assert "Balaji Enterprises" in w.wa.last["body"] and "proof" not in w.wa.last["body"]
    assert w.events().count("proof_asked") == 2         # asked only for the two photos that matched a typed bill
    w.reader.next = {"supplier_name": "Gupta Agency", "total_amount": 200}
    w.run(photo())                   # matches the typed ₹200 bill: asked; 'Naya bill hai' -> its own card
    w.run(tap(btn(w, "pfn:")))
    assert "proof_declined" in w.events() and btn(w, "ok:")
    # proof photos are deleted after 30 days like bill photos
    later = datetime.now(timezone.utc) + timedelta(days=31)
    counts = purge_old_photos(w.store, w.bot.photos, now=later)
    assert counts["photos_deleted"] >= 2


# ---------------------------------------------------------------- voice: rules first, then the checked AI fallback
def test_voice_bill_and_ai_fallback(ready):
    w = ready
    w.bot.transcriber = _heard("Sharma Traders ka bill aaya dedh hazaar ka")
    w.run(_voice())
    assert w.wa.sent[-2]["body"].startswith("🎙️ Maine suna") and "(voice)" in w.wa.last["body"]
    assert "₹1,500" in w.wa.last["body"]
    calls = []
    w.bot.entry_parser = lambda t, names, d: calls.append(t) or {
        "entries": [{"type": "payment", "supplier": "Gupta", "amount": 2200}]}
    w.bot.transcriber = _heard("gupta ji ko kuch rupaye bheje the")
    w.run(_voice())
    assert calls and "₹2,200 ⚠️" in w.wa.last["body"]  # an amount nobody said: flagged on the card
    shop_id = shop(w)["id"]
    assert sum(1 for e in _events(w.store) if e["event"] == "ai_call" and e["props"].get("kind") == "voice_text") == 1
    calls.clear()
    w.bot.transcriber = _heard("kitna baaki hai kya")
    w.run(_voice())
    assert calls == []  # a question: never sent to the classifier
    assert shop_id


def _heard(said):
    async def t(_audio):
        return said
    return t


_n = iter(range(10_000))


def _voice():
    return {"id": f"wamid.v{next(_n)}", "from": OWNER, "type": "audio", "audio": {"id": "a"}}


# ---------------------------------------------------------------- safety
def test_buttons_of_another_shop_do_nothing(ready):
    w = ready
    w.run(text("Gupta Agency ka bill 100"))
    stolen = btn(w, "ok:")
    other = "919000000001"
    w.run(text("hi", other))
    w.run(tap("consent_yes", other))
    w.run(tap(stolen, other))
    assert w.wa.last["body"] == M.ENTRY_GONE      # nothing about the other shop's entry
    w.run(tap("pok:not-a-uuid", other))
    assert w.wa.last["body"] == M.ENTRY_GONE
    w.run(tap("pay_yes", other))  # an old-style button with no state
    assert w.wa.last["body"] == M.MENU


# ---------------------------------------------------------------- review findings (31 Oct)
def test_proof_buttons_work_once(ready, tmp_path):
    w = ready
    w.bot.photos = LocalPhotos(tmp_path / "photos")
    add(w, "Sharma Traders ka bill aaya 8000 ka")
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 8000}
    w.run(photo())
    ask = w.wa.last
    yes = next(b for b, _ in ask["buttons"] if b.startswith("pfy:"))
    no = next(b for b, _ in ask["buttons"] if b.startswith("pfn:"))
    w.run(tap(yes))
    w.run(tap(no))   # the same question answered again: must NOT turn the photo into a second bill
    assert "cancel ho chuki thi" in w.wa.last["body"]
    w.run(tap(yes))
    assert "cancel ho chuki thi" in w.wa.last["body"]
    assert dues(w)["Sharma Traders"] == 13000  # counted once
    bill_id = yes.split(":")[1]
    assert w.store.get_bill(bill_id)["proof_image_path"] not in (None, "pending")  # the proof is attached


def test_a_new_message_leaves_a_waiting_question(ready):
    w = ready
    add(w, "Gupta ko 1000 diye")
    w.run(text("last wala galat hai"))
    w.run(tap(btn(w, "ued:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":amount"))))
    w.run(text("Sharma ko 2000 diye"))  # not a value for Gupta's amount: a new payment
    assert "Naya payment" in w.wa.last["body"] and "Sharma Traders" in w.wa.last["body"]
    w.run(text("Mehta Traders ka bill 500"))  # a new supplier -> the card
    w.run(tap(btn(w, "edit:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":supplier_name"))))
    w.run(text("menu"))  # never becomes a supplier called "menu"
    assert w.wa.last["kind"] == "list" and "menu" not in [s["name"].lower() for s in w.store.list_suppliers(shop(w)["id"])]
    before = dues(w)
    w.run(text("cancel"))  # nothing waiting any more: at most a card asking what to do with the last entry
    assert dues(w) == before


def test_bill_without_a_name_needs_one_pick_only(ready):
    w = ready
    w.run(text("5000 ka bill aaya"))
    assert w.wa.last["kind"] == "list"
    w.run(pick(next(i for i, t, _ in w.wa.last["rows"] if t == "Sharma Traders")))
    assert "Sharma Traders" in w.wa.last["body"] and w.wa.last["kind"] == "buttons"
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Sharma Traders"] == 10000 and "Save ho gaya" in w.wa.last["body"]


def test_a_piece_that_records_nothing_is_named(ready):
    w = ready
    w.run(text("Gupta ko 2000 diye, 1500 ka kuch aur tha kya?"))
    assert any("samajh nahi aaya" in m["body"] for m in w.wa.sent[-3:]) or "Naya payment" in w.wa.last["body"]


def test_a_valid_answer_is_not_taken_as_a_new_message(ready):
    w = ready
    w.run(text("Gupta Agency ka bill 4000"))
    w.run(tap(btn(w, "edit:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":invoice_number"))))
    w.run(text("Bill no 4471"))
    assert "*Bill no:* 4471" in w.wa.last["body"]
    w.run(tap(btn(w, "edit:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":supplier_name"))))
    w.run(text("Shree 786 Kirana"))
    assert "Shree 786 Kirana" in w.wa.last["body"] and "₹4,000" in w.wa.last["body"]


def test_proof_yes_at_the_daily_limit_can_be_tapped_again(ready, tmp_path):
    w = ready
    w.bot.photos = LocalPhotos(tmp_path / "photos")
    add(w, "Sharma Traders ka bill aaya 8000 ka")
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 8000}
    limit = w.bot.limits.per_shop_per_day
    w.bot.limits.per_shop_per_day = 0
    w.run(photo())                       # the photo is read first, so the daily limit applies before reading
    assert w.wa.last["body"] == M.SHOP_DAILY_LIMIT
    w.bot.limits.per_shop_per_day = limit
    w.run(photo())                       # sent again later: read, then offered as proof
    w.run(tap(btn(w, "pfy:")))
    assert w.wa.last["body"] == M.proof_ok(8000)


def test_paying_more_than_owed_shows_advance_not_a_minus():
    assert M.due_line("Gupta Agency", -200) == "*Gupta Agency* ko *₹200* zyada diye hain (advance)"
    assert M.due_line("Gupta Agency", 700) == "*Gupta Agency* ka baaki ab: *₹700*"
    assert "-₹" not in M.pay_saved("Gupta Agency", -200) and "advance" in M.pay_saved("Gupta Agency", -200)


def test_two_shops_with_the_same_supplier_name_keep_separate_ledgers(world):
    """Shop A and shop B both buy from 'Sharma Traders': two supplier rows, two baakis, answers never mix."""
    a, b = OWNER, "919000000002"
    for who in (a, b):
        onboard(world, who)
        world.run(text("naam: Test", who))
    for who, line in ((a, "Sharma Traders ka bill 5000"), (b, "Sharma Traders ka bill 9000"),
                      (b, "Sharma Traders ko 4000 diye"), (a, "Gupta Agency ka bill 3000")):
        world.run(text(line, who))
        world.run(tap(btn(world, "ok:") if "bill" in line else btn(world, "pok:"), who))
    shop_a, shop_b = world.store.get_shop_by_wa(a), world.store.get_shop_by_wa(b)
    sa = {s["name"]: s["id"] for s in world.store.list_suppliers(shop_a["id"])}
    sb = {s["name"]: s["id"] for s in world.store.list_suppliers(shop_b["id"])}
    assert set(sa) == {"Sharma Traders", "Gupta Agency"} and set(sb) == {"Sharma Traders"}
    assert sa["Sharma Traders"] != sb["Sharma Traders"]               # same name, two separate rows
    world.run(text("kitna baaki hai", a))
    assert "Sharma Traders: ₹5,000" in world.wa.last["body"] and "Gupta Agency: ₹3,000" in world.wa.last["body"]
    world.run(text("kitna baaki hai", b))
    assert "Sharma Traders: ₹5,000" in world.wa.last["body"] and "Gupta" not in world.wa.last["body"]
    world.run(text("aaj kitna payment diya", a))
    assert "4,000" not in world.wa.last["body"]                        # B's payment never shows in A's answers
    world.run(text("Gupta ko 100 diye", b))                            # B has no Gupta: a NEW supplier for B
    world.run(tap(btn(world, "old_no:"), b))
    assert "naya supplier" in world.wa.last["body"]
